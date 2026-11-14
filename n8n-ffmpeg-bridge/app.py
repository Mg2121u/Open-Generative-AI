import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ffmpeg-bridge")
TOKEN = os.environ.get("MERGE_API_TOKEN", "")
if len(TOKEN) < 24:
    raise RuntimeError("MERGE_API_TOKEN must be at least 24 characters")

ROOT = Path("/tmp/ffmpeg-bridge")
ROOT.mkdir(parents=True, exist_ok=True)
app = FastAPI(docs_url=None, redoc_url=None)
pool = ThreadPoolExecutor(max_workers=1)
lock = threading.RLock()
jobs = {}
TTL = 3600
MAX_MB = 150

class VideoMerge(BaseModel):
    video_urls: list[str] = Field(min_length=2, max_length=12)
    resolution_aspect_ratio_video_index: int = 0

class AudioMerge(BaseModel):
    video_url: str
    audio_url: str
    start_offset: float = 0

def auth(value):
    if not value:
        raise HTTPException(401, "Bearer token required")
    scheme, _, supplied = value.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(403, "Invalid merge service token")

def validate_url(url):
    parts = urlsplit(url)
    if (not isinstance(url, str) or len(url) > 4096 or parts.scheme != "https"
        or not parts.hostname or parts.username or parts.password
        or parts.port not in (None, 443)):
        raise ValueError("Only public HTTPS media URLs are accepted")
    try:
        answers = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(r[4][0]).is_global for r in answers):
            raise ValueError("Private/internal media URL rejected")
    except socket.gaierror as exc:
        raise ValueError("Media URL hostname cannot be resolved") from exc

def fetch(url, dest, budget):
    session = requests.Session()
    session.trust_env = False
    for _ in range(4):
        validate_url(url)
        with session.get(url, stream=True, allow_redirects=False, timeout=(10, 35)) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, r.headers.get("Location", ""))
                continue
            r.raise_for_status()
            size = 0
            with dest.open("wb") as out:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    budget[0] += len(chunk)
                    if size > MAX_MB * 1048576 or budget[0] > 400 * 1048576:
                        raise ValueError("Media input too large")
                    out.write(chunk)
            if size < 128:
                raise ValueError("Media download empty")
            return
    raise ValueError("Media URL redirected too many times")

def shell(args, timeout=300):
    process = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError("FFmpeg failed: " + process.stderr[-900:].replace("\n", " "))
    return process.stdout

def metadata(file):
    info = json.loads(shell(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(file)], 40))
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("Input has no video stream")
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0 or duration > 300:
        raise ValueError("Video duration missing or exceeds 300 seconds")
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio": any(s.get("codec_type") == "audio" for s in streams)
    }

def target_size(w, h):
    if w < 1 or h < 1:
        raise ValueError("Invalid video dimensions")
    factor = min(1.0, (921600 / (w * h)) ** 0.5, 1280 / w, 1280 / h)
    return max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)

def merge_clips(inputs, result, folder):
    info = [metadata(file) for file in inputs]
    duration = sum(m["duration"] for m in info)
    if duration > 300:
        raise ValueError("Total video must be at most 300 seconds")
    w, h = target_size(info[0]["width"], info[0]["height"])
    normalized = []
    for i, (src, meta) in enumerate(zip(inputs, info)):
        output = folder / f"part_{i:02}.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-y", "-i", str(src)]
        if not meta["audio"]:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_input = "0:a:0" if meta["audio"] else "1:a:0"
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p")
        cmd += ["-map", "0:v:0", "-map", audio_input, "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                "-t", str(meta["duration"]), "-movflags", "+faststart", str(output)]
        shell(cmd)
        normalized.append(output)
    playlist = folder / "playlist.txt"
    playlist.write_text("".join(f"file '{p}'\n" for p in normalized))
    shell(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-f", "concat", "-safe", "0", "-i", str(playlist),
           "-c", "copy", "-movflags", "+faststart", str(result)], 120)
    return duration

def add_audio(video, source, result, offset):
    meta = metadata(video)
    audio_info = json.loads(shell(["ffprobe", "-v", "error", "-show_streams",
                                    "-of", "json", str(source)], 40))
    if not any(s.get("codec_type") == "audio" for s in audio_info.get("streams", [])):
        raise ValueError("Original source contains no audio")
    if offset < 0 or offset > meta["duration"]:
        raise ValueError("Invalid start_offset")
    filter_audio = f"adelay={int(offset*1000)}:all=1,apad" if offset else "apad"
    shell(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(video), "-i", str(source), "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
           "-ac", "2", "-af", filter_audio, "-t", str(meta["duration"]),
           "-movflags", "+faststart", str(result)], 180)
    return meta["duration"]

