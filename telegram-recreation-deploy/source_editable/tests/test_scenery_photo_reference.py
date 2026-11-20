"""Scenery photo input is independent of Nicole's face, wardrobe and source audio.

All Telegram responses and provider calls are mocked (no API cost).
"""
import os
from types import SimpleNamespace

import pytest
from recreation import db, telegram, quality, full_video, segment_revision, scenery_ingest_once, storage


def draft(**override):
    result={'job_id':'local-draft-uuid','owner_id':123,'chat_id':123,
            'state':'DRAFT','stage':'UPLOAD_REFERENCE','character':'NICOLE',
            'model':'wan-3.0','scenery':'keep source scenery',
            'outfit':'keep source clothing','instructions':'',
            'original_key':'archive/original.mp4','source_file_id':'tg-video',
            'checkpoint':{
                'identity':{'status':'ready','nicole_image_key':'private/nicole.jpg'},
                'local_preflight':{'complete':True,'source_key':'archive/original.mp4',
                                   'source_probe':{'width':480,'height':854,
                                                   'duration':8.,'has_audio':True}}
            }}
    result.update(override)
    return result


def staged_scene(**overrides):
    j=draft(stage='AUTO_GENERATE',state='RUNNING')
    j['checkpoint']['scenery_reference']={
        'status':'ready','file_id':'tg-scenery','scenery_image_key':'private/scenery.jpg',
        'description':'warm evening garden'}
    j['scenery']='use scenery reference image'
    j.update(overrides)
    return j


def test_scene_is_never_an_identity_reference_in_prompt():
    job=staged_scene()
    prompt=quality.build_prompt(job,scene_image_number=3)
    assert 'IMAGE 1: Original canonical NICOLE FULL-BODY IDENTITY AND WARDROBE' in prompt
    assert 'IMAGE 2: CLOSE-UP OF THE EXACT SAME NICOLE FACE' in prompt
    assert 'IMAGE 3 IS THE EXCLUSIVE ENVIRONMENT AND SCENERY AUTHORITY' in prompt
    assert 'VIDEO 1: ORIGINAL REFERENCE CLIP' in prompt
    assert 'Never inherit the background from either Nicole image or Video 1' in prompt
    assert 'Do not paste a foreground cutout onto a still picture' in prompt
    assert 'warm evening garden' in prompt
    assert 'outfit shown on her canonical full-body reference' in prompt
    with pytest.raises(ValueError,match='Photo scenery requires canonical'):
        quality.build_prompt(job,scene_image_number=None)


@pytest.mark.parametrize('status',['awaiting','pending','failed'])
def test_unready_photo_blocks_any_generating_prompt(status):
    job=staged_scene()
    job['checkpoint']['scenery_reference']['status']=status
    with pytest.raises(ValueError,match='not ready'):
        quality.build_prompt(job,scene_image_number=3)


def test_scenery_photo_sent_as_caption_is_archived_as_scene_not_nicole(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    j=draft()
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:j)
    captured=[];msgs=[]
    monkeypatch.setattr(telegram.db,'record_scenery_image',
        lambda jid,owner,file_id,desc: captured.append((jid,owner,file_id,desc)) or True)
    monkeypatch.setattr(telegram.subprocess,'Popen',
        lambda args,**kw:captured.append(('spawn',args)) or object())
    monkeypatch.setattr(telegram,'say',lambda chat,msg:msgs.append(msg))
    telegram.process_message({'from':{'id':123},'chat':{'id':123},'caption':'/scenery Mediterranean seaside villa',
                              'photo':[{'file_id':'small','file_size':1000},
                                       {'file_id':'large','file_size':6000}]})
    assert captured[0]==('local-draft-uuid',123,'large','Mediterranean seaside villa')
    assert 'scenery_ingest_once' in captured[1][1][2]
    assert 'No AI charge' in msgs[-1]


def test_reply_to_photo_with_scenery_command(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:draft())
    saved=[]
    monkeypatch.setattr(telegram.db,'record_scenery_image',
        lambda *args:saved.append(args) or True)
    monkeypatch.setattr(telegram.subprocess,'Popen',lambda *a,**kw:None)
    monkeypatch.setattr(telegram,'say',lambda *a:None)
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
        'text':'/scenery','reply_to_message':{'document':{
            'file_id':'document-image','mime_type':'image/png','file_size':5000}}})
    assert saved[0][-2:] == ('document-image','')


def test_bare_scenery_arms_next_photo(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    j=draft()
    msgs=[];saved=[]
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:j)
    def await_pic(*args):
        j['checkpoint']['scenery_reference']={'status':'awaiting'}
        return True
    monkeypatch.setattr(telegram.db,'await_scenery_image',await_pic)
    monkeypatch.setattr(telegram.db,'record_scenery_image',lambda *args:saved.append(args) or True)
    monkeypatch.setattr(telegram.subprocess,'Popen',lambda *a,**kw:None)
    monkeypatch.setattr(telegram,'say',lambda chat,text:msgs.append(text))
    telegram.process_message({'from':{'id':123},'chat':{'id':123},'text':'/scenery'})
    assert 'Send the scenery photo next' in msgs[-1]
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
                              'photo':[{'file_id':'next-photo','file_size':6000}]})
    assert saved[-1][-2:] == ('next-photo','')


