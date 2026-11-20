"""Regression tests for owner-scoped, local-only PHOTO scenery salvage.

No WaveSpeed, image generator, or paid provider is invoked.
"""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import cv2
import pytest

from recreation import db,full_video,telegram,app_queue,scenery_lock,scenery_repair


PARENT_ID='996bca83-1111-4111-a111-111111111111'


def parent():
    return {
        'job_id':PARENT_ID,'owner_id':123,'chat_id':123,
        'state':'COMPLETED','stage':'DONE',
        'character':'NICOLE','model':'wan-3.0',
        'original_key':'recreation/archive/source.mp4',
        'source_file_id':'tg-video','scenery':'use scenery reference image',
        'outfit':'keep source clothing','instructions':'','voiceover_voice':'AUTO',
        'checkpoint':{
            'identity':{'status':'ready','nicole_image_key':'refs/nicole.jpg'},
            'scenery_reference':{'status':'ready','scenery_image_key':'refs/terrace.jpg',
                                  'file_id':'tg-photo'},
            'local_preflight':{'complete':True,'source_key':'recreation/archive/source.mp4',
                               'source_probe':{'duration':15.71,'has_audio':True,
                                               'width':480,'height':854}}
        }
    }


def derived():
    p=parent()
    return {
        **p,'job_id':'22222222-2222-4222-8222-222222222222',
        'state':'RUNNING','stage':'SCENE_FIX','segment_total':0,'lease_owner':'worker-a',
        'checkpoint':{**p['checkpoint'],
            'scene_repair':{'parent_job_id':PARENT_ID,
                'parent_run_key':full_video.RUN_KEY,
                'mode':'strict_local_matte_preview_v1'}}
    }


def test_scene_rescue_queue_only_uses_owned_finished_job(monkeypatch):
    p=parent()
    history=[]
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,sql,params):
            history.append((sql,params))
            if "SELECT * FROM recreation_jobs" in sql:
                return SimpleNamespace(fetchall=lambda:[p])
            if "SELECT job_id,state" in sql:
                return SimpleNamespace(fetchone=lambda:None)
            return SimpleNamespace(fetchone=lambda:None)
    monkeypatch.setattr(db,'connect',lambda:C())
    job=db.create_scene_fix(123,123,'996bca83')
    assert job['from_job']==PARENT_ID
    assert not job['already_queued']
    assert history[0][1]==(123,'996bca83')
    assert "'SCENE_FIX','QUEUED'" in history[-1][0]
    assert history[-1][1][-1].obj['scene_repair']['parent_job_id']==PARENT_ID
    assert history[-1][1][-1].obj['scenery_reference']['scenery_image_key']=='refs/terrace.jpg'
    assert all('provider' not in sql.lower() for sql,_ in history)
    with pytest.raises(ValueError):
        db.create_scene_fix(123,123,'1; DROP TABLE x')


def test_scenery_fix_is_never_billed_and_parent_unchanged(monkeypatch):
    job=derived()
    p=parent()
    monkeypatch.setattr(db,'get_job',lambda id,owner_id:p)
    monkeypatch.setattr(full_video,'_stored',lambda *_:True)
    calls=[]
    monkeypatch.setattr(scenery_repair.storage,'download_key',
                        lambda key,path:Path(path).write_bytes(b'private contents'))
    monkeypatch.setattr(scenery_repair.scenery_lock,'render',
                        lambda video,scene,out: calls.append(('local',str(video),str(scene))) or
                        Path(out).write_bytes(b'local only') or {
                            'frames':24,'uncertain_mask_frames':0})
    # The test above must avoid returning an integer from write_bytes.
    def fake_render(video,scene,out):
        Path(out).write_bytes(b'local local')
        return {'frames':24,'uncertain_mask_frames':0}
    monkeypatch.setattr(scenery_repair.scenery_lock,'render',fake_render)
    uploaded=[];delivered=[]
    monkeypatch.setattr(scenery_repair.storage,'upload_key',
                        lambda key,path:uploaded.append(key))
    monkeypatch.setattr(scenery_repair.identity_qa,'check_archived',
                        lambda job,key:{'status':'passed'})
    monkeypatch.setattr(full_video,'_deliver_once',
                        lambda job,key,**kw:delivered.append((key,kw)))
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a,**kw:pytest.fail('No Wan calls allowed in scenery rescue'))
    assert scenery_repair.run(job)=='completed'
    assert uploaded==[scenery_repair.repaired_key(job['job_id'])]
    assert delivered[0][0]==uploaded[0]
    assert 'no Wan generation' in delivered[0][1]['caption']
    assert job['checkpoint']['scene_repair']['parent_job_id']==PARENT_ID
    assert p['state']=='COMPLETED'


