"""Always-on Telegram recreation queue; owner only and no recurring-job quota.

Only an explicit /generate places a job in this queue. All paid submissions
are reserved in recreation_steps, preventing duplicate charges after restart.
"""
import logging
import tempfile
from pathlib import Path
from . import db,ffmpeg,storage,telegram,full_video,preflight,segment_revision,qa_owner_approval

LOG=logging.getLogger('app-queue')


def _next(job, *, stage, state='QUEUED', checkpoint=None, segment_total=None, current_segment=None, delay=0, event=None):
    fields={'attempt':0,'last_error':None}
    if checkpoint is not None:fields['checkpoint']=checkpoint
    if segment_total is not None:fields['segment_total']=segment_total
    if current_segment is not None:fields['current_segment']=current_segment
    return db.checkpoint(job['job_id'],job['lease_owner'],stage=stage,state=state,
                         fields=fields,delay_seconds=delay,event=event)


def process(job):
    job_id=str(job['job_id']);stage=job['stage']
    if stage==preflight.STAGE:
        return preflight.run_step(job)
    if stage=='INGEST':
        if not job.get('source_file_id'):
            raise ValueError('Source MP4 file is missing')
        key=job.get('original_key') or storage.store_telegram(job_id,job['source_file_id'])
        result=db.checkpoint(job['job_id'],job['lease_owner'],stage='AUTO_PREFLIGHT',state='QUEUED',
                             fields={'original_key':key,'attempt':0,'last_error':None},
                             event=('source_saved','Original media archived before processing'))
        if result:telegram.say(job['chat_id'],'Source archived. Verifying video, identity, and estimated generation cost.')
        return
    if stage in ('UPLOAD_REFERENCE','AUTO_PREFLIGHT'):
        key=job.get('original_key')
        if not key:raise ValueError('Archived source video is missing')
        identity=((job.get('checkpoint') or {}).get('identity') or {})
        if identity.get('status')!='ready' or not identity.get('nicole_image_key'):
            raise full_video.provider.NeedsReview('Nicole reference photo not ready; attach /identity on a fresh draft')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'source.mp4'
            storage.download_key(key,path)
            probe=ffmpeg.probe(path)
        cp=dict(job.get('checkpoint') or {})
        cp['local_preflight']={'source_key':key,'source_probe':probe,'complete':True,
            'mode':'automatic_source_validation'}
        verified=dict(job)
        verified['checkpoint']=cp
        plan=full_video.report(verified)
        # Validate the estimate before enqueuing ANY paid Wan request.
        # Rechecked again immediately before each paid submission.
        import math,os
        try: cap=float(os.getenv('APP_MAX_ESTIMATED_USD','10.00'))
        except (ValueError,TypeError): raise full_video.provider.NeedsReview('Per-video spending cap configuration invalid') from None
        if not math.isfinite(cap) or not (0 < cap <= 100):
            raise full_video.provider.NeedsReview('Per-video spending cap configuration invalid')
        if plan['remaining_estimate_usd'] > cap:
            raise full_video.provider.NeedsReview(
                f'Estimated cost ${plan["remaining_estimate_usd"]:.2f} exceeds per-video limit '
                f'${cap:.2f}. No paid generation submitted.')
        result=_next(job,stage='AUTO_GENERATE',checkpoint=cp,segment_total=len(plan['segments']),
                     event=('app_preflight',f'Source verified: {plan["source_duration"]:.1f} seconds, {len(plan["segments"])} segments'))
        if result:
            telegram.say(job['chat_id'],f'Nicole generation started. {len(plan["segments"])} segments, '
                f'estimated additional WaveSpeed charge ${plan["remaining_estimate_usd"]:.2f} '
                '(actual provider billing may vary). Original audio will be preserved. /status tracks progress.')
        return
    if stage=='SALVAGE':
        # Salvage is local remux only; NEVER invoke the Wan run_auto path.
        outcome=full_video.salvage_existing_v3(job)
        if outcome!='completed':
            raise RuntimeError('Unexpected local-only salvage result')
        done=_next(job,stage='DONE',state='COMPLETED',
                   current_segment=job['segment_total'],
                   event=('local_salvage','Recovered existing paid clips without provider generation'))
        if done:LOG.info('LOCAL_SALVAGE completed=true job=%s provider_calls=0',job_id[:8])
        return
    if stage=='QA_OWNER_APPROVED':
        status=qa_owner_approval.run(job)
        if status!='completed':
            raise RuntimeError('Local QA-owner-approval did not complete')
        done=_next(job,stage='DONE',state='COMPLETED',current_segment=1,
                   event=('owner_qa_accepted',
                          'Owner accepted failed face check and received archived clip without new generation'))
        if done:LOG.info('OWNER_QA_OVERRIDE completed job=%s provider_calls=0',job_id[:8])
        return
    if stage=='SEGMENT_REDO':
        status=segment_revision.run(job)
        if status=='processing':
            _next(job,stage='SEGMENT_REDO',state='WAITING_PROVIDER',
                  current_segment=0,delay=12)
        elif status=='completed':
            done=_next(job,stage='DONE',state='COMPLETED',current_segment=1,
                       event=('section_replaced','One section replaced, previous sections reused, owner video delivered'))
            if done:LOG.info('SECTION_REDO completed=true job=%s',job_id[:8])
        else:
            raise RuntimeError('Unexpected single-section revision stage')
        return
    if stage=='AUTO_GENERATE':
        status=full_video.run_auto(job)
        progress=db.count_auto_segments(job['job_id'])
        if status=='processing':
            _next(job,stage='AUTO_GENERATE',state='WAITING_PROVIDER',current_segment=progress,delay=12)
        elif status=='completed':
            done=_next(job,stage='DONE',state='COMPLETED',current_segment=job['segment_total'],event=('finished','Recreated video delivered in Telegram'))
            if done:LOG.info('APP_RECREATION completed=true job=%s',job_id[:8])
        else:
            raise RuntimeError('Unexpected automatic recreation stage')
        return
    raise RuntimeError('Unknown recreation app stage')
