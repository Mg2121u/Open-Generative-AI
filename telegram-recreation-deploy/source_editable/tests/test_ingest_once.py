"""Zero-paid-call one-shot source backup tests."""
# The local source-tests run without psycopg installed; use the same lightweight
# import shim as the existing state-machine tests. The real Docker image installs
# psycopg and executes these tests against the actual import path.
import sys, types
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
from recreation import ingest_once, telegram, provider


def test_ingest_success_checkpoints_before_notice(monkeypatch):
    steps=[]
    job={'job_id':'test-123','source_file_id':'file', 'chat_id':5}
    monkeypatch.setattr(ingest_once.storage,'store_telegram', lambda *a: steps.append('stored') or 'recreation/test-123/original.mp4')
    monkeypatch.setattr(ingest_once.db,'checkpoint', lambda *a, **kw: steps.append(('checkpoint',kw)) or {'done':True})
    monkeypatch.setattr(ingest_once.telegram,'say', lambda *a:steps.append('notice'))
    monkeypatch.setattr(provider,'submit_once',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('paid call')))
    assert ingest_once.ingest_one(job,'worker-id') is True
    assert steps[0] == 'stored'
    assert steps[1][0] == 'checkpoint'
    assert steps[1][1]['stage'] == 'UPLOAD_REFERENCE'
    assert steps[1][1]['state'] == 'DRAFT'
    assert steps[1][1]['fields']['original_key'] == 'recreation/test-123/original.mp4'
    assert steps[-1] == 'notice'


def test_failure_preserves_draft_and_never_logs_sensitive_exception(monkeypatch,caplog):
    monkeypatch.setattr(ingest_once.storage,'store_telegram', lambda *_: (_ for _ in ()).throw(ValueError('secret-URL')))
    saved=[]
    monkeypatch.setattr(ingest_once.db,'checkpoint', lambda *a,**kw:saved.append(kw))
    assert ingest_once.ingest_one({'job_id':'test-id','source_file_id':'file','chat_id':5},'worker') is False
    assert saved[0]['state']=='DRAFT'
    assert saved[0]['stage']=='UPLOAD'
    assert saved[0]['fields']['last_error'].endswith('ValueError')
    assert 'secret-URL' not in caplog.text


def test_no_allowed_users_means_no_db_or_media_access(monkeypatch):
    monkeypatch.delenv('ALLOWED_TELEGRAM_USER_IDS',raising=False)
    monkeypatch.setattr(ingest_once,'claim_ingest',lambda *a:(_ for _ in ()).throw(AssertionError('queried')))
    assert ingest_once.run()==0


def test_generation_command_is_hard_gated_in_staging(monkeypatch):
    import recreation.telegram as tg
    monkeypatch.setenv('ENABLE_PAID_GENERATION','false')
    monkeypatch.setattr(tg,'ALLOWED_USERS',{8321358143})
    monkeypatch.setattr(tg.db,'latest_job',lambda owner:{'job_id':'job1','state':'DRAFT','source_file_id':'file','voiceover_text':''})
    monkeypatch.setattr(tg.db,'queue_job',lambda *a: (_ for _ in ()).throw(AssertionError('queued paid workflow')))
    messages=[]
    monkeypatch.setattr(tg,'say',lambda chat,text:messages.append(text))
    tg.process_message({'chat':{'id':8321358143},'from':{'id':8321358143},'text':'/generate'})
    assert 'disabled' in messages[-1].lower()


def test_lease_claim_requires_owner_allowlist(monkeypatch):
    assert ingest_once.claim_ingest('worker',[]) is None


def test_archive_command_starts_oneoff_process_without_running_generation(monkeypatch):
    import recreation.telegram as tg
    monkeypatch.setattr(tg,'ALLOWED_USERS',{8321358143})
    monkeypatch.setattr(tg.db,'latest_job',lambda _: {'state':'DRAFT','source_file_id':'file','original_key':None})
    spawned=[]
    monkeypatch.setattr(tg.subprocess,'Popen',lambda *a,**k:spawned.append((a,k)))
    monkeypatch.setattr(tg.db,'queue_job',lambda *a: (_ for _ in ()).throw(AssertionError('queue paid')))
    texts=[]
    monkeypatch.setattr(tg,'say',lambda c,t:texts.append(t))
    tg.process_message({'chat':{'id':8321358143},'from':{'id':8321358143},'text':'/archive'})
    assert len(spawned)==1
    assert spawned[0][0][0][-1]=='recreation.ingest_once'
    assert 'No AI generation' in texts[-1]
