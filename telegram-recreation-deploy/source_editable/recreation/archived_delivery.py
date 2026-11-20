"""User-confirmed resending of an already-archived Nicole MP4.

The old Telegram-by-URL send status is uncertain. The owner must explicitly
request one multipart retry; absolutely no video generation or assembly.
"""
import logging
from . import db,full_video,provider,storage,telegram

LOG=logging.getLogger('archived-delivery')


def run(job):
    if (job.get('stage')!='ARCHIVE_DELIVER'
            or job.get('state') not in ('RUNNING','WAITING_PROVIDER')
            or job.get('character')!='NICOLE'):
        raise provider.NeedsReview('Explicit owner archive-delivery queue required')
    job_id=str(job['job_id'])
    cp=job.get('checkpoint') or {}
    preflight=cp.get('local_preflight') or {}
    if (not preflight.get('complete')
            or preflight.get('source_key')!=job.get('original_key')
            or (cp.get('identity') or {}).get('status')!='ready'):
        raise provider.NeedsReview('Original source or Nicole identity no longer matches')
    steps=db.auto_provider_steps(job_id)
    if (int(job.get('segment_total') or 0)!=len(steps)
            or any(step.get('step_key')!=full_video.step_key(i)
                   or step.get('state')!='COMPLETED'
                   or not step.get('external_request_id')
                   for i,step in enumerate(steps))):
        raise provider.NeedsReview('All paid provider steps must be completed before archive delivery')
    key=full_video.final_key(job_id)
    if not full_video._stored(key):
        raise provider.NeedsReview('Archived final MP4 is not available; no Wan calls made')
    original=f'{full_video.RUN_KEY}_telegram_delivery'
    previous=db.get_step(job_id,original)
    if not previous or previous.get('state') not in ('SUBMITTING','UNCERTAIN','FAILED'):
        raise provider.NeedsReview('Original Telegram delivery state does not authorize resend')
    retry=f'{full_video.RUN_KEY}_telegram_archive_multipart_v1'
    old=db.get_step(job_id,retry)
    if old:
        if old.get('state')=='COMPLETED':
            return 'completed'
        raise provider.NeedsReview(
            'Multipart delivery was already attempted and may have reached Telegram; '
            'check the private chat, no duplicate send is allowed')
    reserved=db.reserve_one_shot(job_id,retry,'telegram',
        {'video_key':key,'purpose':'owner_approved_multipart_archive_delivery'})
    if not reserved:
        raise provider.NeedsReview('Archive delivery already reserved in another worker')
    try:
        result=telegram.send_archived_video(job['chat_id'],key,
            caption='Nicole recreation finished. All saved sections, original soundtrack restored. 480p.')
        complete=db.external_finished(job_id,retry,
            {'message_id':result['message_id'],'delivery':'multipart_archive_v1'})
        if not complete:
            raise provider.NeedsReview(
                'Telegram received archive, but delivery receipt could not be saved; inspect private chat')
    except provider.NeedsReview:
        raise
    except Exception as exc:
        LOG.warning('ARCHIVED_DELIVERY uncertain job=%s error_type=%s reason=%s',
                    job_id[:8],type(exc).__name__,str(exc)[:220])
        raise provider.NeedsReview(
            'Archived video remains safe, but the multipart Telegram send '
            'was not confirmed. Check the private chat; do not regenerate '
            'or resend without checking the delivery record.') from exc
    LOG.info('ARCHIVED_DELIVERY completed=true job=%s multipart=true paid_requests=0 message_id=%s',
             job_id[:8],result['message_id'])
    return 'completed'
