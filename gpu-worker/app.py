"""Salad image worker: persistent private S3 or ephemeral one-replica pilot."""
import io
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode
from typing import Literal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from local_store import EphemeralStore
from qwen_engine import load_qwen_engine, render_qwen_image
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

def normalize_salad_environment():
    """Accept the legacy auto-converted names shown in the user's Salad group.

    Only known variables are aliased. Canonical env names always take priority.
    Never print environment values, especially credentials.
    """
    aliases = {
        "WORKER_API_TOKEN": "_w_o_r_k_e_r__a_p_i__t_o_k_e_n",
        "STORAGE_DRIVER": "_s_t_o_r_a_g_e__d_r_i_v_e_r",
        "ENVIRONMENT": "_e_n_v_i_r_o_n_m_e_n_t",
        "MAX_REPLICAS": "_m_a_x__r_e_p_l_i_c_a_s",
    }
    applied = []
    for standard, legacy in aliases.items():
        if standard not in os.environ and legacy in os.environ:
            os.environ[standard] = os.environ[legacy]
            applied.append(standard)
    return applied


# Salad Portal sometimes converts upper-case environment variable names to
# per-letter snake_case. Normalize the documented legacy names before setup.
_normalized_settings = normalize_salad_environment()

log = logging.getLogger("salad-worker")
logging.basicConfig(level="INFO")
if _normalized_settings:
    log.warning("Recognized Salad environment aliases: %s", ", ".join(_normalized_settings))
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
    reference_images: dict[str, str] = Field(default_factory=dict)
    placement: Literal["foreground_floor", "rear_floor", "sofa_seated"] = "foreground_floor"
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
        if isinstance(e, FileNotFoundError) or getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey"):
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
        # TestClient injects storage/model; never replace them in mock unit tests.
        if os.getenv("ENVIRONMENT") == "test" and os.getenv("MOCK_INFERENCE") == "1" and model == "mock" and s3 is not None:
            ready.set()
            return
        if os.getenv("MOCK_INFERENCE") == "1" and os.getenv("ENVIRONMENT") != "test":
            raise RuntimeError("Mock inference is not permitted in production")
        if os.getenv("STORAGE_DRIVER") == "ephemeral":
            if os.getenv("MAX_REPLICAS", "1") != "1":
                raise RuntimeError("Ephemeral output requires exactly one replica")
            s3 = EphemeralStore()
            log.warning("Ephemeral pilot storage: images/jobs are lost on container replacement")
        else:
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
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA GPU unavailable")
            model = load_qwen_engine()
        ready.set()
        log.info("Qwen model and private storage ready")
    except Exception:
        log.exception("Initialization failed; readiness will stay false")

@app.on_event("startup")
def startup():
    global pool
    # Each ASGI lifecycle needs its own executor; prior test clients
    # shutdown the previous executor on exit.
    pool = ThreadPoolExecutor(max_workers=1)
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

def decode_private_reference(value: str):
    """Decode only local inline image bytes; never fetch a user-provided URL."""
    if not isinstance(value, str) or not value.startswith("data:image/jpeg;base64,"):
        raise ValueError("Reference must be an inline JPEG image")
    data = value.split(",", 1)[1]
    if len(data) > 1_850_000:
        raise ValueError("Reference image too large")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Malformed base64 reference image") from exc
    if len(raw) > 1_350_000:
        raise ValueError("Reference image too large")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "JPEG" or image.width * image.height > 6_000_000:
                raise ValueError("Unsupported reference image format or dimensions")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid reference image") from exc


def reference_role_order(payload):
    refs = payload.reference_images
    if not isinstance(refs, dict) or set(refs) - {"identity", "scenery"} or len(refs) > 2:
        raise ValueError("Unsupported reference roles")
    return [(role, decode_private_reference(refs[role])) for role in ("identity", "scenery") if role in refs]


PLACEMENT_GUIDANCE = {
    "foreground_floor": (
        "Place the subject standing on a CLEAR PATCH OF THE VISIBLE FLOOR/CARPET in the foreground, "
        "ON THE CAMERA SIDE of the coffee table. The entire person is in front of the table, not inside it. "
        "Both shoes rest firmly on the floor and cast realistic contact shadows. "
        "Keep the full standing body and both feet visible within the frame."
    ),
    "rear_floor": (
        "Place the subject standing on the actual floor near the windows, BEHIND the coffee table. "
        "The table may naturally hide the legs or feet according to correct occlusion, "
        "but the shoes must never appear on the tabletop or cut through furniture."
    ),
    "sofa_seated": (
        "Seat the subject naturally on the sofa with hips resting on the cushion, "
        "knees bending anatomically and feet planted on the floor. "
        "The coffee table remains in front with correct depth and natural occlusion."
    ),
}


