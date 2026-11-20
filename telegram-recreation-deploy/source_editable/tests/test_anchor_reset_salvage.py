"""Regression: prevent cumulative drift and recover paid v3 outputs without billing."""
from pathlib import Path
import pytest
from recreation import quality, full_video, app_queue, db


def prepared_job(**overrides):
    job={
        'job_id':'quality-example-job', 'owner_id':123, 'chat_id':123,
        'state':'RUNNING','stage':'AUTO_GENERATE','character':'NICOLE',
        'original_key':'recreation/quality-example-job/original.mp4',
        'lease_owner':'worker-1','segment_total':2,
        'scenery':'keep source scenery','outfit':'keep source clothing',
        'checkpoint':{
            'identity':{'status':'ready','nicole_image_key':'identity/nicole.jpg'},
            'local_preflight':{
                'source_key':'recreation/quality-example-job/original.mp4',
                'complete':True,
                'source_probe':{'duration':17.0,'width':480,'height':854,'has_audio':True},
            },
        },
    }
    job.update(overrides)
    return job


def test_canonical_photo_used_for_later_segment_without_previous_wan_frame(monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    job=prepared_job()
    segments=[
        {'index':0,'start':0,'duration':8.5,'output_seconds':9,'estimated_usd':.9,'reused':False},
        {'index':1,'start':8.5,'duration':8.5,'output_seconds':9,'estimated_usd':.9,'reused':False},
    ]
    monkeypatch.setattr(full_video,'_guard_auto',lambda _: {'segments':segments})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video,'_stored',lambda key:key.endswith('/segments/000.mp4'))
    monkeypatch.setattr(full_video.db,'get_step',lambda *a:None)
    monkeypatch.setattr(full_video,'_prepare_reference',lambda *a:'saved_source_interval.mp4')
    monkeypatch.setattr(full_video,'_prepare_continuity_frame',
                        lambda *a:pytest.fail('MUST NOT CHAIN Wan last frame'))
    monkeypatch.setattr(full_video.storage,'presign',
                        lambda key,ttl:'https://private.example/'+key)
    captured=[]
    def no_real_provider(job_id,step_key,endpoint,payload):
        captured.append(payload)
        return {'state':'SUBMITTED','external_request_id':'test-not-billable',
                'job_id':job_id,'step_key':step_key,
                'input':{'payload':payload}}
    monkeypatch.setattr(full_video.provider,'submit_once',no_real_provider)
    monkeypatch.setattr(full_video.provider,'poll',lambda step:{'status':'processing'})
    assert full_video.run_auto(job)=='processing'
    assert len(captured)==1
    p=captured[0]
    assert len(p['reference_images'])==1
    assert p['reference_images'][0].endswith('/identity/nicole.jpg')
    assert p['reference_videos']==['https://private.example/saved_source_interval.mp4']
    assert 'NO previous generated frame' in p['prompt']
    assert 'Copy the complete wardrobe from Video 1' in p['prompt']
    assert 'Keep the original background' in p['prompt']
    assert p['seed']==quality.fixed_seed(job)
    assert p['generate_audio'] is False


def test_saved_canonical_prediction_never_submitted_twice(monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_IDENTITY_ANCHOR','canonical')
    job=prepared_job()
    segments=[{'index':0,'reused':False},
              {'index':1,'reused':False}]
    monkeypatch.setattr(full_video,'_guard_auto',lambda _: {'segments':segments})
    monkeypatch.setattr(full_video,'_stored',lambda key:key.endswith('/segments/000.mp4'))
    monkeypatch.setattr(full_video.db,'get_step',lambda job_id,key:{
        'state':'SUBMITTED','job_id':job_id,'step_key':key,
        'external_request_id':'already-paid',
        'input':{'payload':{'reference_images':
                           ['https://private.example/identity/nicole.jpg']}}
    })
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a,**k:pytest.fail('duplicate paid submission'))
    monkeypatch.setattr(full_video.provider,'poll',lambda *a:{'status':'processing'})
    assert full_video.run_auto(job)=='processing'


