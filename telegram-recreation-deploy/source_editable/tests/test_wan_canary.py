import pytest
from recreation import wan_canary

def test_canary_budget():
    assert wan_canary.estimated_cost(3.03) == 0.35
    assert wan_canary.estimated_cost(4.01) == 0.40
    assert wan_canary.estimated_cost(5.01) > 0.40


def test_payload_is_one_reference_480p_silent():
    p = wan_canary.payload_for('https://example.org/video.mp4', 352, 624, ['https://example.org/nicole.jpg'])
    assert p['reference_videos'] == ['https://example.org/video.mp4']
    assert p['reference_images'] == ['https://example.org/nicole.jpg']
    assert 'replace the original performer' in p['prompt']
    assert 'Keep the subject' not in p['prompt']
    assert p['aspect_ratio']=='9:16'
    assert p['duration']==3 and p['resolution']=='480p'
    assert p['generate_audio'] is False


def test_canary_fails_closed_without_switch(monkeypatch):
    monkeypatch.setenv('WORKER_MODE', 'canary_once')
    monkeypatch.delenv('WAN_CANARY_ONE_SHOT', raising=False)
    with pytest.raises(RuntimeError, match='explicit one-shot switch'):
        wan_canary.require_approval()


def test_canary_fails_closed_without_max_budget(monkeypatch):
    monkeypatch.setenv('WORKER_MODE','canary_once')
    monkeypatch.setenv('WAN_CANARY_ONE_SHOT','true')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.delenv('WAN_CANARY_MAX_USD',raising=False)
    with pytest.raises(RuntimeError, match='budget'):
        wan_canary.require_approval()


def test_canary_does_not_submit_when_already_reserved(monkeypatch):
    monkeypatch.setattr(wan_canary, 'require_approval', lambda: ({'job_id':'j1'}, 'reference0'))
    monkeypatch.setattr(wan_canary.db, 'get_step', lambda *args: {'state':'SUBMITTING'})
    monkeypatch.setattr(wan_canary.provider, 'submit_once', lambda *args: pytest.fail('duplicate provider submission'))
    with pytest.raises(wan_canary.provider.NeedsReview):
        wan_canary.run_once()


def test_missing_identity_photo_refuses_payload():
    with pytest.raises(ValueError,match='mandatory'):
        wan_canary.payload_for('https://example.org/video.mp4',352,624,[])


def test_canary_archive_uses_identity_specific_key(monkeypatch):
    """A prior provider-only output must never suppress the Nicole result."""
    from recreation import provider
    job_id='job-test'
    monkeypatch.setattr(wan_canary, 'require_approval', lambda: ({'job_id':job_id}, 'old-first'))
    monkeypatch.setattr(wan_canary.db, 'get_step', lambda *args: {'job_id':job_id, 'step_key':wan_canary.STEP_KEY, 'state':'COMPLETED', 'result':{'outputs':['https://example.org/new-nicole-result.mp4']}})
    keys=[]
    class NotThere:
        def head_object(self, **kw):
            keys.append(kw['Key'])
            raise FileNotFoundError()
    monkeypatch.setattr(wan_canary.storage, 'client', lambda: NotThere())
    monkeypatch.setattr(wan_canary.storage, 'bucket', lambda: 'test-bucket')
    monkeypatch.setattr(wan_canary.storage, 'store_provider_url', lambda u,k: keys.append(k))
    assert wan_canary.run_once() == 'completed'
    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert keys[0].endswith('/canary/wan_nicole_identity_canary_480p_20261115_002.mp4')
    assert not keys[0].endswith('/canary/output_480p.mp4')
