import pytest,sys,types
try:
    import psycopg
except ImportError:
    fake=types.ModuleType('psycopg')
    fake.rows=types.ModuleType('psycopg.rows')
    fake.rows.dict_row=object()
    fake.types=types.ModuleType('psycopg.types')
    fake.types.json=types.ModuleType('psycopg.types.json')
    fake.types.json.Jsonb=lambda x:x
    sys.modules.update({'psycopg':fake,'psycopg.rows':fake.rows,'psycopg.types':fake.types,
                        'psycopg.types.json':fake.types.json})
from recreation import wan_canary, telegram, provider


def test_telegram_identity_text_shows_instructions(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{8321358143})
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:{'job_id':'job1','character':'NICOLE','state':'DRAFT'})
    lines=[]
    monkeypatch.setattr(telegram,'say',lambda _,t:lines.append(t))
    telegram.process_message({'chat':{'id':8321358143},'from':{'id':8321358143},'text':'/identity'})
    assert 'Attach one clear Nicole reference photo' in lines[-1]


def test_telegram_attached_photo_records_and_starts_archive(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{8321358143})
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:{'job_id':'job1','character':'NICOLE','state':'DRAFT'})
    called=[]
    monkeypatch.setattr(telegram.db,'record_identity_image',lambda *a:called.append(('db',a)) or True)
    monkeypatch.setattr(telegram.subprocess,'Popen',lambda *a,**kw:called.append(('process',a)))
    monkeypatch.setattr(telegram,'say',lambda *a:None)
    telegram.process_message({'chat':{'id':8321358143},'from':{'id':8321358143},'caption':'/identity','photo':[{'file_id':'photo123','file_size':15000}]})
    assert called[0][0]=='db' and called[0][1][2]=='photo123'
    assert called[1][0]=='process'


def test_generate_fail_closed_without_identity_even_if_paid_switch_enabled(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{8321358143})
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.delenv('NICOLE_IDENTITY_URLS',raising=False)
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:{'job_id':'job1','character':'NICOLE','state':'DRAFT','checkpoint':{},'source_file_id':'f'})
    monkeypatch.setattr(telegram.db,'queue_job',lambda *_:pytest.fail('paid job must not be queued'))
    lines=[]
    monkeypatch.setattr(telegram,'say',lambda _,t:lines.append(t))
    telegram.process_message({'chat':{'id':8321358143},'from':{'id':8321358143},'text':'/generate'})
    assert 'reference missing' in lines[-1].lower()


def test_canary_always_refuses_missing_identity_before_provider(monkeypatch):
    monkeypatch.setenv('WORKER_MODE','canary_once')
    monkeypatch.setenv('WAN_CANARY_ONE_SHOT','true')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','false')
    monkeypatch.setenv('WAN_CANARY_MAX_USD','0.40')
    monkeypatch.setenv('WAVESPEED_API_KEY','fake')
    monkeypatch.setenv('WAN_CANARY_SOURCE_JOB_ID','draft')
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','8321358143')
    job={'job_id':'draft','owner_id':8321358143,'state':'DRAFT','character':'NICOLE','original_key':'original',
         'checkpoint':{'local_preflight':{'source_key':'original','complete':True,'segment_keys':['first']}}}
    monkeypatch.setattr(wan_canary.db,'get_job',lambda *_:job)
    monkeypatch.setattr(wan_canary.provider,'submit_once',lambda *_:pytest.fail('should never submit'))
    with pytest.raises(RuntimeError,match='Nicole reference image not archived'):
        wan_canary.run_once()


def test_identity_context_uses_private_reference_without_env(monkeypatch):
    from recreation import pipeline
    monkeypatch.delenv('NICOLE_IDENTITY_URLS',raising=False)
    monkeypatch.delenv('ARIA_IDENTITY_URLS',raising=False)
    monkeypatch.setattr(pipeline.storage,'presign',lambda key,seconds=3600:'https://storage.example/nicole.jpg')
    job={'job_id':'draft','character':'NICOLE','scenery':'','outfit':'','instructions':'',
        'checkpoint':{'identity':{'status':'ready','nicole_image_key':'private/nicole.jpg'}}}
    context=pipeline.identity_context(job)
    assert context['identity_images']==['https://storage.example/nicole.jpg']