def test_plain_text_scenery_preserves_old_behavior(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'latest_job',lambda *_:draft())
    calls=[];messages=[]
    monkeypatch.setattr(telegram.db,'draft_update',
                        lambda *a:calls.append(a) or True)
    monkeypatch.setattr(telegram,'say',lambda chat,msg:messages.append(msg))
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
                              'text':'/scenery keep source scenery'})
    assert calls[-1][-2:]==('scenery','keep source scenery')
    assert messages[-1]=='Scenery saved.'


def test_storage_role_cannot_be_silently_swapped(monkeypatch):
    roles=[]
    monkeypatch.setattr(storage,'store_telegram_reference_image',
                        lambda jid,file_id,*,role:roles.append(role) or f'{role}/photo.jpg')
    assert storage.store_telegram_scenery('j','f')=='scenery/photo.jpg'
    assert storage.store_telegram_identity('j','f')=='identity/photo.jpg'
    assert roles==['scenery','identity']


@pytest.mark.parametrize('face_qa_mode',['on','off'])
def test_3_references_sent_to_wan_in_correct_order(monkeypatch,face_qa_mode):
    job=staged_scene()
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    monkeypatch.setenv('RECREATION_FACE_QA_MODE',face_qa_mode)
    segment={'index':0,'start':0,'duration':8.,'output_seconds':8,
             'estimated_usd':.8,'reused':False}
    monkeypatch.setattr(full_video,'_guard_auto',lambda j:{'segments':[segment]})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video.db,'get_step',lambda *_:None)
    monkeypatch.setattr(full_video,'_stored',lambda key:key=='private/scenery.jpg')
    monkeypatch.setattr(full_video,'_prepare_reference',lambda *a:full_video.source_key(job['job_id'],0))
    monkeypatch.setattr(full_video.identity_qa,'prepare_anchor',
                        lambda *a:f'recreation/{job["job_id"]}/identity/nicole_face_closeup_v1.jpg')
    monkeypatch.setattr(full_video.storage,'presign',
                        lambda key,seconds:'https://scene.example/'+key)
    payloads=[]
    def submit(jobid,key,model,payload):
        payloads.append(payload)
        return {'state':'SUBMITTED','input':{'payload':payload},
                'external_request_id':'fake-provider'}
    monkeypatch.setattr(full_video.provider,'submit_once',submit)
    monkeypatch.setattr(full_video.provider,'poll',lambda *a:{'status':'processing'})
    assert full_video.run_auto(job)=='processing'
    imgs=payloads[0]['reference_images']
    assert [url.rsplit('/',1)[-1] for url in imgs]==[
        'nicole.jpg','nicole_face_closeup_v1.jpg','scenery.jpg']
    assert 'IMAGE 3' in payloads[0]['prompt']
    assert len(payloads[0]['reference_videos'])==1


def test_single_section_redo_sends_same_three_image_references(monkeypatch):
    j=staged_scene(stage='SEGMENT_REDO',segment_total=1)
    parent=draft(job_id='parent-original-uuid',stage='DONE',state='COMPLETED')
    j['checkpoint']['segment_revision']={
        'parent_job_id':parent['job_id'],'parent_run_key':full_video.RUN_KEY,
        'section_index':0,'parent_sections':1,'revision_version':1}
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    j['state']='RUNNING'
    original_key=full_video.segment_key(parent['job_id'],0)
    motion=full_video.source_key(parent['job_id'],0)
    monkeypatch.setattr(segment_revision,'_validate',
                        lambda *_:(parent,[{'index':0,'start':0,'duration':8.,'output_seconds':8,
                                             'estimated_usd':.8}],0,[original_key],motion))
    monkeypatch.setattr(segment_revision,'_guard_paid',lambda *_:None)
    monkeypatch.setattr(full_video,'_stored',
                        lambda key:key=='private/scenery.jpg')
    monkeypatch.setattr(segment_revision.db,'get_step',lambda *a:None)
    monkeypatch.setattr(segment_revision.identity_qa,'prepare_anchor',
                        lambda _:f'recreation/{j["job_id"]}/identity/nicole_face_closeup_v1.jpg')
    monkeypatch.setattr(segment_revision.storage,'presign',
                        lambda key,ttl:'https://scene.example/'+key)
    payloads=[]
    def submit(jobid,key,model,payload):
        payloads.append(payload)
        return {'state':'SUBMITTED','input':{'payload':payload}}
    monkeypatch.setattr(segment_revision.provider,'submit_once',submit)
    monkeypatch.setattr(segment_revision.provider,'poll',
                        lambda *a:{'status':'processing'})
    assert segment_revision.run(j)=='processing'
    assert len(payloads)==1
    assert len(payloads[0]['reference_images'])==3
    assert payloads[0]['reference_images'][-1].endswith('/private/scenery.jpg')


