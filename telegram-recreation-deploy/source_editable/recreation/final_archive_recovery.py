"""Local-only recovery from S3 EntityTooLarge after ALL Wan sections finished.

The paid provider record table is strictly read-only in this path. Final
joining, storage-size-controlled export, and Telegram delivery are the
only actions. The original audio and existing 15 owner QA overrides remain.
"""
import logging

from . import db,full_video,provider,quality

LOG=logging.getLogger('final-archive-recovery')


def run(job):
    if (job.get('stage')!='FINAL_ASSEMBLE'
            or job.get('state') not in ('RUNNING','WAITING_PROVIDER')
            or job.get('character')!='NICOLE'):
        raise provider.NeedsReview('Final archive recovery requires an explicit owner request')
    job_id=str(job['job_id'])
    cp=job.get('checkpoint') or {}
    source=cp.get('local_preflight') or {}
    if (not source.get('complete')
            or source.get('source_key')!=job.get('original_key')
            or (cp.get('identity') or {}).get('status')!='ready'):
        raise provider.NeedsReview('Original video or Nicole reference has changed')
    plan=quality.quality_plan((source.get('source_probe') or {}).get('duration'))
    if not plan or len(plan)!=int(job.get('segment_total') or 0):
        raise provider.NeedsReview('Saved segment plan changed; cannot locally finalize')
    steps=db.auto_provider_steps(job_id)
    if len(steps)!=len(plan):
        raise provider.NeedsReview('Cannot finalize unless EVERY paid section is completed')
    for i,step in enumerate(steps):
        if (step['step_key']!=full_video.step_key(i)
                or step['state']!='COMPLETED'
                or not step.get('external_request_id')):
            raise provider.NeedsReview('Provider step is not safely finished; no new generation attempted')
    keys=[full_video.segment_key(job_id,i) for i in range(len(plan))]
    if not full_video._stored(job['original_key']):
        raise provider.NeedsReview('Original source video archive is unavailable')
    if any(not full_video._stored(key) for key in keys):
        raise provider.NeedsReview('One of the existing paid section videos is missing')
    output=full_video._merge_original_sound(
        job,plan,keys,motion_aware=True,force_no_warp=True)
    full_video._deliver_once(job,output)
    LOG.info('FINAL_ASSEMBLE completed=true job=%s sections_reused=%d paid_requests=0',
             job_id[:8],len(keys))
    return 'completed'