def build_reference_prompt(payload, roles):
    """Anchor scene geometry and realistic anatomy when composing two references.

    This improves instructions but cannot mathematically guarantee geometry or likeness.
    """
    role_set = set(roles)
    if not role_set:
        return payload.prompt
    intro = []
    for index, role in enumerate(roles, 1):
        intro.append(
            f"Image {index} is the EXACT {role} reference; "
            + ("use only this person's identity, face, hair and general appearance."
               if role == "identity" else
               "use this existing room as the master scene and preserve the original camera angle, "
               "furniture positions, room scale, floor plane, windows, and lighting.")
        )
    if "scenery" in role_set and "identity" in role_set:
        intro.append(
            "Photographic composite of ONE physically present, realistically sized adult human "
            "inside the room from Image 2. Respect a single fixed camera perspective and depth. "
            "Scale the person's overall height and leg length realistically relative to the sofa, "
            "coffee table and window frames. Legs, pelvis, feet, shoulders and head have "
            "natural adult human proportions; no compressed legs or miniature body. "
            "Do not move the coffee table to intersect the person. "
            "Enforce correct foreground/background occlusion and physically plausible contact shadows."
        )
        intro.append(PLACEMENT_GUIDANCE[payload.placement])
    elif "identity" in role_set:
        intro.append(
            "Keep anatomically natural, believable head-to-body, torso-to-leg and shoe proportions; "
            "do not shorten legs, stretch limbs or change body shape."
        )
    else:
        intro.append(
            "Preserve scene scale, camera perspective, major furniture, visible floor "
            "and architectural geometry."
        )
    intro.append("Requested scene and styling: " + payload.prompt)
    return " ".join(intro)


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
            references = reference_role_order(payload)
            prompt = build_reference_prompt(payload, [role for role, _ in references])
            image = render_qwen_image(model, prompt, references, width, height)
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
        raise HTTPException(422, "Qwen pilot supports inline reference image editing only; video is not available")
    try:
        refs = reference_role_order(payload)
        if not refs:
            raise ValueError("Qwen edit requires a reference image")
    except ValueError:
        raise HTTPException(422, "Qwen needs at least one private reference JPEG, under 1.35 MB and 6 megapixels")
    with lock:
        if queue_depth >= 2:
            raise HTTPException(429, "Queue full")
        queue_depth += 1
    job_id = uuid.uuid4().hex
    try:
        write_job(job_id, {"id":job_id, "character":payload.character, "status":"IN_QUEUE", "created_at":utcnow(),
                           "updated_at":utcnow(), "assets_keys":[], "error":None})
        pool.submit(render, job_id, payload)
    except Exception:
        log.exception("Unable to save or schedule job")
        with lock:
            queue_depth -= 1
        raise HTTPException(503, "Unable to save job")
    return {"id":job_id}

def signed_asset_url(request: Request, job_id: str) -> str:
    token = os.getenv("WORKER_API_TOKEN", "")
    expiry = int(time.time()) + 900
    msg = f"{job_id}:{expiry}".encode("utf-8")
    signature = hmac.new(token.encode(), msg, hashlib.sha256).hexdigest()
    # No extra PUBLIC_URL variable required: use the official Salad HTTPS gateway
    # host observed on the authenticated status request.
    hostname = request.headers.get("host", "")
    if not hostname or len(hostname) > 255 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.:"
                                                  for c in hostname):
        raise HTTPException(500, "Invalid public gateway hostname")
    return f"https://{hostname}/assets/{job_id}.png?" + urlencode({"expires": expiry, "sig": signature})


@app.get("/assets/{job_id}.png")
def get_ephemeral_asset(job_id: str, expires: int, sig: str):
    if not isinstance(s3, EphemeralStore):
        raise HTTPException(404, "Not found")
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404, "Not found")
    if expires < int(time.time()) or expires > int(time.time()) + 3600:
        raise HTTPException(403, "Signed link expired")
    if len(sig) != 64:
        raise HTTPException(403, "Invalid signed link")
    mac = hmac.new(os.getenv("WORKER_API_TOKEN", "").encode(),
                   f"{job_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(mac, sig):
        raise HTTPException(403, "Invalid signed link")
    record = read_job(job_id)
    if not record or record.get("status") != "COMPLETED":
        raise HTTPException(404, "Not found")
    keys = record.get("assets_keys", [])
    if len(keys) != 1:
        raise HTTPException(404, "Not found")
    try:
        body = s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read()
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    return Response(content=body, media_type="image/png",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/jobs/{job_id}", dependencies=[Depends(authorize)])
def status(job_id: str, request: Request):
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
    urls = []
    if state == "COMPLETED":
        if isinstance(s3, EphemeralStore):
            urls = [signed_asset_url(request, job_id)] if record.get("assets_keys") else []
        else:
            urls = [s3.generate_presigned_url("get_object",
                Params={"Bucket":BUCKET, "Key":k}, ExpiresIn=900)
                for k in record.get("assets_keys", [])]
    return {"status":state, "assets":urls, "error":record.get("error")}
