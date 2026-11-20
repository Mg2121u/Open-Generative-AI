"""Safely replace exactly one completed Wan video section.

Owner command workflow:
  /segments [oldjob8] -> show source-clock sections, no provider calls.
  /redo_segment oldjob8 3 -> private, UNBILLED draft.
  /confirm_segment -> one immutable WaveSpeed submission and local remux.

The completed original job, its paid predictions, and its final archive are
NEVER overwritten. All other original sections are reused from private S3.
"""
from __future__ import annotations

import logging
import math
import os
from urllib.parse import unquote, urlsplit

from . import db, full_video, provider, quality, storage, wan_canary, identity_qa

LOG=logging.getLogger('segment-revision')
REVISION_STEP='wan_individual_section_revision_v1'


def revision_key(job, section_index):
    return (f'recreation/{job["job_id"]}/section_revision/v1/'
            f'segment_{section_index:03}.mp4')


def final_key(job):
    return f'recreation/{job["job_id"]}/section_revision/v1/nicole_with_original_audio.mp4'


def _validate(job):
    if job.get('stage')!='SEGMENT_REDO' or job.get('state') not in ('RUNNING','WAITING_PROVIDER','QUEUED'):
        raise RuntimeError('Single-section revision requires explicit confirmed queue')
    if job.get('character')!='NICOLE' or job.get('model')!='wan-3.0':
        raise RuntimeError('Single-section replacement supports Nicole / Wan 3.0 only')
    cp=job.get('checkpoint') or {}
    revision=cp.get('segment_revision') or {}
    parent_id=revision.get('parent_job_id')
    if not parent_id or revision.get('parent_run_key')!=full_video.RUN_KEY:
        raise provider.NeedsReview('Original run namespace changed; cannot mix video versions')
    parent=db.get_job(parent_id,job.get('owner_id'))
    if not parent or parent.get('state')!='COMPLETED' or parent.get('stage')!='DONE':
        raise provider.NeedsReview('Original job no longer confirmed complete')
    if parent.get('original_key')!=job.get('original_key'):
        raise provider.NeedsReview('Revision source does not match original archive')
    parent_cp=parent.get('checkpoint') or {}
    if (cp.get('identity') or {}).get('nicole_image_key') != (parent_cp.get('identity') or {}).get('nicole_image_key'):
        raise provider.NeedsReview('Nicole identity changed; refusing a mixed-character remake')
    if (cp.get('local_preflight') or {}).get('source_key')!=parent['original_key']:
        raise provider.NeedsReview('Original source preflight changed')
    duration=((parent_cp.get('local_preflight') or {}).get('source_probe') or {}).get('duration')
    plan=quality.quality_plan(duration)
    index=int(revision.get('section_index',-1))
    if (len(plan)!=int(parent.get('segment_total') or 0)
            or not 0<=index<len(plan) or int(revision.get('parent_sections') or 0)!=len(plan)):
        raise provider.NeedsReview('Original section plan mismatch; no paid model call')
    scene=quality.scene_reference(job)
    if scene.get('status') in ('awaiting','pending','failed'):
        raise provider.NeedsReview('Scenery image not ready; finish /scenery before paying')
    if scene.get('status')=='ready' and not scene.get('scenery_image_key'):
        raise provider.NeedsReview('Scenery image key missing; no paid request')
    if len(plan)>120 or job.get('segment_total')!=1:
        raise provider.NeedsReview('Invalid section count for single-section revision')
    # No generation until all original clips AND the exact motion interval
    # are durably archived. Avoid a paid result that cannot be assembled.
    keys=[full_video.segment_key(str(parent['job_id']),i) for i in range(len(plan))]
    motion=full_video.source_key(str(parent['job_id']),index)
    missing=[key for key in keys+[motion] if not full_video._stored(key)]
    if missing:
        raise provider.NeedsReview('Original video sections or motion reference missing; no paid regeneration attempted')
    return parent,plan,index,keys,motion


def _guard_paid(job,cost):
    if os.getenv('WORKER_MODE')!='app_always_on' or os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
        raise RuntimeError('Paid section replacement is disabled')
    if not os.getenv('WAVESPEED_API_KEY'):
        raise RuntimeError('WaveSpeed provider unavailable')
    owners={int(x.strip()) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()}
    if job.get('owner_id') not in owners:
        raise RuntimeError('Section replacement is owner-only')
    try:
        cap=float(os.getenv('APP_MAX_ESTIMATED_USD','10'))
    except (TypeError,ValueError):
        raise provider.NeedsReview('Invalid configured per-video spending cap') from None
    if math.isnan(cap) or cap<=0 or cost>cap:
        raise provider.NeedsReview('Single-section estimated price exceeds authorized spending cap')


