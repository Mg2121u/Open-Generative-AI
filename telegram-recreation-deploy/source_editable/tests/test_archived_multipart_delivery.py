"""Owner-approved large MP4 Telegram delivery uses multipart, never regenerates Wan.

Mocks S3, Telegram HTTP and database; no paid providers or real bot calls.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from recreation import telegram,db,full_video,archived_delivery,app_queue


JOB_ID='32e09922-09c0-4729-bf29-a7455b51e614'


def original():
    return {'job_id':JOB_ID,'owner_id':123,'chat_id':123,'character':'NICOLE',
            'state':'RUNNING','stage':'ARCHIVE_DELIVER','model':'wan-3.0',
            'segment_total':27,'original_key':'private/original.mp4',
            'lease_owner':'current-worker',
            'checkpoint':{'identity':{'status':'ready','nicole_image_key':'nicole.jpg'},
                'face_qa_approvals':{str(x):{'owner_id':123} for x in range(15)},
                'local_preflight':{'complete':True,'source_key':'private/original.mp4'}}}


def test_send_archived_multipart_upload_uses_original_private_archive(monkeypatch,tmp_path):
    monkeypatch.setattr(telegram,'BOT_TOKEN','fake-unit-token')
    monkeypatch.setattr(telegram,'BOT_API_URL','https://api.telegram.org')
    keys=[]
    monkeypatch.setattr(telegram,'send_archived_video',telegram.send_archived_video)
    def download(key,path):
        keys.append(key)
        Path(path).write_bytes(b'video_bytes'*600)
    monkeypatch.setattr(telegram.storage if hasattr(telegram,'storage') else
        __import__('recreation.storage',fromlist=['storage']),'download_key',download)
    reqs=[]
    def post(url,**kwargs):
        assert '/sendVideo' in url
        assert 'files' in kwargs
        assert 'video' in kwargs['files']
        assert 'video' not in kwargs.get('data',{})
        assert kwargs['files']['video'][1].read(20)
        reqs.append((url,kwargs))
        return SimpleNamespace(json=lambda:{'ok':True,'result':{'message_id':777}})
    monkeypatch.setattr(telegram.requests,'post',post)
    result=telegram.send_archived_video(123,'private/final.mp4')
    assert result['message_id']==777
    assert keys==['private/final.mp4']
    assert len(reqs)==1


def test_archived_delivery_only_archived_bytes_once(monkeypatch):
    j=original()
    steps=[{'step_key':full_video.step_key(i),'state':'COMPLETED',
            'external_request_id':'paid-'+str(i)} for i in range(27)]
    monkeypatch.setattr(db,'auto_provider_steps',lambda *_:steps)
    monkeypatch.setattr(full_video,'_stored',lambda *_:True)
    original_key=f'{full_video.RUN_KEY}_telegram_delivery'
    monkeypatch.setattr(db,'get_step',lambda jid,key:
        {'state':'UNCERTAIN'} if key==original_key else None)
    calls=[]
    monkeypatch.setattr(db,'reserve_one_shot',
        lambda jid,key,provider,payload:calls.append(('reserve',key)) or True)
    monkeypatch.setattr(telegram,'send_archived_video',
        lambda chat,key,**kwargs:calls.append(('send',key)) or {'message_id':321})
    monkeypatch.setattr(db,'external_finished',
        lambda jid,key,result:calls.append(('complete',key)) or {'state':'COMPLETED'})
    monkeypatch.setattr(full_video.provider,'submit_once',
        lambda *_:pytest.fail('No paid model calls'))
    monkeypatch.setattr(full_video,'_merge_original_sound',
        lambda *_ ,**kwargs:pytest.fail('No new FFmpeg remux'))
    assert archived_delivery.run(j)=='completed'
    assert [x[0] for x in calls]==['reserve','send','complete']
    assert calls[1][1]==full_video.final_key(JOB_ID)
    assert len(j['checkpoint']['face_qa_approvals'])==15


def test_prior_archive_delivery_reservation_fails_closed(monkeypatch):
    j=original()
    monkeypatch.setattr(db,'auto_provider_steps',lambda *_:[
        {'step_key':full_video.step_key(i),'state':'COMPLETED',
         'external_request_id':'paid'} for i in range(27)])
    monkeypatch.setattr(full_video,'_stored',lambda *_:True)
    monkeypatch.setattr(db,'get_step',lambda jid,key:
        {'state':'SUBMITTING'} if key.endswith('_telegram_delivery') else
        {'state':'SUBMITTING'})
    monkeypatch.setattr(telegram,'send_archived_video',
        lambda *args,**kw:pytest.fail('May have been delivered already'))
    with pytest.raises(full_video.provider.NeedsReview,match='already attempted'):
        archived_delivery.run(j)


def test_archive_deliver_worker_never_calls_paid_provider_or_ffmpeg(monkeypatch):
    j=original()
    monkeypatch.setattr(archived_delivery,'run',lambda _: 'completed')
    monkeypatch.setattr(full_video,'run_auto',
        lambda *_:pytest.fail('Paid generate disallowed'))
    updated=[]
    monkeypatch.setattr(db,'checkpoint',
        lambda *args,**kwargs:updated.append(kwargs) or True)
    app_queue.process(j)
    assert updated[0]['stage']=='DONE'
    assert updated[0]['state']=='COMPLETED'
    assert updated[0]['fields']['current_segment']==27


def test_owner_archive_delivery_queue_requires_failed_telegram_and_all_paid_steps(monkeypatch):
    history=[]
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,sql,args):
            history.append((sql,args))
            return SimpleNamespace(fetchone=lambda:{'job_id':JOB_ID})
    monkeypatch.setattr(db,'connect',lambda:Conn())
    assert db.queue_archive_delivery(123,'32e09922')['job_id']==JOB_ID
    sql,params=history[0]
    assert "stage='ARCHIVE_DELIVER'" in sql
    assert "j.state='NEEDS_REVIEW'" in sql
    assert "t.state IN ('SUBMITTING','UNCERTAIN','FAILED')" in sql
    assert "s.state='COMPLETED'" in sql
    assert "s.state!='COMPLETED'" in sql
    assert 'multipart_v1' in params[4]
    assert params[0]==123 and params[1]=='32e09922'
    with pytest.raises(ValueError):
        db.queue_archive_delivery(123,'invalid')
    assert len(history)==1


def test_owner_command_delivery_queued_once(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'queue_archive_delivery',
        lambda owner,prefix:{'job_id':JOB_ID})
    out=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg:out.append(msg))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/deliver 32e09922'})
    assert 'ONE possible duplicate' in out[-1]
    assert 'No new Wan generation' in out[-1]
    assert 'directly' in out[-1]


def test_owner_delivery_not_accessible_to_fans(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'queue_archive_delivery',
        lambda *a:pytest.fail('must not query private user media'))
    answers=[]
    monkeypatch.setattr(telegram,'say',lambda c,t:answers.append(t))
    telegram.process_message({'chat':{'id':777},'from':{'id':777},
                              'text':'/deliver 32e09922'})
    assert 'private' in answers[-1].lower()
