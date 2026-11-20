"""Owner-only resume of multi-clip recreation after saved face-QA mismatch.

Every paid request remains at-most-once and the failed result is never called
a successful face match. No real provider/network calls are made.
"""
import json
from pathlib import Path

import pytest
from recreation import qa_continue,quality,full_video,identity_qa,telegram,db


JOB_ID='85468e37-5adc-4808-93b6-d17a1d4c2113'


def job(**overrides):
    j={'job_id':JOB_ID,'chat_id':123,'owner_id':123,'state':'NEEDS_REVIEW',
        'stage':'AUTO_GENERATE','character':'NICOLE','model':'wan-3.0',
        'scenery':'use scenery reference image','outfit':'keep source clothing',
        'segment_total':3,'original_key':'original.mp4',
        'last_error':'Nicole facial identity QA mismatch: 1/6 visible faces matched the saved reference.',
        'checkpoint':{
            'identity':{'status':'ready','nicole_image_key':'identity/nicole.jpg'},
            'scenery_reference':{'status':'ready','scenery_image_key':'scene/photo.jpg'},
            'local_preflight':{'complete':True,'source_key':'original.mp4',
                               'source_probe':{'duration':22,'width':480,
                                               'height':854,'has_audio':True}}
        }}
    j.update(overrides)
    return j


def fake_steps(n=1,status='COMPLETED'):
    return [{'step_key':full_video.step_key(i),
             'external_request_id':'already-paid-'+str(i),
             'state':status} for i in range(n)]


def setup_review(monkeypatch,tmp_path,*,status='mismatch',n=1,steps=None,stored=True):
    monkeypatch.setattr(db,'auto_provider_steps',lambda *a:steps if steps is not None else fake_steps(n))
    monkeypatch.setattr(full_video,'_stored',lambda *_:stored)
    monkeypatch.setattr(qa_continue.storage,'download_key',
        lambda key,dst:Path(dst).write_text(json.dumps(
            {'status':status,'above_threshold':1,'detected_faces':6})))
    return qa_continue.reviewable(job())


def test_exact_archived_face_failure_is_eligible(monkeypatch,tmp_path):
    info=setup_review(monkeypatch,tmp_path)
    assert info['index']==0
    assert info['number']==1 and info['total']==3
    assert info['remaining_count']==2
    assert info['remaining_estimated_usd']>0
    assert info['key']==full_video.segment_key(JOB_ID,0)
    assert info['source_step']==full_video.step_key(0)
    assert info['status']=='mismatch'


@pytest.mark.parametrize('steps',[
    fake_steps(1,'SUBMITTED'),fake_steps(1,'UNCERTAIN'),
    fake_steps(1,'FAILED'),fake_steps(1,'SUBMITTING'),
    [dict(fake_steps()[0],external_request_id=None)],
    [dict(fake_steps()[0],step_key=full_video.step_key(1))]
])
def test_uncertain_or_noncontiguous_provider_is_never_overridden(monkeypatch,tmp_path,steps):
    with pytest.raises(ValueError,match='Provider request'):
        setup_review(monkeypatch,tmp_path,steps=steps)


def test_missing_archive_and_mismatched_qa_artifact_block(monkeypatch,tmp_path):
    with pytest.raises(ValueError,match='archive missing'):
        setup_review(monkeypatch,tmp_path,stored=False)
    with pytest.raises(ValueError,match='does not match'):
        setup_review(monkeypatch,tmp_path,status='passed')


def test_owner_exception_is_exact_clip_and_owner_only():
    j=job()
    j['checkpoint']['face_qa_approvals']={
        '0':{'type':'owner_accepted_facial_mismatch_for_continuation',
             'owner_id':123,
             'segment_key':full_video.segment_key(JOB_ID,0),
             'source_step':full_video.step_key(0),
             'accepted_status':'mismatch',
             'original_failure':j['last_error']}
    }
    key=full_video.segment_key(JOB_ID,0)
    assert qa_continue.owner_acceptance(j,key)['original_status']=='mismatch'
    assert qa_continue.owner_acceptance(j,full_video.segment_key(JOB_ID,1)) is None
    outsider={**j,'owner_id':999}
    assert qa_continue.owner_acceptance(outsider,key) is None
    changed={**j,'job_id':'different-uuid'}
    assert qa_continue.owner_acceptance(changed,key) is None


def test_accepted_clip_still_checks_original_negative_qa_file(monkeypatch,tmp_path):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    j=job()
    j['checkpoint']['face_qa_approvals']={'0':{
        'type':'owner_accepted_facial_mismatch_for_continuation',
        'owner_id':123,'segment_key':full_video.segment_key(JOB_ID,0),
        'source_step':full_video.step_key(0),
        'accepted_status':'mismatch','original_failure':j['last_error']}}
    key=full_video.segment_key(JOB_ID,0)
    received=[]
    monkeypatch.setattr(identity_qa.storage,'download_key',lambda key,dst:
        received.append(key) or Path(dst).write_text('{"status":"mismatch"}'))
    out=identity_qa.check_archived(j,key)
    assert out['status']=='owner_accepted'
    assert out['original_status']=='mismatch'
    assert received==[key+'.face_qa_v1.json']
    monkeypatch.setattr(identity_qa.storage,'download_key',
        lambda k,d:Path(d).write_text('{"status":"passed"}'))
    with pytest.raises(identity_qa.IdentityQualityReview,match='does not match'):
        identity_qa.check_archived(j,key)


