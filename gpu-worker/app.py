"""Salad image worker. One GPU/one replica; jobs and assets persist to private S3."""
import io
import json
import logging
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("salad-worker")
logging.basicConfig(level="INFO")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
pool = ThreadPoolExecutor(max_workers=1)
lock = threading.Lock()
queue_depth = 0
model = None
s3 = None
ready = threading.Event()
BUCKET = os.getenv("S3_BUCKET", "")
PREFIX = "aria-nicole-pilot"
SIZES = {"9:16": (640,1152), "1:1": (1024,1024), "16:9": (1152,640)}

class Job(BaseModel):
    character: str
    mode: str = "image"
    prompt: str = Field(min_length=3, max_length=3000)
    aspect_ratio: str = "9:16"
    reference_image_url: str | None = None
    duration: int | None = None

def authorize(authorization: str | None = Header(default=None)):
    token = os.getenv("WORKER_API_TOKEN", "")
    got = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if len(token) < 32:
        raise HTTPException(503, "Worker token not configured")
    if not secrets.compare_digest(got, token):
        raise HTTPException(401, "Unauthorized")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def job_key(job_id):
    return f"{PREFIX}/jobs/{job_id}.json"

def write_job(job_id, data):
    s3.put_object(Bucket=BUCKET, Key=job_key(job_id), Body=json.dumps(data).encode(), ContentType="application/json")

def read_job(job_id):
    try:
        raw = s3.get_object(Bucket=BUCKET, Key=job_key(job_id))
        return json.loads(raw["Body"].read().decode())
    except Exception as e:
        if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise

def update_job(job_id, patch):
    current = read_job(job_id)
    if current is None:
        raise ValueError("Missing job")
    current.update(patch)
    write_job(job_id, current)

def init():
    global s3, model
    try:
        if os.getenv("MOCK_INFERENCE") == "1" and os.getenv("ENVIRONMENT") != "test":
            raise RuntimeError("Mock inference is not permitted in production")
        import boto3
        if not BUCKET or not os.getenv("S3_ENDPOINT", "").startswith("https://"):
            raise RuntimeError("Private S3 storage is not configured")
        s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.getenv("S3_REGION", "auto"))
        if os.getenv("MOCK_INFERENCE") == "1":
            model = "mock"
        else:
            import torch
            from diffusers import Flux2KleinPipeline
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA GPU unavailable")
            model = Flux2KleinPipeline.from_pretrained(
                "black-forest-labs/FLUX.2-klein-4B", torch_dtype=torch.bfloat16
            ).to("cuda")
        ready.set()
        log.info("Model and private storage ready")
    except Exception:
        log.exception("Initialization failed; readiness will stay false")

@app.on_event("startup")
def startup():
    threading.Thread(target=init, daemon=True).start()

@app.on_event("shutdown")
def shutdown():
    pool.shutdown(wait=False, cancel_futures=True)

@app.get("/healthz")
def healthz():
    return {"status": "alive"}

@app.get("/readyz")
def readyz():
    if not ready.is_set():
        raise HTTPException(503, "Model or S3 not yet ready")
    return {"status": "ready"}

def render(job_id, payload):
    global queue_depth
    try:
        update_job(job_id, {"status": "IN_PROGRESS", "updated_at": utcnow()})
        from PIL import Image, ImageDraw
        width, height = SIZES[payload.aspect_ratio]
        if model == "mock":
            image = Image.new("RGB", (width, height), (40,35,75))
            ImageDraw.Draw(image).text((20,20), "TEST MODE", fill="white")
        else:
            import torch
            image = model(
                prompt=payload.prompt, width=width, height=height,
                num_inference_steps=4, guidance_scale=1.0
            ).images[0]
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        key = f"{PREFIX}/outputs/{payload.character.lower()}/{job_id}.png"
        s3.put_object(Bucket=BUCKET, Key=key, Body=stream.getvalue(), ContentType="image/png")
        update_job(job_id, {"status": "COMPLETED", "updated_at": utcnow(), "assets_keys": [key]})
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        try:
            update_job(job_id, {"status": "ERROR", "updated_at": utcnow(), "error": type(exc).__name__})
        except Exception:
            log.exception("Could not record failed job")
    finally:
        with lock:
            queue_depth -= 1

@app.post("/jobs", dependencies=[Depends(authorize)], status_code=202)
def create_job(payload: Job):
    global queue_depth
    if not ready.is_set():
        raise HTTPException(503, "Worker not yet ready")
    if payload.character not in ("Aria", "Nicole") or payload.aspect_ratio not in SIZES:
        raise HTTPException(422, "Invalid character or aspect ratio")
    if payload.mode != "image" or payload.duration is not None or payload.reference_image_url:
        raise HTTPException(422, "Phase one supports text-to-image only; reference and video coming later")
    with lock:
        if queue_depth >= 2:
            raise HTTPException(429, "Queue full")
        queue_depth += 1
    job_id = uuid.uuid4().hex
    try:
        write_job(job_id, {"id":job_id, "status":"IN_QUEUE", "created_at":utcnow(),
                           "updated_at":utcnow(), "assets_keys":[], "error":None})
        pool.submit(render, job_id, payload)
    except Exception:
        with lock:
            queue_depth -= 1
        raise HTTPException(503, "Unable to save job")
    return {"id":job_id}

@app.get("/jobs/{job_id}", dependencies=[Depends(authorize)])
def status(job_id: str):
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(400, "Invalid job ID")
    if s3 is None:
        raise HTTPException(503, "Storage not available")
    record = read_job(job_id)
    if not record:
        raise HTTPException(404, "Job not found")
    state = record["status"]
    if state in ("IN_QUEUE", "IN_PROGRESS"):
        try:
            age = time.time() - datetime.fromisoformat(record["updated_at"]).timestamp()
            if age > 1200:
                return {"status":"ERROR", "assets":[], "error":"Worker interrupted; job expired"}
        except (TypeError, ValueError):
            pass
    urls = [s3.generate_presigned_url("get_object",
             Params={"Bucket":BUCKET, "Key":k}, ExpiresIn=900)
            for k in record.get("assets_keys", [])] if state == "COMPLETED" else []
    return {"status":state, "assets":urls, "error":record.get("error")}
