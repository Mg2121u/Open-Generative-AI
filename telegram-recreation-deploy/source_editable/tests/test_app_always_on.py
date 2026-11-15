"""Always-on job integration tests: no real provider or network calls."""
import pytest
from recreation import worker,db,full_video,app_queue,telegram


def fake_job(**kwargs):
    x={'job_id':'job-uuid','owner_id':123,'chat_id':123,'character':'NICOLE',
       'state':'RUNNING','stage':'AUTO_GENERATE','lease_owner':'worker-a',
       'segment_total':5,'source_file_id':'telegram-video','original_key':'recreation/job-uuid/original.mp4',
       'checkpoint':{'identity':{'status':'ready','nicole_image_key':'recreation/job-uuid/identity/nicole.jpg'},
       'local_preflight':{'source_key':'recreation/job-uuid/original.mp4','complete':True,
                          'source_probe':{'duration':13.9,'width':352,'height':624,'has_audio':True}}}}
    x.update(kwargs);return x


def test_app_guard_owner_only_without_daily_job_quota(monkeypatch):
    monkeypatch.setenv('WORKER_MODE','app_always_on')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','false')
    monkeypatch.setenv('WAVESPEED_API_KEY','dummy')
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','123')
    monkeypatch.setenv('APP_MAX_ESTIMATED_USD','10.00')
    monkeypatch.setattr(full_video,'report',lambda x:{'segments':[], 'remaining_estimate_usd':2.50})
    assert full_video._guard_auto(fake_job())['remaining_estimate_usd']==2.50
    monkeypatch.setattr(full_video,'report',lambda x:{'segments':[], 'remaining_estimate_usd':12345})
    with pytest.raises(full_video.provider.NeedsReview,match='exceeds'):
        full_video._guard_auto(fake_job())
    with pytest.raises(RuntimeError,match='owner'):
        full_video._guard_auto(fake_job(owner_id=999))
    with pytest.raises(RuntimeError,match='owner'):
        full_video._guard_auto(fake_job(state='DRAFT'))
    monkeypatch.setenv('ENABLE_PAID_GENERATION','false')
    with pytest.raises(RuntimeError,match='not enabled'):
        full_video._guard_auto(fake_job())


def test_first_new_job_segment_is_nicole_and_source_only(monkeypatch):
    job=fake_job()
    segment={'index':0,'reused':False,'start':0,'duration':3,'output_seconds':3,'estimated_usd':.3}
    monkeypatch.setattr(full_video,'_guard_auto',lambda _:{'segments':[segment]})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video.db,'get_step',lambda *_:None)
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video,'_prepare_reference',lambda *_:'recreation/job-uuid/input_000.mp4')
    monkeypatch.setattr(full_video,'_prepare_continuity_frame',lambda *_:pytest.fail('cannot use preceding frame for first segment'))
    monkeypatch.setattr(full_video.storage,'presign',lambda key,ttl:f'https://store.example/{key}')
    captured=[]
    def submit(j,k,model,payload):
        captured.append(payload)
        return {'state':'SUBMITTED','external_request_id':'pred1','input':{'payload':payload}}
    monkeypatch.setattr(full_video.provider,'submit_once',submit)
    monkeypatch.setattr(full_video.provider,'poll',lambda *_:{'status':'processing'})
    assert full_video.run_auto(job)=='processing'
    assert len(captured)==1
    assert len(captured[0]['reference_images'])==1
    assert captured[0]['reference_images'][0].endswith('/identity/nicole.jpg')
    assert len(captured[0]['reference_videos'])==1


def test_auto_worker_stage_progress_and_completion(monkeypatch):
    job=fake_job()
    progress=[]
    monkeypatch.setattr(full_video,'run_auto',lambda _: 'processing')
    monkeypatch.setattr(db,'count_auto_segments',lambda *_:2)
    monkeypatch.setattr(db,'checkpoint',lambda *a,**kw:progress.append(kw))
    app_queue.process(job)
    assert progress[-1]['state']=='WAITING_PROVIDER'
    assert progress[-1]['fields']['current_segment']==2
    assert progress[-1]['delay_seconds']==12
    monkeypatch.setattr(full_video,'run_auto',lambda _: 'completed')
    app_queue.process(job)
    assert progress[-1]['state']=='COMPLETED'


def test_resume_cannot_duplicate_completed_step(monkeypatch):
    job=fake_job()
    info={'segments':[{'index':0,'reused':False,'duration':3}]}
    monkeypatch.setattr(full_video,'_guard_auto',lambda _:info)
    monkeypatch.setattr(full_video,'_stored',lambda _:True)
    monkeypatch.setattr(full_video,'_merge_original_sound',lambda *a,**kw:'final.mp4')
    monkeypatch.setattr(full_video,'_deliver_once',lambda *a:None)
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *a:pytest.fail('duplicate charge'))
    assert full_video.run_auto(job)=='completed'


def test_new_jobs_are_not_capped_to_60_seconds():
    segments=full_video.plan(125.2)
    assert len(segments)>24
    assert abs(sum(s['duration'] for s in segments)-125.2)<0.01
