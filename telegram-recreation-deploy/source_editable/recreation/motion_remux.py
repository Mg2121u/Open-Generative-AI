"""One-shot, no-AI-credit motion-aware reassembly of Nicole's existing segments.

Strictly reuses the FIVE private MP4s from the verified continuity-v2 run.
No WaveSpeed or other paid-provider API is imported or invoked by this module.
It never overwrites the old final video or mutates the old provider steps.
"""
import logging
import os
from . import db,full_video,storage,telegram

LOGGER=logging.getLogger('motion-remux')
RUN_KEY='nicole_full_480p_motion_v3_20261115'
PREVIOUS_KEY=full_video.RUN_KEY


def destination(job_id):
    return f'recreation/{job_id}/full_video/{RUN_KEY}/nicole_with_original_audio.mp4'


def source_segment(job_id,index):
    return f'recreation/{job_id}/full_video/{PREVIOUS_KEY}/segments/{index:03}.mp4'


def guard(job):
    if os.getenv('WORKER_MODE')!='motion_remux_once' or os.getenv('MOTION_REMUX_ONE_SHOT')!='true':
        raise RuntimeError('Motion-only reassembly requires its isolated one-shot mode')
    if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='false':
        raise RuntimeError('Motion-only reassembly refuses any paid generation flag')
    if os.getenv('ENABLE_PAID_VOICE_GENERATION','false').lower()!='false':
        raise RuntimeError('Motion-only reassembly refuses paid voice flag')
    if str(job.get('job_id'))!=os.getenv('MOTION_REMUX_JOB_ID',''):
        raise RuntimeError('Motion-only reassembly job mismatch')
    allowed={int(x.strip()) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()}
    if job.get('owner_id') not in allowed or job.get('state')!='DRAFT' or job.get('character')!='NICOLE':
        raise RuntimeError('Unrecognized Nicole owner draft')
    info=full_video.report(job)
    if info['source_duration']>60 or len(info['segments'])>24:
        raise RuntimeError('Unsupported input length for motion-only reassembly')
    return info


def _deliver_once(job,key):
    job_id=str(job['job_id'])
    step_key=f'{RUN_KEY}_telegram_delivery'
    previous=db.get_step(job_id,step_key)
    if previous:
        if previous['state']=='COMPLETED':return 'already_sent'
        raise RuntimeError('Ambiguous existing Telegram send; do not duplicate')
    reserved=db.reserve_one_shot(job_id,step_key,'telegram',{'video_key':key,'purpose':'no_paid_motion_remux'})
    if not reserved:
        raise RuntimeError('Telegram send reserved by another worker')
    try:
        result=telegram.telegram('sendVideo',{'chat_id':job['chat_id'],
             'video':storage.presign(key,3600),
             'caption':'Nicole smoother motion edit (v3). Same Wan generations, original soundtrack. No new AI credits.'})
        db.external_finished(job_id,step_key,{'message_id':result.get('message_id')})
    except Exception as exc:
        raise RuntimeError('Video archived; Telegram delivery outcome needs manual review') from exc
    return 'sent'


def run_once():
    job_id=os.getenv('MOTION_REMUX_JOB_ID','')
    if not job_id:raise RuntimeError('No motion-remux source job selected')
    job=db.get_job(job_id)
    if not job:raise RuntimeError('Owner job is missing')
    info=guard(job)
    segments=info['segments']
    if not info['reused_first_canary']:
        raise RuntimeError('Previously verified first Nicole clip no longer matches this source')
    keys=[]
    for seg in segments:
        idx=seg['index']
        if idx==0:
            key=full_video._get_reused_first(job_id)
        else:
            key=source_segment(job_id,idx)
            step=db.get_step(job_id,full_video.step_key(idx))
            if not step or step.get('state')!='COMPLETED':
                raise RuntimeError('Previous continuity model prediction missing; no new submissions')
        if not full_video._stored(key):
            raise RuntimeError('Previously generated segment missing; no new submissions')
        keys.append(key)
    key=destination(job_id)
    if not full_video._stored(key):
        LOGGER.info('MOTION_REMUX assembling_no_provider_calls=true segments=%d',len(keys))
        full_video._merge_original_sound(job,segments,keys,destination_override=key,motion_aware=True)
    status=_deliver_once(job,key)
    LOGGER.info('MOTION_REMUX done=true provider_calls=0 delivery=%s',status)
    return 'completed'
