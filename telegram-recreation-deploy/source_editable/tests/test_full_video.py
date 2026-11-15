import pytest
from pathlib import Path
import shutil, subprocess
from recreation import full_video


def test_full_plan_covers_13_9_source_without_billing_first_segment_again():
    segs=full_video.plan(13.9,reuse_first=True)
    assert len(segs)==5
    assert [s['duration'] for s in segs]==[3.0,3.0,3.0,2.9,2.0]
    assert [s['output_seconds'] for s in segs]==[3,3,3,3,2]
    assert [s['start'] for s in segs]==[0,3,6,9,11.9]
    assert [s['reused'] for s in segs]==[True,False,False,False,False]
    assert full_video.estimate(segs)==1.10


@pytest.mark.parametrize('duration', [2,3,3.1,4,4.01,5,9.1,12.99,14.999,60])
def test_plan_no_gaps_and_minimum_two_seconds(duration):
    segs=full_video.plan(duration)
    assert all(s['duration']>=2 and s['output_seconds']>=2 for s in segs)
    assert all(s['output_seconds']<=30 for s in segs)
    assert all(abs(s['start']-round(sum(p['duration'] for p in segs[:i]),3))<.005 for i,s in enumerate(segs))
    assert abs(sum(s['duration'] for s in segs)-duration)<.005


@pytest.mark.parametrize('duration',[0,1.99,7201,float('nan'),float('inf'),None])
def test_plan_rejects_unsupported_duration(duration):
    with pytest.raises(ValueError):full_video.plan(duration)


def sample_job():
    return {'job_id':'a-uuid','owner_id':123,'chat_id':123,'state':'DRAFT',
            'character':'NICOLE','original_key':'original.mp4',
            'checkpoint':{'identity':{'status':'ready','nicole_image_key':'nicole.jpg'},
                'local_preflight':{'source_key':'original.mp4','complete':True,
                'source_probe':{'duration':13.9,'width':352,'height':624,'has_audio':True}}}}


def test_plan_requires_private_identity_and_original_audio():
    job=sample_job()
    from unittest.mock import patch
    with patch.object(full_video,'_reusable_canary',return_value=True):
        assert full_video.report(job)['remaining_estimate_usd']==1.10
    job['checkpoint']['identity']={}
    with pytest.raises(ValueError,match='identity'):full_video.report(job)
    job=sample_job();job['checkpoint']['local_preflight']['source_probe']['has_audio']=False
    with pytest.raises(ValueError,match='soundtrack'):full_video.report(job)


def test_full_video_cannot_run_using_old_canary_approval(monkeypatch):
    job=sample_job()
    monkeypatch.setattr(full_video,'_reusable_canary',lambda job:True)
    monkeypatch.setenv('WORKER_MODE','full_video_once')
    monkeypatch.setenv('FULL_VIDEO_ONE_SHOT','true')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','false')
    monkeypatch.setenv('FULL_VIDEO_SOURCE_JOB_ID','a-uuid')
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','123')
    monkeypatch.setenv('WAVESPEED_API_KEY','test-only')
    monkeypatch.delenv('FULL_VIDEO_APPROVAL_ID',raising=False)
    with pytest.raises(RuntimeError,match='cost approval'):full_video._guard_approval(job)
    monkeypatch.setenv('FULL_VIDEO_APPROVAL_ID',full_video.RUN_KEY)
    monkeypatch.delenv('FULL_VIDEO_MAX_USD',raising=False)
    with pytest.raises(RuntimeError,match='USD cap'):full_video._guard_approval(job)
    monkeypatch.setenv('FULL_VIDEO_MAX_USD','0.40')
    with pytest.raises(RuntimeError,match='exceeds approved'):full_video._guard_approval(job)
    monkeypatch.setenv('FULL_VIDEO_MAX_USD','1.50')
    assert full_video._guard_approval(job)['generated_sections']==4


def test_original_identity_canary_must_be_nicole_ref(monkeypatch):
    monkeypatch.setattr(full_video.db,'get_step',lambda *args:{'state':'COMPLETED','input':{'payload':{'reference_videos':['x'],'duration':3}}})
    with pytest.raises(RuntimeError,match='identity-specific'):full_video._get_reused_first('fake-job')


