import os
import logging
import subprocess
import sys
from fastapi import FastAPI
from .telegram import router
from .diagnostics import schedule_startup_probe
from .telegram_setup import bot_token, schedule_registration

app=FastAPI(title='Telegram Recreation Studio',docs_url=None,redoc_url=None)
app.include_router(router)

@app.get('/health')
def health():
    required=('DATABASE_URL','S3_BUCKET','S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY',
              'TELEGRAM_WEBHOOK_SECRET')
    return {'service':'telegram-recreation-app','configured':all(os.getenv(v) for v in required) and bool(bot_token()),
            'generation_ready':os.getenv('ENABLE_PAID_GENERATION','false').lower()=='true','status':'app_enabled' if os.getenv('ENABLE_PAID_GENERATION','false').lower()=='true' else 'preflight_only'}

@app.on_event('startup')
def _staging_probe():
    schedule_startup_probe()
    schedule_registration()
    # One-off, non-paid archiving smoke test in a distinct subprocess.
    # Disabled by default and NOT a replacement for a dedicated always-on worker.
    if os.getenv('STAGING_INGEST_ON_BOOT','false').lower() == 'true':
        try:
            subprocess.Popen([sys.executable, '-m', 'recreation.ingest_once'],
                             stdout=None, stderr=None, close_fds=True)
            logging.getLogger('uvicorn.error').info('STAGING_INGEST subprocess_started=true')
        except Exception as exc:
            logging.getLogger('uvicorn.error').warning('STAGING_INGEST subprocess_failed=%s', type(exc).__name__)

