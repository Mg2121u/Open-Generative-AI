import importlib
from unittest.mock import Mock

import pytest

from recreation import telegram_setup as setup


def test_backward_compatible_bot_token(monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.setenv('BOT_TOKEN','testtoken')
    assert setup.bot_token() == 'testtoken'
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','different')
    assert setup.bot_token() == ''


def test_target_requires_exact_staging_url(monkeypatch):
    for url in ['http://telegram-recreation-studio-stage.onrender.com/telegram/webhook',
                'https://evil.example.com/telegram/webhook',
                'https://telegram-recreation-studio-stage.onrender.com/telegram/webhook?bad=1']:
        monkeypatch.setenv('TELEGRAM_WEBHOOK_URL',url)
        assert setup.webhook_target() == ''
    monkeypatch.setenv('TELEGRAM_WEBHOOK_URL',
                       'https://telegram-recreation-studio-stage.onrender.com/telegram/webhook')
    assert setup.webhook_target().endswith('/telegram/webhook')


def test_cannot_take_over_other_webhook(monkeypatch):
    monkeypatch.setenv('BOT_TOKEN','testtoken')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET','testsecret')
    monkeypatch.setenv('TELEGRAM_WEBHOOK_URL',
                       'https://telegram-recreation-studio-stage.onrender.com/telegram/webhook')
    methods=[]
    def api(token,method,payload=None):
        methods.append(method)
        if method == 'getMe': return {'is_bot':True,'id':123}
        if method == 'getWebhookInfo': return {'url':'https://nicole.production.example/telegram/webhook'}
        raise AssertionError('setWebhook must never run')
    monkeypatch.setattr(setup,'_api',api)
    assert setup.register_webhook() == 'blocked_existing_different_webhook'
    assert 'setWebhook' not in methods


def test_registers_verified_webhook_without_dropping_updates(monkeypatch):
    monkeypatch.setenv('BOT_TOKEN','testtoken')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET','testsecret')
    monkeypatch.setenv('TELEGRAM_WEBHOOK_URL',
                       'https://telegram-recreation-studio-stage.onrender.com/telegram/webhook')
    got=[]
    def api(token,method,payload=None):
        got.append((method,payload))
        if method == 'getMe': return {'is_bot':True,'id':123}
        if method == 'getWebhookInfo':
            return {'url':'' if len(got)==2 else 'https://telegram-recreation-studio-stage.onrender.com/telegram/webhook'}
        if method == 'setWebhook': return True
        raise AssertionError(method)
    monkeypatch.setattr(setup,'_api',api)
    assert setup.register_webhook() == 'ok'
    assert [x[0] for x in got]==['getMe','getWebhookInfo','setWebhook','getWebhookInfo']
    assert got[2][1]['drop_pending_updates'] is False
    assert got[2][1]['secret_token'] == 'testsecret'


def test_api_does_not_leak_token_in_errors(monkeypatch):
    import requests
    def fails(*args,**kwargs):
        raise requests.ConnectionError('TOKENLEAK-https://api.telegram.org/botsecret')
    monkeypatch.setattr(setup.requests,'post',fails)
    with pytest.raises(RuntimeError) as exc:
        setup._api('secret','getMe')
    assert 'secret' not in str(exc.value)


def test_health_accepts_legacy_bot_token(monkeypatch):
    for key in ('DATABASE_URL','S3_BUCKET','S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY',
                'TELEGRAM_WEBHOOK_SECRET','BOT_TOKEN'):
        monkeypatch.setenv(key,'test')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN',raising=False)
    from recreation.main import health
    assert health()['configured'] is True
