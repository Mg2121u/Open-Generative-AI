"""No secrets or paid requests required for staging readiness checks."""
import os
from unittest.mock import MagicMock
from recreation import diagnostics,storage

def test_missing_secrets_are_reported_without_network(monkeypatch):
    for key in ('DATABASE_URL','S3_BUCKET','S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY','S3_ENDPOINT_URL'):
        monkeypatch.delenv(key,raising=False)
    assert diagnostics.probe()=={'database':'not_configured','storage':'not_configured'}

def test_s3_client_forces_supabase_path_style(monkeypatch):
    fake=MagicMock()
    monkeypatch.setattr(storage.boto3,'client',fake)
    storage.client()
    kwargs=fake.call_args.kwargs
    assert kwargs['config'].s3['addressing_style']=='path'
    assert kwargs['config'].signature_version=='s3v4'

def test_startup_app_has_health_route():
    from recreation.main import app
    assert any(getattr(r,'path',None)=='/health' for r in app.routes)
