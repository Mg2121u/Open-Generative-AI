"""One-shot owner-only staging scene photo ingest. No paid providers."""
import logging
import os
import sys

from . import db,storage,telegram

LOGGER=logging.getLogger('scenery-ingest')


def ingest_scenery(job_id):
    job=db.get_job(job_id)
    allowed={part.strip() for part in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',')
             if part.strip()}
    if (not job or str(job.get('owner_id')) not in allowed
            or job.get('state')!='DRAFT'):
        LOGGER.warning('SCENERY_INGEST ignored_unauthorized_or_unready=true')
        return False
    scene=((job.get('checkpoint') or {}).get('scenery_reference') or {})
    file_id=scene.get('file_id')
    if not file_id or scene.get('status')!='pending':
        return False
    try:
        key=storage.store_telegram_scenery(job_id,file_id)
        saved=db.mark_scenery_ready(job_id,file_id,key)
        if not saved:
            return False
        LOGGER.info('SCENERY_INGEST archived=true job=%s',str(job_id)[:8])
        telegram.say(job['chat_id'],
            'Scenery reference photo archived privately. This picture controls '
            'the background/location, NOT Nicole\'s face, body or clothing. '
            'Check /status; when Scenery reference: ready, use /generate '
            'or /confirm_segment. No paid AI call was made.')
        return True
    except Exception as exc:
        LOGGER.warning('SCENERY_INGEST archived=false error_type=%s job=%s',
                       type(exc).__name__,str(job_id)[:8])
        db.mark_scenery_failed(job_id,file_id)
        try:
            telegram.say(job['chat_id'],
                'Scenery photo could not be archived. Please resend JPEG, PNG or '
                'WEBP (max 10 MB) with caption /scenery. No AI was called.')
        except Exception:
            LOGGER.exception('Could not notify owner of scenery archive failure')
        return False


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv)==2:
        ingest_scenery(sys.argv[1])
