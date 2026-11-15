"""Crash/resume and FFmpeg tests for the non-paid dedicated worker path."""
import os
import shutil
import subprocess
import uuid
from pathlib import Path
import pytest
from recreation import preflight, db


def test_clip_plan_is_bounded_and_ordered():
    assert preflight.plan(10) == [
        {'start':0.0,'duration':4.0},
        {'start':4.0,'duration':4.0},
        {'start':8.0,'duration':2.0}]
    assert len(preflight.plan(250)) == 3
    with pytest.raises(ValueError): preflight.plan(0)
    with pytest.raises(ValueError): preflight.plan(float('nan'))
    with pytest.raises(ValueError): preflight.plan(7201)


def test_preflight_requires_archived_source():
    with pytest.raises(ValueError):
        preflight.run_step({'job_id':uuid.uuid4(),'original_key':None})


def test_archival_preview_recovers_after_each_checkpoint(tmp_path,monkeypatch):
    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        pytest.skip('FFmpeg binary not installed')
    original=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
          '-f','lavfi','-i','testsrc2=size=160x284:rate=12',
          '-t','8.4','-c:v','libx264','-preset','ultrafast',str(original)],
          check=True,timeout=60)
    objects={'test/original.mp4':original.read_bytes()}

    def download(key,path): Path(path).write_bytes(objects[key])
    def upload(key,path,mime='video/mp4'): objects[key]=Path(path).read_bytes()
    monkeypatch.setattr(preflight.storage,'download_key',download)
    monkeypatch.setattr(preflight.storage,'upload_key',upload)
    monkeypatch.setattr(preflight.telegram,'say',lambda *args: None)
    updates=[]
    def checkpoint(job_id,worker,**kwargs):
        updates.append(kwargs)
        return {'job_id':job_id,'state':kwargs.get('state')}
    monkeypatch.setattr(preflight.db,'checkpoint',checkpoint)
    job={'job_id':str(uuid.uuid4()),'chat_id':1,'lease_owner':'test-worker',
         'original_key':'test/original.mp4','checkpoint':{}}
    # Each new run reconstructs job state from durable checkpoint: simulates a crash.
    for _ in range(5):
        preflight.run_step(job)
        last=updates[-1]
        job={**job,'checkpoint':last['fields']['checkpoint']}
    assert len(updates)==5
    assert [x['state'] for x in updates]==['QUEUED']*4+['DRAFT']
    assert updates[-1]['stage']=='UPLOAD_REFERENCE'
    cp=updates[-1]['fields']['checkpoint']['local_preflight']
    assert cp['complete'] is True
    assert len(cp['segment_keys'])==3
    assert objects[cp['merged_key']][:4]!=b''
    merged=tmp_path/'merged.mp4';merged.write_bytes(objects[cp['merged_key']])
    info=preflight.ffmpeg.probe(merged)
    assert info['duration']>8.0
    assert info['width']==480


def test_reupload_invalidates_old_preflight_checkpoint():
    import inspect
    source=inspect.getsource(db.draft_update)
    assert "checkpoint=jsonb_build_object('identity'" in source
    assert "original_key=NULL" in source
    assert "original_key=NULL" in source


def test_safe_worker_claim_sql_never_selects_provider_stages(monkeypatch):
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def execute(self,sql,args):
            assert "stage='LOCAL_PREFLIGHT'" in sql
            assert 'WAN_SUBMIT' not in sql
            assert args[0]==[8321358143]
            return self
        def fetchone(self):return {'job_id': 'test'}
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','8321358143')
    monkeypatch.setattr(db,'connect',lambda: Conn())
    assert db.claim_local_preflight('preflight-worker')['job_id']=='test'
