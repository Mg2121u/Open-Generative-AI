"""Owner-triggered local background remake from ALREADY PAID video archive.

No WaveSpeed calls. The previous completed job remains untouched. The new
output is a private preview that preserves original video timing and audio.
"""
import json
import logging
import tempfile
from pathlib import Path

from . import db,full_video,identity_qa,provider,scenery_lock,storage

LOG=logging.getLogger('scenery-repair')


def repaired_key(job_id):
    return f'recreation/{job_id}/scene_fix/{scenery_lock.OUTPUT_VERSION}/nicole_fixed_scenery.mp4'


def run(job):
    if job.get('stage')!='SCENE_FIX' or job.get('state') not in ('RUNNING','WAITING_PROVIDER'):
        raise provider.NeedsReview('Scenery preview must be explicitly requested by the owner')
    if job.get('character')!='NICOLE':
        raise provider.NeedsReview('Only Nicole scenery preview is supported')
    cp=job.get('checkpoint') or {}
    instruction=cp.get('scene_repair') or {}
    parent_id=instruction.get('parent_job_id')
    if not parent_id or instruction.get('parent_run_key')!=full_video.RUN_KEY:
        raise provider.NeedsReview('Parent video namespace mismatch')
    parent=db.get_job(parent_id,job['owner_id'])
    if not parent or parent.get('state')!='COMPLETED' or parent.get('stage')!='DONE':
        raise provider.NeedsReview('Completed source video not found')
    parent_cp=parent.get('checkpoint') or {}
    scene=(parent_cp.get('scenery_reference') or {})
    if (scene.get('status')!='ready' or not scene.get('scenery_image_key')
            or scene != cp.get('scenery_reference')):
        raise provider.NeedsReview('The saved scene reference is missing or changed')
    identity=(parent_cp.get('identity') or {}).get('nicole_image_key')
    if not identity or identity!=(cp.get('identity') or {}).get('nicole_image_key'):
        raise provider.NeedsReview('Nicole original identity did not match')
    source_key=full_video.final_key(str(parent['job_id']))
    scene_key=scene['scenery_image_key']
    dest=repaired_key(str(job['job_id']))
    if not all(full_video._stored(x) for x in (source_key,scene_key)):
        raise provider.NeedsReview('Previously archived video or scene photo is missing')
    if not full_video._stored(dest):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);video=root/'already_paid.mp4';photo=root/'background.jpg'
            revised=root/'strict_scene_fixed.mp4'
            storage.download_key(source_key,video)
            storage.download_key(scene_key,photo)
            metrics=scenery_lock.render(video,photo,revised)
            LOG.info('SCENE_FIX output_ready job=%s frames=%d uncertain_masks=%d '
                     'no_paid_calls=true',str(job['job_id'])[:8],
                     metrics['frames'],metrics['uncertain_mask_frames'])
            storage.upload_key(dest,revised)
    # Fail closed if the person matte accidentally damages Nicole's face.
    identity_qa.check_archived(job,dest)
    full_video._deliver_once(
        job,dest,
        delivery_key='owner_scene_fix_preview_local_matte_v1',
        caption=('LOCAL SCENERY REPAIR PREVIEW — no Wan generation or new charge. '
                 'The scenery photo is fixed behind Nicole, rather than blended '
                 'by an AI generator. Original soundtrack and video timing '
                 'preserved. Check the subject edges, lighting and movement; '
                 'this is a preview, not a guaranteed final-quality composite.'))
    LOG.info('SCENE_FIX delivered=true job=%s source_original_unchanged=true '
             'provider_calls=0',str(job['job_id'])[:8])
    return 'completed'
