"""Storage cap regression for a fully generated multi-section video.

Mock all external endpoints: neither tests nor recovery re-run paid Wan.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from recreation import storage,ffmpeg,db,telegram,final_archive_recovery,full_video,quality,app_queue


def test_storage_cap_defaults_to_safe_44mb(monkeypatch):
    monkeypatch.delenv('RECREATION_FINAL_S3_MAX_MB',raising=False)
    assert storage.final_video_max_bytes()==44_000_000
    monkeypatch.setenv('RECREATION_FINAL_S3_MAX_MB','85')
    assert storage.final_video_max_bytes()==85_000_000
    for invalid in ('0','invalid','-3','NaN','2200'):
        monkeypatch.setenv('RECREATION_FINAL_S3_MAX_MB',invalid)
        with pytest.raises(RuntimeError):
            storage.final_video_max_bytes()


def test_local_final_size_reencode_preserves_audio_and_dimensions(monkeypatch,tmp_path):
    original=tmp_path/'final.mp4'
    final=tmp_path/'storage_fitted.mp4'
    # Sparse mock file with a 15MB final.mp4 (no need for video inference).
    with original.open('wb') as output:
        output.truncate(15_000_000)
    probe_result={'duration':225.0,'width':480,'height':854,'has_audio':True}
    monkeypatch.setattr(ffmpeg,'probe',lambda path:probe_result)
    calls=[]
    def local_ffmpeg(*args,**kwargs):
        calls.append(args)
        # Unit test mocks ffmpeg encoding, not WaveSpeed calls.
        with Path(args[-1]).open('wb') as dst:
            dst.truncate(10_000_000)
    monkeypatch.setattr(ffmpeg,'run',local_ffmpeg)
    result=ffmpeg.fit_mp4_under_storage_limit(original,final,max_bytes=12_000_000)
    assert result['encoded']
    assert result['before_bytes']==15_000_000
    assert result['after_bytes']==10_000_000
    assert '-map' in calls[0]
    assert '0:a:0' in calls[0]
    assert 'libx264' in calls[0]
    assert 'aac' in calls[0]
    assert '--' not in calls[0]


def job():
    jobid='32e09922-09c0-4729-bf29-a7455b51e614'
    return {
       'job_id':jobid,'owner_id':123,'chat_id':123,
       'state':'RUNNING','stage':'FINAL_ASSEMBLE','model':'wan-3.0',
       'character':'NICOLE','segment_total':27,'original_key':'archive/original.mp4',
       'checkpoint':{
          'identity':{'status':'ready','nicole_image_key':'photos/nicole.jpg'},
          'face_qa_approvals':{str(n):{'owner_id':123} for n in range(15)},
          'local_preflight':{'complete':True,'source_key':'archive/original.mp4',
             'source_probe':{'duration':224.489,'width':480,'height':854,
                             'has_audio':True}}}
    }


def test_finished_27_sections_locally_finalized_without_paid_requests(monkeypatch):
    j=job()
    segments=[{'index':i,'start':i*8,'duration':8,'output_seconds':8,
               'estimated_usd':.8} for i in range(27)]
    monkeypatch.setattr(quality,'quality_plan',lambda duration:segments)
    monkeypatch.setattr(db,'auto_provider_steps',lambda jid:[
        {'step_key':full_video.step_key(i),'state':'COMPLETED',
         'external_request_id':'paid-'+str(i)} for i in range(27)])
    monkeypatch.setattr(full_video,'_stored',lambda key:True)
    merged=[];delivered=[]
    def merge(job,segments,keys,**kwargs):
        merged.append((keys,kwargs))
        return 'archive/final_fixed.mp4'
    monkeypatch.setattr(full_video,'_merge_original_sound',merge)
    monkeypatch.setattr(full_video,'_deliver_once',
                        lambda job,key:delivered.append(key))
    monkeypatch.setattr(full_video.provider,'submit_once',
                        lambda *a,**kw:pytest.fail('No model provider call permitted'))
    assert final_archive_recovery.run(j)=='completed'
    assert len(merged)==1
    assert len(merged[0][0])==27
    assert merged[0][1]['force_no_warp'] is True
    assert delivered==['archive/final_fixed.mp4']
    assert len(j['checkpoint']['face_qa_approvals'])==15


def test_incomplete_provider_sections_block_local_finalize(monkeypatch):
    j=job()
    monkeypatch.setattr(quality,'quality_plan',
                        lambda _:[{'index':i} for i in range(27)])
    monkeypatch.setattr(db,'auto_provider_steps',lambda _:[
        {'step_key':full_video.step_key(i),'state':'COMPLETED',
         'external_request_id':'id'} for i in range(26)])
    monkeypatch.setattr(full_video,'_merge_original_sound',
                        lambda *a,**kw:pytest.fail('Must not join incomplete archive'))
    with pytest.raises(full_video.provider.NeedsReview,match='EVERY'):
        final_archive_recovery.run(j)


def test_final_archive_worker_never_enters_paid_wan_route(monkeypatch):
    j=job()
    monkeypatch.setattr(final_archive_recovery,'run',lambda _: 'completed')
    monkeypatch.setattr(full_video,'run_auto',
        lambda *args:pytest.fail('Do not enter paid generation route'))
    updates=[]
    monkeypatch.setattr(db,'checkpoint',
        lambda *a,**kw:updates.append(kw) or True)
    j['lease_owner']='worker-local'
    app_queue.process(j)
    assert updates[-1]['stage']=='DONE'
    assert updates[-1]['state']=='COMPLETED'
    assert updates[-1]['fields']['current_segment']==27


def test_owner_only_finish_claim_validates_all_paid_sections_sql(monkeypatch):
    statements=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,sql,args):
            statements.append((sql,args))
            return SimpleNamespace(fetchone=lambda:{'job_id':job()['job_id'],
                                                     'segment_total':27})
    monkeypatch.setattr(db,'connect',lambda:Connection())
    result=db.queue_final_assembly(123,'32e09922')
    assert result['segment_total']==27
    sql,params=statements[0]
    assert "stage='FINAL_ASSEMBLE'" in sql
    assert "s.state='COMPLETED'" in sql
    assert "s.state!='COMPLETED'" in sql
    assert "lease_until IS NULL OR j.lease_until<now()" in sql
    assert 'S3UploadFailedError' in sql
    assert params[0]==123 and params[1]=='32e09922'
    with pytest.raises(ValueError):
        db.queue_final_assembly(123,'not-a-job')
    assert len(statements)==1


def test_owner_finish_command_queues_local_only(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'queue_final_assembly',
                        lambda owner,prefix: {
                          'job_id':job()['job_id'],'segment_total':27})
    notices=[]
    monkeypatch.setattr(telegram,'say',lambda chat,msg:notices.append(msg))
    telegram.process_message({'from':{'id':123},'chat':{'id':123},
        'text':'/finish 32e09922'})
    assert 'No' not in notices[-1] or 'no paid' not in notices[-1].lower()
    assert '27 paid Wan sections' in notices[-1]
    assert 'none will be regenerated' in notices[-1]


def test_finish_command_does_not_allow_unauthorized_fan(monkeypatch):
    monkeypatch.setattr(telegram,'ALLOWED_USERS',{123})
    monkeypatch.setattr(telegram.db,'queue_final_assembly',
        lambda *a:pytest.fail('Unauthorized actor must not reach storage'))
    messages=[]
    monkeypatch.setattr(telegram,'say',
        lambda chat,msg:messages.append(msg))
    telegram.process_message({'from':{'id':999},'chat':{'id':999},
                              'text':'/finish 32e09922'})
    assert 'private' in messages[-1].lower()