def test_long_six_segment_plan_stays_under_wan_reference_limits():
    chunks=quality.quality_plan(49.766667)
    assert len(chunks)==6
    assert 49.76<sum(x['duration'] for x in chunks)<49.78
    assert all(2<=s['duration']<=8.5 and
               s['duration']+s['output_seconds']<=30 for s in chunks)
    assert all(s['reused'] is False for s in chunks)


def test_local_salvage_uses_completed_old_outputs_and_never_calls_provider(monkeypatch):
    old=full_video.SALVAGE_SOURCE_RUN_KEY
    job=prepared_job(stage='SALVAGE',state='RUNNING',segment_total=13)
    job['checkpoint']['local_preflight']['source_probe']['duration']=49.766667
    keys=[]
    monkeypatch.setattr(full_video.db,'get_step',
                        lambda jid,key:{'state':'COMPLETED','step_key':key,
                                        'job_id':jid})
    monkeypatch.setattr(full_video,'_stored',lambda key:True)
    def merge(job,segs,objects,**kwargs):
        assert len(segs)==13
        assert len(objects)==13
        assert all(f'/{old}/' in k for k in objects)
        assert kwargs['force_no_warp'] is True
        keys.extend(objects)
        return kwargs['destination_override']
    monkeypatch.setattr(full_video,'_merge_original_sound',merge)
    sent=[]
    monkeypatch.setattr(full_video,'_deliver_once',
                        lambda job,key,**k:sent.append((key,k)))
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a,**k:pytest.fail('NO paid provider calls'))
    monkeypatch.setattr(full_video.provider,'poll',
                        lambda *a,**k:pytest.fail('NO provider polling'))
    assert full_video.salvage_existing_v3(job)=='completed'
    assert len(keys)==13
    assert len(sent)==1
    assert 'local_hardcuts_v1' in sent[0][0]
    assert 'no ' in sent[0][1]['caption'].lower() or 'no' in sent[0][1]['caption'].lower()


def test_missing_salvage_output_fails_closed_without_regeneration(monkeypatch):
    job=prepared_job(stage='SALVAGE',state='RUNNING',segment_total=13)
    job['checkpoint']['local_preflight']['source_probe']['duration']=49.766667
    monkeypatch.setattr(full_video.db,'get_step',
                        lambda *a:{'state':'COMPLETED'})
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a:pytest.fail('Do not generate again'))
    with pytest.raises(full_video.provider.NeedsReview,match='missing from storage'):
        full_video.salvage_existing_v3(job)


def test_salvage_worker_routes_away_from_paid_generation(monkeypatch):
    job=prepared_job(stage='SALVAGE',state='RUNNING',segment_total=13)
    called=[]
    monkeypatch.setattr(full_video,'salvage_existing_v3',
                        lambda job: called.append('local') or 'completed')
    monkeypatch.setattr(full_video,'run_auto',
                        lambda *a:pytest.fail('must not generate'))
    monkeypatch.setattr(db,'checkpoint',lambda *a,**kw:called.append(kw) or True)
    app_queue.process(job)
    assert called[0]=='local'
    assert called[1]['state']=='COMPLETED'


def test_salvage_sends_one_video_under_new_delivery_key(monkeypatch):
    job=prepared_job()
    monkeypatch.setattr(full_video.db,'get_step',lambda *a:None)
    monkeypatch.setattr(full_video.db,'reserve_one_shot',lambda *a:True)
    monkeypatch.setattr(full_video.db,'external_finished',lambda *a:True)
    sent=[]
    monkeypatch.setattr(full_video.telegram,'send_archived_video',
                        lambda chat,key,**kw:sent.append((chat,key,kw)) or {'message_id':42})
    full_video._deliver_once(job,'archive.mp4',delivery_key='local_salvage_delivery',
                             caption='Recovered, no provider generation')
    assert len(sent)==1
    assert sent[0][0]==job['chat_id']
    assert sent[0][1]=='archive.mp4'
    assert sent[0][2]['caption']=='Recovered, no provider generation'
