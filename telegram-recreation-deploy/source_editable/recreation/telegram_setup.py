"""Conservative webhook registration for the independent staging bot.

Never overwrite a webhook already pointing at a different application.
Never log the token or Telegram API URLs (which contain the token).
"""
import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger('uvicorn.error')


def bot_token():
    canonical = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    legacy = os.getenv('BOT_TOKEN', '').strip()
    if canonical and legacy and canonical != legacy:
        return ''  # A conflicting secret is never resolved by guessing.
    return canonical or legacy


def webhook_target():
    url = os.getenv('TELEGRAM_WEBHOOK_URL', '').strip()
    if not url:
        return ''
    p = urlparse(url)
    if (p.scheme != 'https' or p.netloc != 'telegram-recreation-studio-stage.onrender.com'
            or p.path != '/telegram/webhook' or p.query or p.fragment or p.username or p.password):
        return ''
    return url


def _api(token, method, payload=None):
    base = os.getenv('TELEGRAM_BOT_API_URL', 'https://api.telegram.org').rstrip('/')
    try:
        response = requests.post(f'{base}/bot{token}/{method}', json=payload or {}, timeout=15)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        # The underlying HTTP error normally includes the token-bearing URL.
        raise RuntimeError('telegram_api_' + type(exc).__name__) from None
    if not body.get('ok'):
        raise RuntimeError('telegram_api_rejected')
    return body.get('result')


def register_webhook():
    token = bot_token()
    secret = os.getenv('TELEGRAM_WEBHOOK_SECRET', '').strip()
    target = webhook_target()
    if not token or not secret or not target:
        return 'not_configured'
    try:
        identity = _api(token, 'getMe')
        if not isinstance(identity, dict) or not identity.get('is_bot'):
            return 'token_not_bot'
        current = _api(token, 'getWebhookInfo')
        existing_url = (current or {}).get('url', '')
        if existing_url and existing_url != target:
            return 'blocked_existing_different_webhook'
        _api(token, 'setWebhook', {
            'url': target,
            'secret_token': secret,
            'allowed_updates': ['message'],
            'drop_pending_updates': False,
        })
        checked = _api(token, 'getWebhookInfo')
        return 'ok' if (checked or {}).get('url') == target else 'verification_failed'
    except Exception as exc:
        # Only the exception's class is exposed; never its message/URL.
        logger.warning('STAGING_TELEGRAM error_class=%s', type(exc).__name__)
        return 'error'


def schedule_registration():
    from threading import Thread
    def task():
        state = register_webhook()
        logger.info('STAGING_TELEGRAM webhook=%s token_present=%s secret_present=%s allowed_user_ids_present=%s',
                    state, bool(bot_token()), bool(os.getenv('TELEGRAM_WEBHOOK_SECRET')),
                    bool(os.getenv('ALLOWED_TELEGRAM_USER_IDS')))
    Thread(target=task, daemon=True, name='telegram-staging-registration').start()
