"""Read-only, secret-free checks for isolated staging readiness."""
import logging
import os
from threading import Thread

logger=logging.getLogger('uvicorn.error')
from .telegram_setup import bot_token

def probe():
    """Attempt harmless SQL and S3 bucket existence checks, never returning secrets."""
    result={'database':'not_configured','storage':'not_configured'}
    url=os.getenv('DATABASE_URL')
    if url:
        try:
            import psycopg
            with psycopg.connect(url,connect_timeout=7) as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1 FROM public.recreation_jobs LIMIT 0')
            result['database']='ok'
        except Exception as exc:
            result['database']='failed:'+type(exc).__name__
    if (os.getenv('S3_BUCKET') and os.getenv('S3_ACCESS_KEY_ID')
            and os.getenv('S3_SECRET_ACCESS_KEY') and os.getenv('S3_ENDPOINT_URL')):
        try:
            from . import storage
            storage.client().head_bucket(Bucket=storage.bucket())
            result['storage']='ok'
        except Exception as exc:
            result['storage']='failed:'+type(exc).__name__
    return result

def log_startup_probe():
    try:
        status=probe()
        logger.info('STAGING_READINESS database=%s storage=%s telegram_token_present=%s webhook_secret_present=%s',
            status['database'],status['storage'],bool(bot_token()),
            bool(os.getenv('TELEGRAM_WEBHOOK_SECRET')))
        logger.info('WAN_CANARY_READINESS wavespeed_key_present=%s nicole_reference_count=%s canary_disabled=%s',
            bool(os.getenv('WAVESPEED_API_KEY')),
            sum(bool(v.strip()) for v in os.getenv('NICOLE_IDENTITY_URLS','').split(',')),
            os.getenv('ENABLE_PAID_GENERATION','false').strip().lower()!='true')
    except Exception as exc:
        logger.warning('STAGING_READINESS failed_to_complete exception_type=%s',type(exc).__name__)

def schedule_startup_probe():
    Thread(target=log_startup_probe,name='staging-readiness',daemon=True).start()
