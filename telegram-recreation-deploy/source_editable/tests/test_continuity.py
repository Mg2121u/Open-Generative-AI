"""No WaveSpeed calls. Exercise real FFmpeg and mocked durability edges."""
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from recreation import ffmpeg, full_video


def test_new_run_has_distinct_approval_and_archive_namespace():
    assert full_video.RUN_KEY != 'nicole_full_480p_v1_20261115'
    assert 'continuity' in full_video.RUN_KEY
    assert full_video.continuity_frame_key('job',0).endswith('/continuity/last_frame_000.jpg')
    assert full_video.step_key(1).startswith('wan_'+full_video.RUN_KEY)
    assert full_video.estimate(full_video.plan(13.9,reuse_first=True))==1.10


def test_payload_includes_previous_generated_frame_separate_from_nicole_identity():
    p=full_video.continuity_payload('https://s.example/video.mp4',width=352,height=624,
       nicole_image_url='https://s.example/identity.jpg',last_frame_url='https://s.example/last_generated.jpg')
    assert p['reference_images']==['https://s.example/identity.jpg','https://s.example/last_generated.jpg']
    assert p['reference_videos']==['https://s.example/video.mp4']
    assert 'Image 1' in p['prompt'] and 'Image 2' in p['prompt'] and 'Video 1' in p['prompt']
    assert 'first_frame' not in p
    assert p['generate_audio'] is False
    assert p['resolution']=='480p'


def test_missing_previous_generated_video_fails_closed_before_download(monkeypatch):
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video.storage,'download_key',lambda *a:pytest.fail('missing video was downloaded'))
    monkeypatch.setattr(full_video.storage,'upload_key',lambda *a,**k:pytest.fail('missing video was uploaded'))
    with pytest.raises(RuntimeError,match='Previous GENERATED'):
        full_video._prepare_continuity_frame('job',0,'unavailable.mp4')


def test_last_frame_durably_archived_and_reused_without_second_download(tmp_path,monkeypatch):
    saved=[]; downloaded=[];objects={'generated.mp4'}
    previous=tmp_path/'real_generated.mp4'
    previous.write_bytes(b'a simulated private MP4')
    def storage_download(key,dest):
        downloaded.append(key)
        Path(dest).write_bytes(previous.read_bytes())
    monkeypatch.setattr(full_video,'_stored',lambda key:key in objects)
    monkeypatch.setattr(full_video.storage,'download_key',storage_download)
    monkeypatch.setattr(full_video.ffmpeg,'extract_last_frame',lambda src,dest:Path(dest).write_bytes(b'J'*900))
    def upload(key,file,mime=None):
        saved.append((key,mime,Path(file).read_bytes()));objects.add(key)
    monkeypatch.setattr(full_video.storage,'upload_key',upload)
    expected=full_video.continuity_frame_key('job',0)
    assert full_video._prepare_continuity_frame('job',0,'generated.mp4')==expected
    assert full_video._prepare_continuity_frame('job',0,'generated.mp4')==expected
    assert downloaded==['generated.mp4']
    assert saved==[(expected,'image/jpeg',b'J'*900)]


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg unavailable')
def test_frame_extraction_and_local_blend_enforces_first_frame_and_audio_sync(tmp_path):
    first=tmp_path/'first.mp4'; next_clip=tmp_path/'second.mp4'
    for path,color in [(first,'red'),(next_clip,'blue')]:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-f','lavfi','-i',f'color=c={color}:s=480x854:r=24:d=2.0',
            '-c:v','libx264','-preset','ultrafast',str(path)],check=True)
    end=tmp_path/'end.jpg';start=tmp_path/'start.jpg';out=tmp_path/'smooth.mp4'
    ffmpeg.extract_last_frame(first,end)
    ffmpeg.extract_first_frame(next_clip,start)
    assert ffmpeg.frame_difference(end,start)>0.3
    orig_duration=ffmpeg.probe(next_clip)['duration']
    smooth=ffmpeg.seam_blend_start(end,next_clip,out,seconds=0.20)
    ffmpeg.extract_first_frame(out,start)
    assert ffmpeg.frame_difference(end,start)<0.10
    assert abs(smooth['duration']-orig_duration)<0.08
    assert not smooth['has_audio']


