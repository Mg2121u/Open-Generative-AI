"""One-shot private staging Nicole identity image archive. Never invokes paid AI."""
import logging,os,sys,uuid
from . import db, storage, telegram
LOGGER=logging.getLogger('identity-ingest')


def ingest_identity(job_id):
    job=db.get_job(job_id)
    allowed={x.strip() for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip()}
    if not job or str(job.get('owner_id')) not in allowed or job.get('state')!='DRAFT' or job.get('character')!='NICOLE':
        LOGGER.warning('IDENTITY_INGEST ignored_unauthorized_or_unready=true')
        return False
    ident=(job.get('checkpoint') or {}).get('identity') or {}
    file_id=ident.get('nicole_file_id')
    if not file_id or ident.get('status') != 'pending':
        return False
    try:
        key=storage.store_telegram_identity(job_id,file_id)
        saved=db.mark_identity_ready(job_id,file_id,key)
        if not saved:
            return False
        LOGGER.info('IDENTITY_INGEST archived=true job=%s',str(job_id)[:8])
        telegram.say(job['chat_id'],'Nicole identity reference archived privately. /status now shows Identity: ready. Paid generation remains disabled.')
        return True
    except Exception as exc:
        LOGGER.warning('IDENTITY_INGEST archived=false error_type=%s job=%s',type(exc).__name__,str(job_id)[:8])
        db.mark_identity_failed(job_id,file_id)
        try:telegram.say(job['chat_id'],'Nicole identity photo could not be archived. Please resend a clear JPEG/PNG photo (max 10 MB) with caption /identity. No AI was run.')
        except Exception: pass
        return False


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv)==2:
        ingest_identity(sys.argv[1])