def test_preexisting_completed_segment_is_never_submitted_again(monkeypatch):
    job=sample_job()
    monkeypatch.setattr(full_video,'_reusable_canary',lambda job:True)
    info=full_video.report(job)
    info['segments']=info['segments'][:2]
    monkeypatch.setattr(full_video,'_guard_approval',lambda job:info)
    monkeypatch.setattr(full_video.db,'get_job',lambda *args:job)
    monkeypatch.setattr(full_video,'_get_reused_first',lambda x:'old_nicole.mp4')
    monkeypatch.setattr(full_video,'_stored',lambda key:False)
    monkeypatch.setattr(full_video.db,'get_step',lambda *args:{'state':'SUBMITTED','external_request_id':'saved-uuid',
      'job_id':'a-uuid','step_key':'saved-step','input':{'payload':{'reference_images':[
      'https://private.example/identity/nicole.jpg',
      'https://private.example/'+full_video.continuity_frame_key('a-uuid',0)]}}})
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *args:pytest.fail('a duplicate paid submission was attempted'))
    monkeypatch.setattr(full_video.provider,'poll',lambda step:{'status':'processing'})
    monkeypatch.setenv('FULL_VIDEO_SOURCE_JOB_ID','a-uuid')
    assert full_video.run_once()=='processing'


def test_delivery_is_never_repeated(monkeypatch):
    monkeypatch.setattr(full_video.db,'get_step',lambda *args:{'state':'COMPLETED'})
    monkeypatch.setattr(full_video.telegram,'telegram',lambda *args:pytest.fail('duplicate Telegram send'))
    full_video._deliver_once(sample_job(),'archived.mp4')


@pytest.mark.skipif(shutil.which('ffmpeg') is None,reason='ffmpeg unavailable')
def test_full_13_9_second_join_restores_original_sound(tmp_path):
    from recreation import ffmpeg
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','testsrc2=size=320x560:rate=24:duration=13.9',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=13.9',
        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(source)],check=True)
    clipped=[]
    for seg in full_video.plan(13.9):
        dest=tmp_path/f'seg_{seg["index"]}.mp4'
        ffmpeg.trim(source,dest,seg['start'],seg['duration'])
        clipped.append(dest)
    merged=tmp_path/'merged.mp4';final=tmp_path/'final.mp4'
    assert not ffmpeg.concatenate(clipped,merged)['has_audio']
    info=ffmpeg.restore_source_audio(merged,source,final)
    assert info['has_audio']
    assert abs(info['duration']-13.9)<0.2


def test_reuse_requires_same_saved_identity_and_source_age(monkeypatch):
    from datetime import datetime,timezone,timedelta
    from unittest.mock import MagicMock
    job=sample_job()
    now=datetime.now(timezone.utc)
    image='https://storage.example.org/storage/v1/s3/bucket/recreation/a-uuid/identity/nicole.jpg?signature=abc'
    video='https://storage.example.org/storage/v1/s3/bucket/recreation/a-uuid/canary/short_reference.mp4?signature=abc'
    step={'state':'COMPLETED','input':{'payload':{'reference_images':[image],
          'reference_videos':[video],'duration':3}},'created_at':now}
    monkeypatch.setattr(full_video.db,'get_step',lambda *a:step)
    monkeypatch.setattr(full_video,'_stored',lambda key:True)
    mock=MagicMock()
    mock.head_object.return_value={'LastModified':now-timedelta(hours=1)}
    monkeypatch.setattr(full_video.storage,'client',lambda:mock)
    monkeypatch.setattr(full_video.storage,'bucket',lambda:'stage-bucket')
    assert full_video._reusable_canary(job)
    # New identity reference should invalidate old generation reuse.
    job['checkpoint']['identity']['nicole_image_key']='identity/other.jpg'
    assert not full_video._reusable_canary(job)
    job['checkpoint']['identity']['nicole_image_key']='nicole.jpg'
    # Replacing the source video after the canary also invalidates reuse.
    mock.head_object.return_value={'LastModified':now+timedelta(hours=1)}
    assert not full_video._reusable_canary(job)


def test_no_paid_call_or_delivery_when_reuse_is_missing_and_no_approval(monkeypatch):
    job=sample_job()
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setenv('FULL_VIDEO_SOURCE_JOB_ID','a-uuid')
    monkeypatch.delenv('FULL_VIDEO_ONE_SHOT',raising=False)
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *a:pytest.fail('unauthorized provider call'))
    monkeypatch.setattr(full_video.telegram,'telegram',lambda *a:pytest.fail('unauthorized delivery'))
    with pytest.raises(RuntimeError):full_video.run_once()