def test_run_once_supplies_previous_generated_frame_for_next_paid_segment(monkeypatch):
    """Freeze before WaveSpeed. Capture exact submitted media roles without billing."""
    job={'job_id':'job','owner_id':123,'chat_id':123,'state':'DRAFT',
         'character':'NICOLE','original_key':'original.mp4',
         'checkpoint':{'identity':{'status':'ready','nicole_image_key':'identity/nicole.jpg'},
            'local_preflight':{'source_probe':{'width':352,'height':624},'complete':True}}}
    segments=[{'index':0,'reused':True,'duration':3,'output_seconds':3},
              {'index':1,'reused':False,'duration':3,'output_seconds':3,'estimated_usd':0.3}]
    info={'segments':segments}
    monkeypatch.setenv('FULL_VIDEO_SOURCE_JOB_ID','job')
    monkeypatch.setattr(full_video,'_guard_approval',lambda x:info)
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video,'_get_reused_first',lambda job_id:'OLD_GENERATED_NICOLE.mp4')
    monkeypatch.setattr(full_video,'_stored',lambda key:False)
    monkeypatch.setattr(full_video.db,'get_step',lambda *args:None)
    seen=[]
    def frame(job_id,idx,prev):
        seen.append(('previous_video',prev))
        return full_video.continuity_frame_key(job_id,idx)
    monkeypatch.setattr(full_video,'_prepare_continuity_frame',frame)
    monkeypatch.setattr(full_video,'_prepare_reference',lambda *a:'source_motion.mp4')
    monkeypatch.setattr(full_video.storage,'presign',lambda key,ttl:f'https://private.example/{key}')
    def fake_submit(job_id,step_key,endpoint,payload):
        seen.append(('payload',payload))
        return {'state':'SUBMITTED','job_id':'job','external_request_id':'saved','step_key':step_key,'input':{'payload':payload}}
    monkeypatch.setattr(full_video.provider,'submit_once',fake_submit)
    monkeypatch.setattr(full_video.provider,'poll',lambda step:{'status':'processing'})
    assert full_video.run_once()=='processing'
    assert ('previous_video','OLD_GENERATED_NICOLE.mp4') in seen
    payload=dict(seen)['payload']
    assert urlsplit(payload['reference_images'][0]).path.endswith('/identity/nicole.jpg')
    assert urlsplit(payload['reference_images'][1]).path.endswith('/continuity/last_frame_000.jpg')
    assert urlsplit(payload['reference_videos'][0]).path.endswith('/source_motion.mp4')


def test_restarted_submitted_step_must_preserve_previous_frame(monkeypatch):
    job={'job_id':'job','owner_id':123,'chat_id':123,'state':'DRAFT',
         'character':'NICOLE','original_key':'original.mp4',
         'checkpoint':{'identity':{'status':'ready','nicole_image_key':'identity/nicole.jpg'},
                       'local_preflight':{'source_probe':{'width':352,'height':624}}}}
    segments=[{'index':0,'reused':True}, {'index':1,'reused':False}]
    monkeypatch.setenv('FULL_VIDEO_SOURCE_JOB_ID','job')
    monkeypatch.setattr(full_video,'_guard_approval',lambda _: {'segments':segments})
    monkeypatch.setattr(full_video.db,'get_job',lambda *_:job)
    monkeypatch.setattr(full_video,'_get_reused_first',lambda *_:'previous.mp4')
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video.db,'get_step',lambda *a:{'state':'SUBMITTED','external_request_id':'existing',
      'input':{'payload':{'reference_images':['https://private.example/identity/nicole.jpg']}}})
    monkeypatch.setattr(full_video.provider,'submit_once',lambda *a:pytest.fail('must not pay again'))
    with pytest.raises(full_video.provider.NeedsReview,match='chained-frame'):
        full_video.run_once()

@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg unavailable')
def test_complete_13_9s_local_assembly_has_smooth_boundaries_and_original_audio(tmp_path,monkeypatch):
    """Five segments, non-paid FFmpeg operation, unchanged total time/audio."""
    source=tmp_path/'original.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','testsrc2=size=320x560:rate=24:duration=13.9',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=13.9',
        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast',
        '-c:a','aac',str(source)],check=True)
    job={'job_id':'smooth-job','original_key':'original.mp4'}
    segments=full_video.plan(13.9,reuse_first=True)
    generated={job['original_key']:source}
    keys=[]
    for seg in segments:
        dest=tmp_path/f'generated_{seg["index"]}.mp4'
        ffmpeg.trim(source,dest,seg['start'],seg['duration'])
        k=f'generated/{seg["index"]}.mp4';generated[k]=dest;keys.append(k)
    monkeypatch.setattr(full_video,'_stored',lambda key:False)
    monkeypatch.setattr(full_video.storage,'download_key',lambda key,dest:shutil.copyfile(generated[key],dest))
    saved={}
    def capture(key,src):
        path=tmp_path/'saved.mp4'
        shutil.copyfile(src,path)
        saved['path']=path
        return key
    monkeypatch.setattr(full_video.storage,'upload_key',capture)
    result=full_video._merge_original_sound(job,segments,keys)
    assert result==full_video.final_key('smooth-job')
    info=ffmpeg.probe(saved['path'])
    assert info['has_audio']
    assert info['width']==480 and info['height']==854
    assert abs(info['duration']-13.9)<0.20