def process(job_id, kind, params):
    folder = ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock:
        jobs[job_id]["status"] = "IN_PROGRESS"
    try:
        budget = [0]
        output = folder / "result.mp4"
        if kind == "video":
            inputs = []
            for i, url in enumerate(params["video_urls"]):
                file = folder / f"input_{i:02}.mp4"
                fetch(url, file, budget)
                inputs.append(file)
            duration = merge_clips(inputs, output, folder)
        else:
            video = folder / "source_video.mp4"
            audio = folder / "source_audio.mp4"
            fetch(params["video_url"], video, budget)
            fetch(params["audio_url"], audio, budget)
            duration = add_audio(video, audio, output, params["start_offset"])
        for temporary in folder.iterdir():
            if temporary != output and temporary.is_file():
                temporary.unlink(missing_ok=True)
        with lock:
            jobs[job_id].update(status="COMPLETED", duration=duration)
        logger.info("Completed merge job %s", job_id)
    except Exception as exc:
        logger.exception("Merge failed %s", job_id)
        with lock:
            jobs[job_id].update(status="FAILED", error=str(exc)[:240])

def origin(req):
    host = req.headers.get("host", "")
    if not host or "/" in host or "\\" in host or "@" in host:
        raise HTTPException(400, "Invalid host")
    return "https://" + host

def start(req, kind, params):
    now = time.time()
    with lock:
        for key, entry in list(jobs.items()):
            if entry["created"] < now - TTL:
                jobs.pop(key, None)
                shutil.rmtree(ROOT / key, ignore_errors=True)
        if sum(j["status"] in ("IN_QUEUE", "IN_PROGRESS") for j in jobs.values()) >= 3:
            raise HTTPException(429, "Too many merge requests queued")
        job_id = secrets.token_hex(16)
        base = origin(req) + "/jobs/" + job_id
        record = {
            "created": now,
            "status": "IN_QUEUE",
            "status_url": base + "/status",
            "response_url": base
        }
        jobs[job_id] = record
    pool.submit(process, job_id, kind, params)
    return {"request_id": job_id,
            "status": record["status"],
            "status_url": record["status_url"],
            "response_url": record["response_url"]}

@app.get("/health")
def health():
    return {"ok": True, "service": "n8n-ffmpeg-bridge"}

@app.post("/merge-videos")
def merge_videos(data: VideoMerge, req: Request, authorization: str | None = Header(None)):
    auth(authorization)
    if not 0 <= data.resolution_aspect_ratio_video_index < len(data.video_urls):
        raise HTTPException(422, "Invalid aspect ratio index")
    try:
        for url in data.video_urls:
            validate_url(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return start(req, "video", data.model_dump())

@app.post("/merge-audio-video")
def merge_audio(data: AudioMerge, req: Request, authorization: str | None = Header(None)):
    auth(authorization)
    try:
        validate_url(data.video_url)
        validate_url(data.audio_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return start(req, "audio", data.model_dump())

@app.get("/jobs/{job_id}/status")
def job_status(job_id: str, authorization: str | None = Header(None)):
    auth(authorization)
    with lock:
        entry = jobs.get(job_id)
        if not entry:
            raise HTTPException(404, "Job expired or unknown")
        if entry["status"] == "FAILED":
            raise HTTPException(422, "Merge failed: " + entry.get("error", "Unknown error"))
        return {field: entry[field] for field in ("status", "status_url", "response_url")}

@app.get("/jobs/{job_id}")
def job_result(job_id: str, req: Request, authorization: str | None = Header(None)):
    auth(authorization)
    with lock:
        entry = jobs.get(job_id)
        if not entry:
            raise HTTPException(404, "Job expired or unknown")
        if entry["status"] != "COMPLETED":
            raise HTTPException(409, "Job is not completed")
        expires = int(time.time()) + TTL
        sig = hmac.new(TOKEN.encode(), f"{job_id}:{expires}".encode(), hashlib.sha256).hexdigest()
        url = f"{origin(req)}/files/{job_id}.mp4?expires={expires}&sig={sig}"
        return {"video": {"url": url, "content_type": "video/mp4"},
                "duration": entry["duration"]}

@app.get("/files/{job_id}.mp4")
def media(job_id: str, expires: int, sig: str):
    if expires < time.time() or expires > time.time() + TTL + 60:
        raise HTTPException(403, "Media URL expired")
    expected = hmac.new(TOKEN.encode(), f"{job_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(403, "Invalid URL signature")
    file = ROOT / job_id / "result.mp4"
    if not job_id.isalnum() or not file.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(file, media_type="video/mp4", filename="merged-video.mp4")
