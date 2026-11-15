"""Non-paid motion-only remux tests. No provider endpoints are called."""
import shutil
import subprocess
from pathlib import Path
import pytest
from recreation import ffmpeg,full_video,motion_remux


def job():
    return {'job_id':'job','owner_id':123,'chat_id':123,'state':'DRAFT',
            'character':'NICOLE','original_key':'orig.mp4'}


def test_motion_remux_guard_never_accepts_paid_flags(monkeypatch):
    monkeypatch.setenv('WORKER_MODE','motion_remux_once')
    monkeypatch.setenv('MOTION_REMUX_ONE_SHOT','true')
    monkeypatch.setenv('MOTION_REMUX_JOB_ID','job')
    monkeypatch.setenv('ALLOWED_TELEGRAM_USER_IDS','123')
    monkeypatch.setenv('ENABLE_PAID_GENERATION','true')
    with pytest.raises(RuntimeError,match='refuses any paid'):
        motion_remux.guard(job())
    monkeypatch.setenv('ENABLE_PAID_GENERATION','false')
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','true')
    with pytest.raises(RuntimeError,match='refuses paid voice'):
        motion_remux.guard(job())
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','false')
    monkeypatch.setattr(full_video,'report',lambda *_:{'source_duration':13.9,'segments':[{'index':0}], 'reused_first_canary':True})
    assert motion_remux.guard(job())['source_duration']==13.9


def test_remux_requires_five_previously_generated_clips_and_never_pays(monkeypatch):
    monkeypatch.setenv('MOTION_REMUX_JOB_ID','job')
    monkeypatch.setattr(motion_remux.db,'get_job',lambda *_:job())
    segments=[{'index':i,'reused':i==0} for i in range(5)]
    monkeypatch.setattr(motion_remux,'guard',lambda job: {'segments':segments,'reused_first_canary':True})
    monkeypatch.setattr(full_video,'_get_reused_first',lambda job:'saved_canary.mp4')
    monkeypatch.setattr(full_video,'_stored',lambda key:True)
    calls=[]
    monkeypatch.setattr(motion_remux.db,'get_step',lambda job,key:{'state':'COMPLETED'})
    def fake_merge(job,segs,keys,**kwargs):
        calls.append((keys,kwargs))
    monkeypatch.setattr(full_video,'_merge_original_sound',fake_merge)
    monkeypatch.setattr(motion_remux,'_deliver_once',lambda job,key:'sent')
    assert motion_remux.run_once()=='completed'
    # Existing output skips repeated rendering and delivery is idempotent.
    assert not calls
    assert motion_remux.destination('job').endswith('/nicole_full_480p_motion_v3_20261115/nicole_with_original_audio.mp4')
    assert motion_remux.source_segment('job',2).endswith('/nicole_full_480p_continuity_v2_20261115/segments/002.mp4')


@pytest.mark.skipif(shutil.which('ffmpeg') is None,reason='Requires FFmpeg')
def test_motion_bridge_has_no_freeze_and_keeps_source_duration(tmp_path):
    import cv2
    prev=tmp_path/'prev.mp4';nextc=tmp_path/'next.mp4';out=tmp_path/'out.mp4'
    for path,saturation in [(prev,'1'),(nextc,'0.8')]:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-f','lavfi','-i','testsrc2=size=320x560:rate=24:duration=1.25',
            '-vf',f'hue=s={saturation}',
            '-c:v','libx264','-preset','ultrafast',str(path)],check=True)
    result=ffmpeg.motion_interpolate_start(prev,nextc,out,seconds=.25)
    assert abs(result['duration']-ffmpeg.probe(nextc)['duration'])<.08
    assert result['frames_written']>=29
    assert result['bridge_frames']==6
    end=tmp_path/'prevend.jpg';start=tmp_path/'outstart.jpg'
    ffmpeg.extract_last_frame(prev,end);ffmpeg.extract_first_frame(out,start)
    assert ffmpeg.frame_difference(end,start)<0.08
    motion=ffmpeg.seam_motion_metrics(prev,out)
    assert motion['first_frame_motion']>0.0005
    assert not result['has_audio']


@pytest.mark.skipif(shutil.which('ffmpeg') is None,reason='Requires FFmpeg')
def test_13_9s_motion_only_assembly_retains_original_audio_and_timing(tmp_path,monkeypatch):
    source=tmp_path/'original.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','testsrc2=size=320x560:rate=24:duration=13.9',
        '-f','lavfi','-i','sine=frequency=480:sample_rate=44100:duration=13.9',
        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast',
        '-c:a','aac',str(source)],check=True)
    segments=full_video.plan(13.9,reuse_first=True)
    keys=[];objects={'original.mp4':source}
    for s in segments:
        clip=tmp_path/f'clip_{s["index"]}.mp4'
        ffmpeg.trim(source,clip,s['start'],s['duration'])
        key=f'clip_{s["index"]}.mp4';keys.append(key);objects[key]=clip
    monkeypatch.setattr(full_video,'_stored',lambda _:False)
    monkeypatch.setattr(full_video.storage,'download_key',lambda key,dest:shutil.copy2(objects[key],dest))
    saved={}
    def save(key,file):
        dest=tmp_path/'final_saved.mp4';shutil.copy2(file,dest);saved['result']=dest
    monkeypatch.setattr(full_video.storage,'upload_key',save)
    key='recreation/job/full_video/motion_v3/final.mp4'
    assert full_video._merge_original_sound({'job_id':'job','original_key':'original.mp4'},segments,keys,
           destination_override=key,motion_aware=True)==key
    result=ffmpeg.probe(saved['result'])
    assert result['has_audio']
    assert (result['width'],result['height'])==(480,854)
    assert abs(result['duration']-13.9)<0.2
