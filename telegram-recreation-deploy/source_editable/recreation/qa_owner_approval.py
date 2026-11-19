"""Owner-requested delivery of one completed Wan clip that FAILED face QA.

The QA result is retained and shown as a warning. This NEVER resubmits,
regenerates or charges for a Wan prediction; it simply restores the original
soundtrack to an already paid video and sends it in the private Telegram DM.
"""
import logging
from . import db,full_video,quality,provider

LOG=logging.getLogger('qa-owner-approval')


def run(job):
    if job.get('state') not in ('RUNNING','WAITING_PROVIDER'):
        raise provider.NeedsReview('QA approval has not been explicitly queued')
    if job.get('stage')!='QA_OWNER_APPROVED' or job.get('character')!='NICOLE':
        raise provider.NeedsReview('Invalid owner-approval stage')
    job_id=str(job['job_id'])
    cp=job.get('checkpoint') or {}
    override=cp.get('quality_override') or {}
    if override.get('type')!='owner_accepted_facial_mismatch':
        raise provider.NeedsReview('Missing explicit owner approval')
    if override.get('owner_id')!=job.get('owner_id'):
        raise provider.NeedsReview('Owner mismatch on QA approval')
    if not str(override.get('original_failure','')).startswith('Nicole facial identity QA mismatch:'):
        raise provider.NeedsReview('Original facial mismatch was not verified')
    if override.get('source_step')!=full_video.step_key(0):
        raise provider.NeedsReview('Approved provider step does not match archived video')
    if job.get('segment_total')!=1:
        raise provider.NeedsReview('QA override only supports one previously paid section')
    local=(cp.get('local_preflight') or {})
    if not local.get('complete') or local.get('source_key')!=job.get('original_key'):
        raise provider.NeedsReview('Original source was not verified')
    planned=quality.quality_plan((local.get('source_probe') or {}).get('duration'))
    if len(planned)!=1:
        raise provider.NeedsReview('Only exactly one completed source interval may be approved')
    step=db.get_step(job_id,full_video.step_key(0))
    if not step or step.get('state')!='COMPLETED':
        raise provider.NeedsReview('Paid segment is not confirmed completed')
    key=full_video.segment_key(job_id,0)
    if not full_video._stored(key):
        raise provider.NeedsReview('Paid segment archive missing; cannot approve delivery')
    # Deliberately do NOT call full_video.run_auto or identity_qa.check_archived:
    # this is explicit human override of an ALREADY FAILED, immutable QA result.
    result=full_video._merge_original_sound(
        job,planned,[key],force_no_warp=True,motion_aware=False)
    full_video._deliver_once(
        job,result,
        delivery_key=f'{full_video.RUN_KEY}_owner_qa_override_delivery_v1',
        caption=(
            'Owner-approved QA exception: Nicole face match FAILED. '
            'This already-paid 480p clip is delivered as generated; the '
            'appearance is not identity-verified. Original audio restored. '
            'No new Wan generation was made.'
        ))
    LOG.info('OWNER_QA_OVERRIDE job=%s provider_calls=0 original_QA_not_changed=true delivered=true',
             job_id[:8])
    return 'completed'
