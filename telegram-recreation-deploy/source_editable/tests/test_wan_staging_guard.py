"""Wan 3.0 integration safety checks: zero external requests unless explicitly enabled.

Every HTTP response here is a local fake; these tests do not require credentials
or incur generation charges.
"""
import pytest
from recreation import provider, pipeline

ENDPOINT = 'alibaba/wan-3.0/reference-to-video'
PAYLOAD = {
    'prompt': 'Preserve reference choreography with identity locked',
    'reference_videos': ['https://example.com/clip.mp4'], 'reference_images': ['https://example.com/nicole.jpg'],
    'resolution': '480p', 'duration': 4, 'enable_audio': False,
}

class Response:
    def __init__(self, payload): self.payload=payload
    def raise_for_status(self): pass
    def json(self): return self.payload


def test_zero_wavespeed_calls_while_staging_disabled(monkeypatch):
    monkeypatch.delenv('ENABLE_PAID_GENERATION',raising=False)
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.db,'get_step',lambda *a:None)
    monkeypatch.setattr(provider.db,'prepare_external',lambda *a: pytest.fail('Reserved a paid request while disabled'))
    monkeypatch.setattr(provider.requests,'post',lambda *a,**k: pytest.fail('Made a paid HTTP POST while disabled'))
    with pytest.raises(RuntimeError,match='disabled'):
        provider.submit_once('job','wan_segment_000',ENDPOINT,PAYLOAD)


def test_single_atomic_reservation_refuses_second_worker(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.db,'get_step',lambda *a:None)
    monkeypatch.setattr(provider.db,'prepare_external',lambda *a:None)
    monkeypatch.setattr(provider.requests,'post',lambda *a,**k: pytest.fail('Second worker made duplicate POST'))
    with pytest.raises(provider.NeedsReview,match='Another worker'):
        provider.submit_once('job','wan_segment_000',ENDPOINT,PAYLOAD)


def test_wan_submit_accepts_200_and_saves_prediction_id(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.db,'get_step',lambda *a:None)
    recorded=[]
    monkeypatch.setattr(provider.db,'prepare_external',lambda *args: {'state':'SUBMITTING'})
    def fake_post(url, **kwargs):
        recorded.append((url,kwargs['json']))
        return Response({'code':200,'data':{'id':'pred_local_simulated_123','status':'created'}})
    monkeypatch.setattr(provider.requests,'post',fake_post)
    monkeypatch.setattr(provider.db,'external_submitted',lambda job,step,id:{'state':'SUBMITTED','external_request_id':id})
    result=provider.submit_once('job','wan_segment_000',ENDPOINT,PAYLOAD)
    assert result['external_request_id']=='pred_local_simulated_123'
    assert recorded==[(provider.WAVESPEED_URL+'/'+ENDPOINT,PAYLOAD)]


def test_semantic_api_error_must_not_be_recorded_as_success(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.db,'get_step',lambda *a:None)
    monkeypatch.setattr(provider.db,'prepare_external',lambda *args: {'state':'SUBMITTING'})
    monkeypatch.setattr(provider.requests,'post',lambda *a,**k: Response({'code':400,'data':{'id':'bad-id'}}))
    monkeypatch.setattr(provider.db,'external_submitted',lambda *a: pytest.fail('Incorrect provider success'))
    with pytest.raises(provider.NeedsReview,match='unsuccessful'):
        provider.submit_once('job','wan_segment_000',ENDPOINT,PAYLOAD)


@pytest.mark.parametrize('status',['failed','cancelled','timeout','deleted','mystery'])
def test_terminal_or_unknown_prediction_requires_manual_review(monkeypatch,status):
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.requests,'get',lambda *a,**k: Response({'code':200,'data':{'status':status}}))
    monkeypatch.setattr(provider.db,'external_finished',lambda *a: pytest.fail('Terminal prediction marked successful'))
    step={'job_id':'job','step_key':'wan_segment_000','external_request_id':'pred_saved'}
    with pytest.raises(provider.NeedsReview):
        provider.poll(step)


def test_pending_prediction_polls_saved_id_without_post(monkeypatch):
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.requests,'post',lambda *a,**k: pytest.fail('Polling submitted another request'))
    urls=[]
    def fake_get(url, **kwargs):
        urls.append(url)
        return Response({'code':200,'data':{'status':'processing'}})
    monkeypatch.setattr(provider.requests,'get',fake_get)
    step={'job_id':'job','step_key':'wan_segment_000','external_request_id':'pred_existing'}
    assert provider.poll(step)['status']=='processing'
    assert urls==[provider.WAVESPEED_URL+'/predictions/pred_existing/result']


def test_no_model_or_resolution_drift(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake-unit-test-only')
    monkeypatch.setattr(provider.db,'get_step',lambda *a:None)
    monkeypatch.setattr(provider.db,'prepare_external',lambda *a: pytest.fail('Invalid request reserved'))
    monkeypatch.setattr(provider.requests,'post',lambda *a,**kw: pytest.fail('Invalid request posted'))
    with pytest.raises(ValueError,match='Unapproved'):
        provider.submit_once('job','wan_segment_000','some-other-model',PAYLOAD)
    with pytest.raises(ValueError,match='480p'):
        provider.submit_once('job','wan_segment_000',ENDPOINT,{**PAYLOAD,'resolution':'720p'})


def test_wan_pipeline_gated_without_flag(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','false')
    monkeypatch.setattr(pipeline.provider,'submit_once',lambda *a: pytest.fail('WAN submitted while paid disabled'))
    j={'job_id':'job','chat_id':123,'lease_owner':'worker','stage':'WAN_SUBMIT','state':'QUEUED',
       'current_segment':0,'outputs':[], 'checkpoint':{'context':{'character':'NICOLE'},'plan':{'segments':[{'source_start':0,'source_end':4}]},
       'segment_inputs':{'0':{'source_key':'x.mp4'}}}}
    with pytest.raises(pipeline.ConfigurationError,match='disabled'):
        pipeline.process(j)


def test_wan_pipeline_uses_original_payload_then_waits(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    j={'job_id':'job','chat_id':123,'lease_owner':'worker','stage':'WAN_SUBMIT','state':'QUEUED',
       'current_segment':0,'outputs':[], 'attempt':0,
       'checkpoint':{'context':{'character':'NICOLE'},'plan':{'segments':[{'source_start':0,'source_end':4}]},
       'segment_inputs':{'0':{'source_key':'x.mp4'}}}}
    monkeypatch.setattr(pipeline.storage,'presign',lambda *a:'https://example.com/source.mp4')
    monkeypatch.setattr(pipeline.parity,'run',lambda *a,**kw:{'wan_recreation_prompt':'same motion'})
    monkeypatch.setattr(pipeline.parity,'wan_payload',lambda **kw:PAYLOAD)
    calls=[]
    monkeypatch.setattr(pipeline.provider,'submit_once',lambda *args:calls.append(args) or {'state':'SUBMITTED','external_request_id':'mock-pred'})
    monkeypatch.setattr(pipeline.db,'checkpoint',lambda *a,**kw: calls.append(kw) or {'ok':True})
    pipeline.process(j)
    assert calls[0]==('job','wan_segment_000',ENDPOINT,PAYLOAD)
    assert calls[1]['stage']=='WAN_POLL'
    assert calls[1]['state']=='WAITING_PROVIDER'
