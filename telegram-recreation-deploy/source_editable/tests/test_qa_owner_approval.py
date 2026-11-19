"""An explicit owner acceptance may deliver a failed-QA ONE-clip video.

No provider requests, no model retry, no possible cross-owner clip lookup.
"""
import pytest
from recreation import qa_owner_approval, app_queue, full_video, telegram, db


def review_job(**update):
    cp={'identity':{'status':'ready','nicole_image_key':'photos/nicole-original.jpg'},
        'local_preflight':{'complete':True,'source_key':'archive/source.mp4',
            'source_probe':{'duration':7.5,'width':480,'height':854,'has_audio':True}},
        'quality_override':{
            'type':'owner_accepted_facial_mismatch',
            'owner_id':123,
            'original_failure':'Nicole facial identity QA mismatch: 1/9 visible faces matched the saved reference.',
            'source_step':full_video.step_key(0)}
        }
    job={'job_id':'59bc3ef4-e5c0-4514-8c1a-07d1b75aeb7c',
        'owner_id':123,'chat_id':123,'stage':'QA_OWNER_APPROVED',
        'state':'RUNNING','character':'NICOLE','model':'wan-3.0',
        'original_key':'archive/source.mp4','checkpoint':cp,'segment_total':1,
        'lease_owner':'worker-1',
        'last_error':'Nicole facial identity QA mismatch: 1/9 visible faces matched the saved reference.'}
    job.update(update)
    return job


def test_reviewed_clip_is_assembled_without_any_model_request(monkeypatch):
    job=review_job()
    original_key=full_video.segment_key(job['job_id'],0)
    monkeypatch.setattr(db,'get_step',lambda *_:{'state':'COMPLETED'})
    monkeypatch.setattr(full_video,'_stored',lambda key:key==original_key)
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a,**kw:pytest.fail('QA owner override must never buy a new clip'))
    monkeypatch.setattr(full_video,'run_auto',
                        lambda *a,**kw:pytest.fail('QA override must not enter paid generation'))
    merged=[];delivered=[]
    monkeypatch.setattr(full_video,'_merge_original_sound',
                        lambda job,segments,keys,**kwargs:
                        merged.append((segments,keys,kwargs)) or 'private/new.mp4')
    monkeypatch.setattr(full_video,'_deliver_once',
                        lambda job,key,**kw:delivered.append((key,kw)))
    assert qa_owner_approval.run(job)=='completed'
    assert len(merged)==1
    assert merged[0][1]==[original_key]
    assert merged[0][2]['force_no_warp'] is True
    assert len(delivered)==1
    assert 'FAILED' in delivered[0][1]['caption']
    assert delivered[0][1]['delivery_key'].endswith('owner_qa_override_delivery_v1')


def test_owner_override_never_changes_biometric_truth(monkeypatch):
    job=review_job()
    monkeypatch.setattr(db,'get_step',lambda *_:{'state':'COMPLETED'})
    monkeypatch.setattr(full_video,'_stored',lambda *_:True)
    monkeypatch.setattr(full_video,'_merge_original_sound',lambda *a,**kw:'file.mp4')
    monkeypatch.setattr(full_video,'_deliver_once',lambda *a,**kw:None)
    assert qa_owner_approval.run(job)=='completed'
    assert job['checkpoint']['quality_override']['type']=='owner_accepted_facial_mismatch'


@pytest.mark.parametrize('changes',[
    {'segment_total':2},
    {'checkpoint':{'quality_override':{'type':'owner_accepted_facial_mismatch',
                                       'owner_id':999,
                                       'source_step':full_video.step_key(0),
                                       'original_failure':'Nicole facial identity QA mismatch: 1/9'},
                   'local_preflight':{'complete':True,'source_key':'archive/source.mp4',
                                     'source_probe':{'duration':7.5}}}},
    {'stage':'AUTO_GENERATE'},
    {'state':'NEEDS_REVIEW'},
])
def test_invalid_override_fails_closed_without_delivery(monkeypatch,changes):
    job=review_job(**changes)
    monkeypatch.setattr(full_video,'_deliver_once',lambda *a,**kw:pytest.fail('No delivery'))
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *a,**kw:pytest.fail('No billing'))
    with pytest.raises(full_video.provider.NeedsReview):
        qa_owner_approval.run(job)


def test_worker_owner_approved_stage_uses_local_only_processor(monkeypatch):
    job=review_job()
    changes=[]
    monkeypatch.setattr(qa_owner_approval,'run',lambda job: 'completed')
    monkeypatch.setattr(full_video,'run_auto',lambda *_:pytest.fail('No Wan calls'))
    monkeypatch.setattr(db,'checkpoint',lambda *a,**kw:changes.append(kw) or True)
    app_queue.process(job)
    assert changes[0]['stage']=='DONE'
    assert changes[0]['state']=='COMPLETED'
    assert changes[0]['fields']['current_segment']==1


def test_qa_preview_and_approval_command_does_not_charge(monkeypatch):
    review=review_job(state='NEEDS_REVIEW',stage='AUTO_GENERATE')
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'qa_review_job',lambda owner,prefix:review)
    monkeypatch.setattr(telegram.db,'get_step',lambda *a:{'state':'COMPLETED'})
    monkeypatch.setattr(telegram.full_video if hasattr(telegram,'full_video') else full_video,
                        '_stored',lambda *_:True)
    monkeypatch.setattr(full_video.storage,'presign',lambda *a:'https://private.example/clip.mp4')
    sent=[];notes=[]
    monkeypatch.setattr(telegram,'telegram',lambda method,payload:sent.append((method,payload)))
    monkeypatch.setattr(telegram,'say',lambda chat,msg:notes.append(msg))
    monkeypatch.setattr(telegram.db,'approve_qa_review',lambda *a,**kw:{'state':'QUEUED'})
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *a,**kw:pytest.fail('No paid requests'))
    telegram.process_message({'chat':{'id':123},'from':{'id':123},'text':'/review_qa 59bc3ef4'})
    assert len(sent)==1 and sent[0][0]=='sendVideo'
    assert '/approve_qa 59bc3ef4' in sent[0][1]['caption']
    telegram.process_message({'chat':{'id':123},'from':{'id':123},'text':'/approve_qa 59bc3ef4'})
    assert 'No new Wan' in notes[-1]


def test_qa_preview_disallows_non_owner(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'qa_review_job',lambda *a:pytest.fail('Not authorized'))
    out=[]
    monkeypatch.setattr(telegram,'say',lambda *a:out.append(a))
    telegram.process_message({'chat':{'id':999},'from':{'id':999},'text':'/approve_qa 59bc3ef4'})
    assert 'private' in out[-1][1].lower()
