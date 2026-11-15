"""Idempotent, durable WaveSpeed generation submission/polling primitives.

This module is reusable by the exact migrated Wan workflow; it deliberately
never invents prompts or changes the reference-generation payload.
"""
import os
import requests
from . import db

WAVESPEED_URL='https://api.wavespeed.ai/api/v3'


class NeedsReview(RuntimeError):
    """Submission may have been accepted and billed; not safe to retry blindly."""


def auth_headers():
    key=os.environ.get('WAVESPEED_API_KEY')
    if not key: raise RuntimeError('WAVESPEED_API_KEY missing')
    return {'Authorization':'Bearer '+key,'Content-Type':'application/json'}


def _paid_gate():
    if os.getenv('ENABLE_PAID_GENERATION','false').strip().lower() != 'true':
        raise RuntimeError('Paid generation is disabled; external submission blocked')


def submit_once(job_id,step_key,endpoint,payload):
    """Submit at most once automatically for a checkpoint key.

    A crash between provider acceptance and writing the provider ID creates an
    ambiguous state; escalate instead of re-running and double billing.
    """
    existing=db.get_step(job_id,step_key)
    if existing:
        if existing['state'] in ('SUBMITTED','POLLING','COMPLETED'):
            return existing
        if existing['state'] in ('SUBMITTING','UNCERTAIN'):
            raise NeedsReview('Provider submission outcome unknown; inspect provider dashboard before retry')
        if existing['state']=='FAILED':
            raise NeedsReview('Manual approval required before recreating a failed paid submission')
    # Fail closed before writing an ambiguous paid intent, and never route to a
    # different host or model via a user-controlled endpoint value.
    _paid_gate()
    if endpoint != 'alibaba/wan-3.0/reference-to-video':
        raise ValueError('Unapproved WaveSpeed model endpoint')
    if payload.get('resolution') != '480p':
        raise ValueError('Only tested 480p generation is permitted')
    if not payload.get('reference_videos') or not payload.get('prompt'):
        raise ValueError('Missing reference video or generation prompt')
    images = payload.get('reference_images')
    if not isinstance(images,list) or not 1 <= len(images) <= 10 or not all(isinstance(v,str) and v.startswith('https://') for v in images):
        raise ValueError('Nicole/Aria identity reference images required; refusing paid video-only recreation')
    headers=auth_headers()
    reserved=db.prepare_external(job_id,step_key,'wavespeed',{'endpoint':endpoint,'payload':payload})
    if not reserved:
        raise NeedsReview('Another worker reserved this provider request; no second submission')
    try:
        response=requests.post(WAVESPEED_URL+'/'+endpoint,
                               headers=headers,json=payload,timeout=(15,55))
        response.raise_for_status()
        result=response.json()
    except requests.RequestException as exc:
        # Even some 5xx responses can follow accepted provider submissions.
        raise NeedsReview('Submission may have succeeded; check provider jobs before any retry') from exc
    if result.get('code') != 200:
        raise NeedsReview('Provider returned an unsuccessful response; reconcile before retry')
    provider_id=(result.get('data') or {}).get('id')
    if not isinstance(provider_id,str) or not provider_id.strip():
        raise NeedsReview('Provider response lacks a request ID; investigate before retry')
    saved=db.external_submitted(job_id,step_key,provider_id)
    if not saved: raise NeedsReview('Could not save provider request ID; investigate before retry')
    return saved


def poll(step):
    """Read-only polling: safe to retry after timeouts or server restarts."""
    external_id=step['external_request_id']
    if not external_id: raise NeedsReview('No saved provider request ID')
    response=requests.get(WAVESPEED_URL+'/predictions/'+external_id+'/result',
                          headers=auth_headers(),timeout=(15,50))
    response.raise_for_status()
    body=response.json()
    if body.get('code') != 200:
        raise RuntimeError('Provider result lookup failed')
    data=body.get('data') or {}
    status=str(data.get('status','')).lower()
    if status=='completed':
        db.external_finished(step['job_id'],step['step_key'],data)
    elif status in ('failed','cancelled','timeout','deleted'):
        # Terminal provider outcomes cannot recover by automatic re-submission.
        raise NeedsReview(f'Provider job ended as {status}; inspect the saved request ID')
    elif status not in ('created','processing'):
        raise NeedsReview('Unknown provider status; inspect provider dashboard')
    return data


def chat_once(job_id, step_key, body):
    """Synchronous WaveSpeed LLM POST, with write-ahead intent for billing safety.

    A crash after a provider accepted the call is ambiguous; never automatically
    retry such a step, even if no response was durably recorded.
    """
    existing = db.get_step(job_id, step_key)
    if existing:
        if existing['state'] == 'COMPLETED':
            return existing['result']
        raise NeedsReview(f'LLM step {step_key} is {existing["state"]}; manual reconciliation required')
    _paid_gate()
    headers=auth_headers()
    reserved=db.prepare_external(job_id, step_key, 'wavespeed_llm', {'model': body.get('model'), 'body': body})
    if not reserved:
        raise NeedsReview('Another worker already reserved this LLM request')
    try:
        response = requests.post('https://llm.wavespeed.ai/v1/chat/completions',
            headers=headers, json=body, timeout=(20, 240))
        response.raise_for_status()
        output = response.json()
    except Exception as exc:
        raise NeedsReview(f'LLM {step_key} response uncertain; do not resubmit blindly') from exc
    saved = db.external_finished(job_id, step_key, output)
    if not saved:
        raise NeedsReview('Could not persist LLM result; inspect billing before retry')
    return output


def upload_media_once(job_id, source_path):
    """Original n8n source upload step, checkpointed before the external POST."""
    key='reference_video_upload'
    existing=db.get_step(job_id,key)
    if existing:
        if existing['state']=='COMPLETED':
            value=((existing['result'] or {}).get('data') or {}).get('download_url')
            if not value: raise NeedsReview('Saved source upload lacks a WaveSpeed URL')
            return value
        raise NeedsReview(f'Source upload outcome ambiguous: {existing["state"]}')
    _paid_gate()
    auth=auth_headers()
    reserved=db.prepare_external(job_id,key,'wavespeed',{'endpoint':'media/upload/binary?ext=mp4'})
    if not reserved:
        raise NeedsReview('Another worker already reserved this source media upload')
    try:
        with open(source_path,'rb') as stream:
            result=requests.post(WAVESPEED_URL+'/media/upload/binary?ext=mp4',
                headers={'Authorization':auth['Authorization'],
                         'Content-Type':'application/octet-stream'},
                data=stream,timeout=(20,300))
            result.raise_for_status()
            payload=result.json()
    except Exception as exc:
        raise NeedsReview('Source upload outcome uncertain; inspect provider before retry') from exc
    url=(payload.get('data') or {}).get('download_url')
    if not url or not url.startswith('https://'):
        raise NeedsReview('Source upload returned no usable WaveSpeed HTTPS download URL')
    db.external_finished(job_id,key,payload)
    return url
