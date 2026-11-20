"""Persistent job dispatcher. No in-process long-lived n8n execution."""
import logging
from .voiceover import VoiceConfigurationError
import os
import threading
import time
import uuid
from . import db,storage,telegram,provider,pipeline,preflight

logger=logging.getLogger('recreation-worker')
WORKER_ID='worker-'+uuid.uuid4().hex[:8]


def _heartbeat(job_id,stop):
    while not stop.wait(20):
        try: db.heartbeat(job_id,WORKER_ID)
        except Exception: logger.exception('Could not renew lease')


def process_job(job):
    job_id=job['job_id']; stage=job['stage']; chat=job['chat_id']
    if stage==preflight.STAGE:
        return preflight.run_step(job)
    if stage=='INGEST':
        key=storage.store_telegram(job_id,job['source_file_id'])
        db.checkpoint(job_id,WORKER_ID,stage='UPLOAD_REFERENCE',state='QUEUED',fields={'original_key':key},
              event=('source_saved','Original video safely backed up to durable media storage'))
        telegram.say(chat,'Original video saved safely. Your source will not need to be uploaded again.')
        return
    return pipeline.process(job)



def loop():
    logging.basicConfig(level=logging.INFO)
    mode=os.getenv('WORKER_MODE','preflight_only').strip().lower()
    if mode not in ('preflight_only','paid_pipeline','canary_once','full_video_once','motion_remux_once','app_always_on'):
        raise RuntimeError('Unrecognized WORKER_MODE')
    if mode=='canary_once':
        # Never enter generic job claims; this is a bounded single provider submission.
        from .wan_canary import run_once
        logging.info('WAN_CANARY isolated_worker_mode=true')
        run_once()
        return
    if mode=='motion_remux_once':
        from .motion_remux import run_once
        logging.info('MOTION_REMUX isolated_worker_mode=true paid_generation_disabled=true')
        run_once()
        while True:time.sleep(300)
    if mode=='full_video_once':
        from .full_video import run_once
        logging.info('FULL_VIDEO isolated_worker_mode=true')
        while True:
            status=run_once()
            if status=='completed':
                logging.info('FULL_VIDEO completed=true paid_submission_loop_exited=true')
                # Keep the service healthy while an operator disables paid mode.
                while True: time.sleep(300)
            if status!='processing':
                raise RuntimeError('Unexpected full-video stage result')
            time.sleep(12)
    if mode in ('paid_pipeline','app_always_on') and os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
        raise RuntimeError('Paid pipeline mode is not enabled')
    # Database schema was already migrated and verified in the isolated Supabase project.
    # Workers must not run DDL on every restart.
    with db.connect() as conn: conn.execute('SELECT 1')
    logger.info('WORKER_BOOT mode=%s paid_generation_enabled=%s', mode, mode in ('paid_pipeline','app_always_on'))
    last_review=0
    while True:
        try:
            if mode in ('paid_pipeline','app_always_on') and time.monotonic()-last_review>60:
                db.review_uncertain_submissions()
                last_review=time.monotonic()
            job=(db.claim_local_preflight(WORKER_ID) if mode=='preflight_only'
                 else db.claim_app(WORKER_ID) if mode=='app_always_on'
                 else db.claim(WORKER_ID))
            if not job:
                time.sleep(3); continue
            stop=threading.Event()
            heartbeat=threading.Thread(target=_heartbeat,args=(job['job_id'],stop),daemon=True)
            heartbeat.start()
            try:
                if mode=='app_always_on':
                    from . import app_queue
                    app_queue.process(job)
                else:
                    process_job(job)
            except (provider.NeedsReview,pipeline.ConfigurationError,VoiceConfigurationError) as exc:
                logger.warning('Manual review required at stage %s for job %s: %s',job['stage'],job['job_id'],exc)
                db.checkpoint(job['job_id'],WORKER_ID,state='NEEDS_REVIEW',
                    fields={'last_error':str(exc)[:650]},
                    event=('manual_review',str(exc)[:300]))
                try:
                    reason=str(exc)
                    if (job.get('stage')=='AUTO_GENERATE'
                            and (reason.startswith('Nicole facial identity QA mismatch:')
                                 or reason.startswith('Nicole facial identity QA unverified:'))):
                        short=str(job['job_id'])[:8]
                        telegram.say(job['chat_id'],
                            f'Nicole face QA flagged an already-paid section on job {short}. '
                            'The generated clip is archived. '
                            f'Preview it with /review_qa {short}. '
                            f'If you accept the imperfect face, send /approve_qa {short} '
                            'to deliver the single section or resume remaining sections. '
                            'Any remaining new sections may incur their normal Wan cost; '
                            'existing paid sections will not be regenerated. '
                            'Provider request IDs and failed-QA evidence are preserved.')
                    else:
                        telegram.say(job['chat_id'],
                            'Job paused for a provider or processing safety review. '
                            'Existing provider request IDs are preserved. '
                            'Use /status for the precise issue. '
                            'Uncertain paid submissions cannot be retried by a generic approval.')
                except Exception: logger.exception('Could not notify user')
            except Exception as exc:
                logger.exception('Worker stage failed: stage=%s job=%s error_type=%s',job['stage'],str(job['job_id'])[:8],type(exc).__name__)
                attempts=int(job['attempt'])+1
                retry=attempts<=5
                delay=min(900,15*2**(attempts-1))
                db.checkpoint(job['job_id'],WORKER_ID,state='QUEUED' if retry else 'FAILED',
                    delay_seconds=delay if retry else 0,
                    fields={'last_error':'Processing stage failed: '+type(exc).__name__,'attempt':attempts},
                    event=('retry' if retry else 'failed','Processing stage failed: '+type(exc).__name__))
            finally:
                stop.set(); heartbeat.join(timeout=2)
        except Exception:
            logger.exception('Worker loop exception')
            time.sleep(8)


if __name__=='__main__': loop()
