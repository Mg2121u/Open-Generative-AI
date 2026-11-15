"""Recovery tests, including an interrupted paid segment, no API credentials."""
import sys
import types
# Isolate the state-machine tests from PostgreSQL's optional client library.
# A real database integration test is still required before deployment.
try:
    import psycopg
except ImportError:
    fake=types.ModuleType('psycopg')
    fake.rows=types.ModuleType('psycopg.rows')
    fake.rows.dict_row=object()
    fake.types=types.ModuleType('psycopg.types')
    fake.types.json=types.ModuleType('psycopg.types.json')
    fake.types.json.Jsonb=lambda x:x
    sys.modules.update({'psycopg':fake,'psycopg.rows':fake.rows,
                        'psycopg.types':fake.types,'psycopg.types.json':fake.types.json})
from recreation import pipeline


def job(stage='WAN_POLL',index=6):
    plan={'segments':[{'segment_index':i,'source_start':i*10,'source_end':(i+1)*10,
                        'source_duration':10,'output_duration':10,'wan_prompt':'Copy exact movements'}
                       for i in range(9)]}
    return {'job_id':'test-job-uuid','chat_id':1234,'lease_owner':'worker-test',
            'stage':stage,'state':'WAITING_PROVIDER','current_segment':index,
            'attempt':0,'checkpoint':{'plan':plan,'context':{'character':'NICOLE'}},
            'outputs':[{'segment_index':i,'video_key':f'saved/clip_{i}.mp4'} for i in range(index)]}


def test_restarted_worker_polls_existing_seventh_segment_not_submits(monkeypatch):
    current=job()
    calls=[]
    monkeypatch.setattr(pipeline.db,'get_step',lambda job_id,key:{
        'state':'SUBMITTED','external_request_id':'existing-paid-request',
        'step_key':key,'job_id':job_id})
    monkeypatch.setattr(pipeline.provider,'submit_once',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Double billed')))
    monkeypatch.setattr(pipeline.provider,'poll',lambda step: {'status':'completed','outputs':['https://example.org/clip7.mp4']})
    monkeypatch.setattr(pipeline.db,'checkpoint',lambda *args,**kwargs:calls.append(kwargs) or {'saved':True})
    pipeline.process(current)
    assert len(calls)==1
    assert calls[0]['stage']=='SAVE_SEGMENT'
    assert calls[0]['fields']['checkpoint']['last_provider_result']['outputs'][0].endswith('clip7.mp4')


def test_all_first_six_saved_without_rerunning_after_restart(monkeypatch):
    current=job(stage='SAVE_SEGMENT')
    current['checkpoint']['last_provider_result']={
        'status':'completed','outputs':['https://example.org/clip7.mp4']}
    calls=[]
    monkeypatch.setattr(pipeline.storage,'store_provider_url',lambda url,key:key)
    monkeypatch.setattr(pipeline.db,'checkpoint',lambda *a,**kw:calls.append(kw) or {'saved':True})
    pipeline.process(current)
    assert len(calls)==1
    updates=calls[0]
    assert updates['stage']=='SEGMENT_TRIM'
    assert updates['fields']['current_segment']==7
    outputs=updates['fields']['outputs']
    assert [x['segment_index'] for x in outputs]==list(range(7))
    assert outputs[0]['video_key']=='saved/clip_0.mp4'
    assert outputs[5]['video_key']=='saved/clip_5.mp4'
    assert outputs[6]['video_key'].endswith('/006.mp4')