def test_ingest_updates_only_scene_status(monkeypatch):
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','123')
    j=draft()
    j['checkpoint']['scenery_reference']={'status':'pending','file_id':'tg-scene'}
    monkeypatch.setattr(scenery_ingest_once.db,'get_job',lambda *_:j)
    monkeypatch.setattr(scenery_ingest_once.storage,'store_telegram_scenery',
                        lambda *a:'private/scenery/scene.jpg')
    approved=[];messages=[]
    monkeypatch.setattr(scenery_ingest_once.db,'mark_scenery_ready',
                        lambda *a:approved.append(a) or {'job_id':'local-draft-uuid'})
    monkeypatch.setattr(scenery_ingest_once.telegram,'say',
                        lambda *a:messages.append(a))
    assert scenery_ingest_once.ingest_scenery(j['job_id'])
    assert approved[0][-1]=='private/scenery/scene.jpg'
    assert 'No paid AI call' in messages[-1][1]


def test_generate_accepts_ready_scenery_photo_after_approval(monkeypatch):
    job=staged_scene(stage='UPLOAD_REFERENCE',state='DRAFT')
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setattr(telegram.db,'latest_job',lambda owner:job)
    authorized=[]
    monkeypatch.setattr(telegram.db,'queue_job',lambda jid,owner:authorized.append((jid,owner)) or {'job_id':jid})
    notices=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg:notices.append(msg))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},'text':'/generate'})
    assert authorized==[(job['job_id'],123)]
    assert 'Image 1 Nicole identity and outfit' in notices[-1]
    assert 'Image 3 uploaded scenery' in notices[-1]
    assert 'Video 1 choreography' in notices[-1]


def test_confirm_segment_accepts_ready_scenery_photo(monkeypatch):
    job=staged_scene(stage='SEGMENT_REDO',state='DRAFT')
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setattr(telegram.db,'latest_job',lambda owner:job)
    submitted=[]
    monkeypatch.setattr(telegram.db,'queue_segment_revision',
                        lambda jid,owner:submitted.append(jid) or True)
    message=[]
    monkeypatch.setattr(telegram,'say',lambda chat,txt:message.append(txt))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/confirm_segment'})
    assert submitted==[job['job_id']]
    assert 'ONE new Wan section' in message[-1]


def test_old_greenscreen_command_is_retired_without_queuing(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'create_scene_fix',
                        lambda *args:pytest.fail('No green-screen jobs may be created'),
                        raising=False)
    messages=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg:messages.append(msg))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},
                              'text':'/fix_scenery 996bca83'})
    assert 'retired' in messages[-1]
    assert 'did not start a render' in messages[-1]


def test_later_segment_receives_full_three_photo_contract_without_frame_chaining(monkeypatch):
    job=staged_scene()
    job['segment_total']=2
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','off')
    segs=[
        {'index':0,'start':0,'duration':8,'output_seconds':8,'estimated_usd':.8,'reused':False},
        {'index':1,'start':8,'duration':8,'output_seconds':8,'estimated_usd':.8,'reused':False}
    ]
    monkeypatch.setattr(full_video,'_guard_auto',lambda *_:{'segments':segs})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video.db,'get_step',lambda *_:None)
    monkeypatch.setattr(full_video,'_stored',lambda key:key in (
        'private/scenery.jpg',full_video.segment_key(job['job_id'],0)))
    monkeypatch.setattr(full_video.identity_qa,'check_archived',lambda *a,**kw:{'status':'disabled'})
    monkeypatch.setattr(full_video.identity_qa,'prepare_anchor',
        lambda *_:f'recreation/{job["job_id"]}/identity/nicole_face_closeup_v1.jpg')
    monkeypatch.setattr(full_video,'_prepare_continuity_frame',
        lambda *args:pytest.fail('No generated-face chaining permitted for scene photos'))
    monkeypatch.setattr(full_video,'_prepare_reference',
        lambda job,seg:full_video.source_key(job['job_id'],seg['index']))
    monkeypatch.setattr(full_video.storage,'presign',
        lambda key,ttl:'https://test-private.example/'+key)
    sent=[]
    monkeypatch.setattr(full_video.provider,'submit_once',
        lambda jid,key,model,payload:sent.append((key,payload)) or {
            'state':'SUBMITTED','external_request_id':'not-billable',
            'input':{'payload':payload}})
    monkeypatch.setattr(full_video.provider,'poll',lambda *_:{'status':'processing'})
    assert full_video.run_auto(job)=='processing'
    assert len(sent)==1
    assert sent[0][0]==full_video.step_key(1)
    payload=sent[0][1]
    assert len(payload['reference_images'])==3
    assert payload['reference_images'][0].endswith('/private/nicole.jpg')
    assert payload['reference_images'][1].endswith('/identity/nicole_face_closeup_v1.jpg')
    assert payload['reference_images'][2].endswith('/private/scenery.jpg')
    assert payload['reference_videos']==[
        'https://test-private.example/'+full_video.source_key(job['job_id'],1)]
    assert 'Never inherit the background from either Nicole image or Video 1' in payload['prompt']
    assert payload['generate_audio'] is False
