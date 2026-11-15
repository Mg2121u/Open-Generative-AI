"""One-shot, capped, owner-only Wan 3.0 480p provider connectivity test.

Runs ONLY in the existing staging worker with WORKER_MODE=canary_once and
ENABLE_PAID_GENERATION=true. Never queues the complete paid recreation pipeline.
No second POST is permitted after a durable reservation was created.
"""
import logging
import math
import os
import tempfile
import time
from pathlib import Path

from . import db, ffmpeg, provider, storage

LOGGER = logging.getLogger('wan-canary')
STEP_KEY = 'wan_nicole_identity_canary_480p_20261115_002'
MODEL = 'alibaba/wan-3.0/reference-to-video'
RATE_USD = 0.05
CAP_USD = 0.40
OUTPUT_SECONDS = 3
INPUT_SECONDS = 3


def estimated_cost(source_duration, output_seconds=OUTPUT_SECONDS):
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise ValueError('Invalid reference duration')
    return round((math.ceil(source_duration) + output_seconds) * RATE_USD, 2)


def require_approval():
    if os.getenv('WORKER_MODE') != 'canary_once':
        raise RuntimeError('Wan canary must run in canary_once mode')
    if os.getenv('WAN_CANARY_ONE_SHOT', 'false').lower() != 'true':
        raise RuntimeError('Wan canary requires explicit one-shot switch')
    if os.getenv('ENABLE_PAID_GENERATION', 'false').lower() != 'true':
        raise RuntimeError('Paid generation remains disabled')
    if os.getenv('ENABLE_PAID_VOICE_GENERATION', 'false').lower() == 'true':
        raise RuntimeError('Voice generation must stay disabled during canary')
    configured = os.getenv('WAN_CANARY_MAX_USD', '')
    if configured != '0.40':
        raise RuntimeError('One-shot budget must be exactly $0.40')
    if not os.getenv('WAVESPEED_API_KEY'):
        raise RuntimeError('Staging WaveSpeed key missing')
    job_id = os.getenv('WAN_CANARY_SOURCE_JOB_ID', '')
    if not job_id:
        raise RuntimeError('Source job is not selected')
    allowed = {x.strip() for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS', '').split(',')}
    job = db.get_job(job_id)
    if not job or str(job['owner_id']) not in allowed or not job.get('original_key'):
        raise RuntimeError('Canary source job is not archived and owner-authorized')
    if job.get('state') != 'DRAFT' or job.get('character') != 'NICOLE':
        raise RuntimeError('Canary requires the archived Nicole staging draft')
    cp = (job.get('checkpoint') or {}).get('local_preflight') or {}
    if cp.get('source_key') != job['original_key'] or not cp.get('complete'):
        raise RuntimeError('Canary needs completed source preflight')
    if not cp.get('segment_keys'):
        raise RuntimeError('Canary needs an archived source segment')
    identity=(job.get('checkpoint') or {}).get('identity') or {}
    if identity.get('status')!='ready' or not identity.get('nicole_image_key'):
        raise RuntimeError('Nicole reference image not archived; cannot request paid identity replacement')
    return job, cp['segment_keys'][0]


def payload_for(url, width, height, nicole_images):
    if not isinstance(nicole_images,list) or not 1 <= len(nicole_images) <= 3:
        raise ValueError('Nicole reference image is mandatory')
    if not all(isinstance(v,str) and v.startswith('https://') for v in nicole_images):
        raise ValueError('Reference must be an HTTPS image URL')
    return {
        'prompt': (
            'IDENTITY REPLACEMENT: The woman in Image 1 is Nicole and is the ONLY person '
            'to appear in the output. Completely replace the original performer in Video 1 '
            'with Nicole from Image 1. Her face shape, facial features, hair, skin tone, '
            'and body proportions must follow Image 1, NOT the original woman in Video 1. '
            'Video 1 is ONLY a motion and composition guide: faithfully reproduce the '
            'actions, gestures, choreography, camera angles, cuts, lighting, scene, and '
            'timing from Video 1 with Nicole performing them. Keep the requested outfit '
            'and setting visible in Video 1, but NEVER inherit or blend the original '
            'performer identity. No extra people, no text, no logo, no watermarks.'
        ),
        'reference_images': nicole_images,
        'reference_videos': [url],
        'resolution': '480p',
        'aspect_ratio': '9:16' if height > width else '16:9',
        'duration': OUTPUT_SECONDS,
        'generate_audio': False,
        'enable_prompt_expansion': False,
    }


def _reference_clip(job, first_key):
    key = f"recreation/{job['job_id']}/canary/short_reference.mp4"
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / 'source.mp4'
        out = Path(td) / 'short.mp4'
        storage.download_key(first_key, inp)
        ffmpeg.trim(inp, out, 0, INPUT_SECONDS)
        media = ffmpeg.probe(out)
        cost = estimated_cost(media['duration'])
        if cost > CAP_USD or media['duration'] > 4 or min(media['height'], media['width']) < 240:
            raise RuntimeError('Reference unsuitable for bounded canary')
        storage.upload_key(key, out)
    return key, media, cost


def run_once():
    job, first_key = require_approval()
    job_id = str(job['job_id'])
    # Check an existing reservation BEFORE preparing new media or making any POST.
    saved = db.get_step(job_id, STEP_KEY)
    if saved and saved['state'] not in ('SUBMITTED', 'POLLING', 'COMPLETED'):
        raise provider.NeedsReview('Canary request is uncertain; do not create another prediction')
    if not saved:
        ref_key, media, cost = _reference_clip(job, first_key)
        identity = (job.get('checkpoint') or {}).get('identity') or {}
        image_url = storage.presign(identity['nicole_image_key'], 3600)
        payload = payload_for(storage.presign(ref_key, 3600), media['width'], media['height'], [image_url])
        LOGGER.info('WAN_CANARY submission_ready=true estimated_usd=%.2f max_usd=%.2f', cost, CAP_USD)
        saved = provider.submit_once(job_id, STEP_KEY, MODEL, payload)
        LOGGER.info('WAN_CANARY submitted=true request_id_recorded=%s', bool(saved.get('external_request_id')))

    deadline = time.monotonic() + 360
    while True:
        if saved['state'] == 'COMPLETED':
            result = saved['result']
        else:
            result = provider.poll(saved)
            if str(result.get('status', '')).lower() != 'completed':
                if time.monotonic() > deadline:
                    LOGGER.info('WAN_CANARY still_processing=true saved_request_id=true')
                    return 'processing'
                time.sleep(12)
                saved = db.get_step(job_id, STEP_KEY)
                continue

        outputs = result.get('outputs') or []
        if not isinstance(outputs, list) or not outputs:
            raise provider.NeedsReview('Canary returned no usable output; inspect provider dashboard')
        # Each distinct paid request needs a distinct immutable output key.
        # Do not mistake the first connectivity canary for the Nicole result.
        out_key = f'recreation/{job_id}/canary/{STEP_KEY}.mp4'
        try:
            storage.client().head_object(Bucket=storage.bucket(), Key=out_key)
        except Exception:
            storage.store_provider_url(outputs[0], out_key)
        LOGGER.info('WAN_CANARY complete=true output_archived=true paid_calls_limit=1')
        return 'completed'


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        LOGGER.info('WAN_CANARY starting=true')
        status = run_once()
        LOGGER.info('WAN_CANARY status=%s', status)
    except Exception as exc:
        # Never log credential-bearing URLs, provider payloads, or keys.
        LOGGER.error('WAN_CANARY failed=true error_type=%s', type(exc).__name__)
        raise SystemExit(1) from None
