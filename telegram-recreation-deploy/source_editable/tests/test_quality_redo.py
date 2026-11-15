"""Owner-only unbilled redo of a completed source, with isolated new job ID."""
from recreation import db, telegram, quality


class StubConnection:
    def __init__(self, prior):
        self.prior=prior
        self.executed=[]
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def execute(self, sql, params):
        self.executed.append((sql, params))
        if sql.lstrip().startswith('SELECT'):
            return StubResult(self.prior)
        return StubResult({'job_id':params[0]})

class StubResult:
    def __init__(self, value): self.value=value
    def fetchone(self): return self.value


def test_redo_forks_completed_original_without_provider_or_old_outputs(monkeypatch):
    previous={
        'job_id':'old-job-id', 'character':'NICOLE', 'model':'wan-3.0',
        'source_file_id':'old-telegram-file', 'original_key':'source/in-storage.mp4',
        'scenery':'Beirut skyline during sunset',
        'outfit':'same as reference image', 'instructions':'same motions',
        'voiceover_voice':'AUTO',
        'checkpoint':{
            'identity':{'status':'ready','nicole_image_key':'owner/nicole.jpg'},
            'local_preflight':{'source_key':'source/in-storage.mp4',
                               'source_probe':{'duration':18.2},'complete':True},
        }
    }
    conn=StubConnection(previous)
    monkeypatch.setattr(db, 'connect', lambda:conn)
    result=db.create_redo_from_latest(123,123)
    assert result['from_job']=='old-job-id'
    assert result['job_id']!='old-job-id'
    insert=conn.executed[-1]
    assert "INSERT INTO recreation_jobs" in insert[0]
    assert "'UPLOAD_REFERENCE'" in insert[0]
    assert insert[1][5]=='old-telegram-file'
    assert insert[1][6]=='source/in-storage.mp4'
    assert insert[1][7]=='Beirut skyline during sunset'
    assert len(conn.executed)==2
    assert 'recreation_steps' not in str(conn.executed)


def test_no_completed_owner_source_no_redo(monkeypatch):
    conn=StubConnection(None)
    monkeypatch.setattr(db, 'connect', lambda:conn)
    assert db.create_redo_from_latest(123,123) is None
    assert len(conn.executed)==1


def test_telegram_redo_does_not_call_paid_generate(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    messages=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg: messages.append(msg))
    monkeypatch.setattr(telegram.db,'create_redo_from_latest',
                        lambda chat,owner:{'job_id':'new-draft-uuid','from_job':'old-source-uuid'})
    monkeypatch.setattr(telegram.db,'queue_job',
                        lambda *a: (_ for _ in ()).throw(AssertionError('billable queue')))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},'text':'/redo'})
    assert len(messages)==1
    assert 'No new AI charges' in messages[0]


def test_redo_draft_uses_quality_estimate(monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    assert quality.enabled({'stage':'UPLOAD_REFERENCE'})
    assert len(quality.quality_plan(18.2))==3
