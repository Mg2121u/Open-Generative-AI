"""Staging-only single-shot Telegram -> durable private S3 ingestion.

This module NEVER calls WaveSpeed, ElevenLabs, or paid APIs. It is safe to run
as a short-lived *separate process* on the existing staging web instance for a
one-off smoke test. Long-term production requires a dedicated background worker.
"""
import logging
import os
import uuid
from . import db, storage, telegram

logger = logging.getLogger('recreation-ingest')


def allowed_owner_ids():
    return [int(x.strip()) for x in os.environ.get('ALLOWED_TELEGRAM_USER_IDS', '').split(',')
            if x.strip().isdigit()]


def claim_ingest(worker_id, owner_ids):
    """Claim a draft or an abandoned ingestion lease without touching paid stages."""
    if not owner_ids:
        return None
    with db.connect() as conn:
        return conn.execute('''WITH selected AS (
          SELECT job_id FROM recreation_jobs
           WHERE owner_id = ANY(%s::bigint[])
             AND source_file_id IS NOT NULL AND original_key IS NULL
             AND ((state = 'DRAFT' AND stage = 'UPLOAD')
               OR (state = 'RUNNING' AND stage = 'INGEST'
                   AND (lease_until IS NULL OR lease_until < now())))
           ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE recreation_jobs j
           SET state='RUNNING',stage='INGEST',lease_owner=%s,
               lease_until=now()+interval '5 minutes', updated_at=now()
          FROM selected WHERE j.job_id=selected.job_id RETURNING j.*''',
          (owner_ids, worker_id)).fetchone()


def ingest_one(job, worker_id):
    """Store a video and checkpoint it BEFORE notifying Telegram."""
    job_id = job['job_id']
    try:
        key = storage.store_telegram(job_id, job['source_file_id'])
        updated = db.checkpoint(job_id, worker_id, stage='UPLOAD_REFERENCE', state='DRAFT',
             fields={'original_key':key, 'last_error':None},
             event=('source_saved','Original source retained in private object storage'))
        if not updated:
            raise RuntimeError('Checkpoint rejected (lease expired or job cancelled)')
        logger.info('STAGING_INGEST result=ok job=%s', str(job_id)[:8])
        try:
            telegram.say(job['chat_id'], 'Your original video is safely archived. /status shows the checkpoint. Paid generation remains disabled.')
        except Exception:
            logger.warning('STAGING_INGEST notification_failed job=%s', str(job_id)[:8])
        return True
    except Exception as exc:
        logger.warning('STAGING_INGEST result=failed kind=%s job=%s', type(exc).__name__, str(job_id)[:8])
        # Never expose the secret-bearing request URL or exception message in logs or DB.
        try:
            db.checkpoint(job_id, worker_id, stage='UPLOAD', state='DRAFT',
                 fields={'last_error':'Source archiving failed: '+type(exc).__name__},
                 event=('source_archive_failed','Storage backup not confirmed; original Telegram file reference preserved'))
        except Exception:
            logger.exception('STAGING_INGEST could_not_checkpoint_failure job=%s', str(job_id)[:8])
        return False


def run(max_jobs=2):
    owner_ids = allowed_owner_ids()
    if not owner_ids:
        logger.warning('STAGING_INGEST no_allowed_owner_ids')
        return 0
    worker_id='staging-ingest-'+uuid.uuid4().hex[:8]
    count = 0
    for _ in range(max_jobs):
        job = claim_ingest(worker_id, owner_ids)
        if not job:
            break
        if ingest_one(job, worker_id):
            count += 1
    logger.info('STAGING_INGEST finished stored=%s', count)
    return count


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