def test_worker_routes_scenery_rescue_locally(monkeypatch):
    job=derived()
    seen=[];updates=[]
    monkeypatch.setattr(scenery_repair,'run',
                        lambda j: seen.append(j['job_id']) or 'completed')
    monkeypatch.setattr(full_video,'run_auto',
                        lambda *_:pytest.fail('Not a paid generation'))
    monkeypatch.setattr(db,'checkpoint',
                        lambda *a,**kw:updates.append(kw) or True)
    app_queue.process(job)
    assert seen==[job['job_id']]
    assert updates[-1]['stage']=='DONE'
    assert updates[-1]['state']=='COMPLETED'


def test_photo_scenery_payment_is_paused_on_owner_command(monkeypatch):
    j=parent()
    j['state']='DRAFT';j['stage']='UPLOAD_REFERENCE'
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'latest_job',lambda *a:j)
    monkeypatch.setattr(telegram.db,'queue_job',
                        lambda *a:pytest.fail('Must block another paid render'))
    out=[]
    monkeypatch.setattr(telegram,'say',lambda c,text:out.append(text))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},'text':'/generate'})
    assert 'temporarily' in out[-1]
    assert 'photo' in out[-1].lower()


def test_owner_fix_command_does_not_require_recent_draft(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'create_scene_fix',
                        lambda chat,owner,prefix:{
                            'job_id':'new-local-job-uuid','from_job':PARENT_ID,
                            'already_queued':False})
    out=[]
    monkeypatch.setattr(telegram,'say',lambda c,txt:out.append(txt))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/fix_scenery 996bca83'})
    assert 'No paid AI charge' in out[-1]
    assert '996bca83' in out[-1]


def test_exact_background_composite_without_wan(monkeypatch,tmp_path):
    """A synthetic moving rectangle stays unchanged; background is exact green."""
    w,h=320,576
    video=tmp_path/'input.mp4'
    image=tmp_path/'scene.jpg'
    out=tmp_path/'strict.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),24,(w,h))
    assert writer.isOpened()
    for n in range(24):
        f=np.zeros((h,w,3),dtype=np.uint8)
        f[:]=(170,70,50) # original room
        cv2.rectangle(f,(120+n//3,160),(205+n//3,500),(50,30,235),-1)
        writer.write(f)
    writer.release()
    from PIL import Image
    Image.new('RGB',(w,h),(10,220,20)).save(image)
    class Dummy:
        def get_inputs(self):return [SimpleNamespace(shape=[1,3,640,400])]
    def fake_alpha(frame,session,size):
        # On this synthetic test, the mannequin's moving mask is supplied.
        a=np.zeros(frame.shape[:2],dtype=np.float32)
        # Locate the synthetic red subject by color, not room/background.
        a[(frame[:,:,2]>150)&(frame[:,:,1]<90)]=1.
        return a
    monkeypatch.setattr(scenery_lock,'_matte',fake_alpha)
    info=scenery_lock.render(video,image,out,session=Dummy())
    assert info['frames']==24
    assert info['uncertain_mask_frames']==0
    cap=cv2.VideoCapture(str(out))
    ok,frame=cap.read()
    cap.release()
    assert ok
    # BGR: image RGB green means BGR (20,220,10). H264 adds tiny variance.
    assert int(frame[20,20,1])>195
    assert int(frame[20,20,0])<55
    assert int(frame[260,160,2])>190