def test_owner_approval_atomic_guard_preserves_provider_step(monkeypatch):
    history=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,sql,params):
            history.append((sql,params))
            return type('Result',(),{'fetchone':lambda self:{'state':'QUEUED'}})()
    monkeypatch.setattr(db,'connect',lambda:Connection())
    row=db.approve_qa_continue(JOB_ID,123,index=0,
        expected_step=full_video.step_key(0),
        expected_key=full_video.segment_key(JOB_ID,0),
        qa_status='mismatch')
    assert row['state']=='QUEUED'
    statement,params=history[0]
    assert "state='QUEUED'" in statement
    assert "state='COMPLETED'" in statement
    assert "COUNT(*)" in statement
    assert "s.state!='COMPLETED'" in statement
    assert 'face_qa_approvals' in statement
    assert JOB_ID in params and 123 in params
    assert 'mismatch' in params
    with pytest.raises(ValueError):
        db.approve_qa_continue(JOB_ID,123,index=0,
            expected_step='incorrect',
            expected_key=full_video.segment_key(JOB_ID,0),
            qa_status='mismatch')
    assert len(history)==1


def test_private_preview_then_explicit_approval_requeues_only(monkeypatch,tmp_path):
    j=job()
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'qa_review_job',lambda owner,prefix:j if owner==123 else None)
    monkeypatch.setattr(qa_continue,'reviewable',lambda job:{
        'index':0,'number':1,'total':3,'status':'mismatch',
        'matched':1,'detected':6,
        'key':full_video.segment_key(JOB_ID,0),
        'source_step':full_video.step_key(0),
        'remaining_count':2,'remaining_estimated_usd':1.4})
    monkeypatch.setattr(full_video.storage,'presign',lambda *a:'https://private.example/clip.mp4')
    output=[];notices=[];resumed=[]
    monkeypatch.setattr(telegram,'telegram',lambda method,payload:output.append((method,payload)))
    monkeypatch.setattr(telegram,'say',lambda chat,msg:notices.append(msg))
    monkeypatch.setattr(telegram.db,'approve_qa_continue',
        lambda jid,owner,**args:resumed.append((jid,owner,args)) or {'state':'QUEUED'})
    monkeypatch.setattr(full_video.provider,'submit_once',
        lambda *a,**kw:pytest.fail('Owner approval itself cannot charge Wan'))
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
        'text':'/review_qa 85468e37'})
    assert output[0][0]=='sendVideo'
    assert 'section 1/3' in output[0][1]['caption']
    assert '$1.40' in output[0][1]['caption']
    assert '/approve_qa 85468e37' in output[0][1]['caption']
    assert not resumed
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
        'text':'/approve_qa 85468e37'})
    assert len(resumed)==1
    assert resumed[0][0]==JOB_ID and resumed[0][1]==123
    assert 'Resuming 2 remaining sections' in notices[-1]


def test_resumed_approved_archive_is_not_resubmitted(monkeypatch,tmp_path):
    j=job(state='RUNNING',lease_owner='worker-123')
    j['checkpoint']['face_qa_approvals']={'0':{
        'type':'owner_accepted_facial_mismatch_for_continuation',
        'owner_id':123,'segment_key':full_video.segment_key(JOB_ID,0),
        'source_step':full_video.step_key(0),
        'accepted_status':'mismatch',
        'original_failure':j['last_error']}}
    # Frame/face check must be applied only to segment zero via owner override.
    plan=quality.quality_plan(22)
    monkeypatch.setattr(full_video,'_guard_auto',lambda job:{'segments':plan})
    monkeypatch.setattr(full_video,'_stored',lambda key:
        key==full_video.segment_key(JOB_ID,0) or key=='scene/photo.jpg')
    monkeypatch.setattr(full_video.db,'get_job',lambda *a:j)
    monkeypatch.setattr(full_video.db,'get_step',lambda *a:None)
    checks=[]
    monkeypatch.setattr(identity_qa,'check_archived',lambda job,key:checks.append(key) or {'status':'owner_accepted'})
    monkeypatch.setattr(full_video,'_prepare_reference',lambda job,seg:
        full_video.source_key(JOB_ID,seg['index']))
    monkeypatch.setattr(full_video.identity_qa,'prepare_anchor',
        lambda *a:'recreation/'+JOB_ID+'/identity/nicole_face_closeup_v1.jpg')
    monkeypatch.setattr(full_video.storage,'presign',
        lambda key,ttl:'https://private.example/'+key)
    submitted=[]
    def submit_once(jid,step,endpoint,payload):
        submitted.append(step)
        return {'state':'SUBMITTED','external_request_id':'fake-one',
                'input':{'payload':payload}}
    monkeypatch.setattr(full_video.provider,'submit_once',submit_once)
    monkeypatch.setattr(full_video.provider,'poll',lambda *a:{'status':'processing'})
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','on')
    assert full_video.run_auto(j)=='processing'
    assert checks==[full_video.segment_key(JOB_ID,0)]
    assert submitted==[full_video.step_key(1)]