def _verified_existing_step(job,step,index,parent):
    if step['state'] not in ('SUBMITTED','POLLING','COMPLETED'):
        raise provider.NeedsReview('Ambiguous or failed Wan section request; never double-submit')
    payload=(step.get('input') or {}).get('payload') or {}
    pics=payload.get('reference_images') or []
    vids=payload.get('reference_videos') or []
    identity=(parent['checkpoint']['identity'])['nicole_image_key']
    motion=full_video.source_key(str(parent['job_id']),index)
    scene=quality.scene_reference(job)
    scene_key=scene.get('scenery_image_key') if scene.get('status')=='ready' else None
    scene_ok=(not scene_key or (len(pics)>=2 and scene_key in unquote(urlsplit(pics[-1]).path)))
    if (len(pics) not in (1,2,3) or len(vids)!=1 or not scene_ok
            or identity not in unquote(urlsplit(pics[0]).path)
            or motion not in unquote(urlsplit(vids[0]).path)):
        raise provider.NeedsReview('Saved Wan request used unexpected identity/motion references')


def run(job):
    """Restart-safe worker action; submits at most ONE new Wan prediction."""
    parent,segments,index,original_keys,motion=_validate(job)
    job_id=str(job['job_id'])
    replaced=revision_key(job,index)
    step=db.get_step(job_id,REVISION_STEP)
    if not full_video._stored(replaced):
        if step is None:
            _guard_paid(job,segments[index]['estimated_usd'])
            clip=(parent.get('checkpoint') or {})['local_preflight']['source_probe']
            identity=(parent.get('checkpoint') or {})['identity']['nicole_image_key']
            refs=[storage.presign(identity,3600)]
            face_anchor=False
            if os.getenv('RECREATION_FACE_QA_MODE','on').lower()!='off':
                anchor=identity_qa.prepare_anchor(job)
                refs.append(storage.presign(anchor,3600))
                face_anchor=True
            scene=quality.scene_reference(job)
            scene_number=None
            if scene.get('status')=='ready':
                scene_key=scene['scenery_image_key']
                if not full_video._stored(scene_key):
                    raise provider.NeedsReview('Archived scenery reference is missing; no paid regeneration')
                scene_number=len(refs)+1
                if scene_number>3:
                    raise provider.NeedsReview('Scenery image would exceed model reference limit')
                refs.append(storage.presign(scene_key,3600))
            payload=wan_canary.payload_for(
                storage.presign(motion,3600),
                clip['width'],clip['height'],refs
            )
            payload['prompt']=quality.build_prompt(
                job,chained=False,scene_image_number=scene_number,
                face_anchor_present=face_anchor)
            payload['seed']=quality.fixed_seed(job)
            payload['duration']=segments[index]['output_seconds']
            payload['generate_audio']=False
            LOG.info('SECTION_REDO submit job=%s section=%d cost_estimate=%.2f',
                     job_id[:8],index+1,segments[index]['estimated_usd'])
            step=provider.submit_once(job_id,REVISION_STEP,wan_canary.MODEL,payload)
        _verified_existing_step(job,step,index,parent)
        if step['state']!='COMPLETED':
            outcome=provider.poll(step)
            if str(outcome.get('status','')).lower()!='completed':
                LOG.info('SECTION_REDO waiting job=%s section=%d',job_id[:8],index+1)
                return 'processing'
            step=db.get_step(job_id,REVISION_STEP)
            _verified_existing_step(job,step,index,parent)
        provider_outputs=(step.get('result') or {}).get('outputs') or []
        if not provider_outputs or not isinstance(provider_outputs[0],str):
            raise provider.NeedsReview('Completed section request has no usable video URL')
        storage.store_provider_url(provider_outputs[0],replaced)
        LOG.info('SECTION_REDO archived job=%s section=%d',job_id[:8],index+1)
    identity_qa.check_archived(job,replaced)
    stitched=list(original_keys)
    stitched[index]=replaced
    output=final_key(job)
    full_video._merge_original_sound(
        job,segments,stitched,
        destination_override=output,
        force_no_warp=True,motion_aware=False
    )
    full_video._deliver_once(
        job,output,
        delivery_key='telegram_section_revision_v1_delivery',
        caption=(f'Nicole: section {index+1}/{len(segments)} replaced in '
                 f'video {str(parent["job_id"])[:8]}. All other sections reused. '
                 'Original soundtrack restored (480p).')
    )
    LOG.info('SECTION_REDO completed job=%s section=%d original_untouched=true',
             job_id[:8],index+1)
    return 'completed'
