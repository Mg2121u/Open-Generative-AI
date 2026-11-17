"""Ensure small accumulated 24fps join overruns never discard paid clips."""
from pathlib import Path
import subprocess

import pytest
from recreation import ffmpeg, full_video


def test_stream_copy_trim_limits_overrun_without_video_reencode(tmp_path):
    source=tmp_path/'long.mp4'
    result=tmp_path/'corrected.mp4'
    subprocess.run([
        'ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','color=c=blue:s=96x160:r=24:d=3.0',
        '-c:v','libx264','-pix_fmt','yuv420p',str(source),
    ],check=True)
    original=source.read_bytes()
    info=ffmpeg.trim_video_stream_copy(source,result,2.783)
    assert result.is_file()
    assert info['has_audio'] is False
    assert abs(info['duration']-2.783)<.18
    assert source.read_bytes()==original


def test_assembled_video_repaired_to_source_clock_without_new_wan_jobs(tmp_path,monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE','identity_lock')
    monkeypatch.setenv('RECREATION_SEAM_MODE','cut_no_warp')
    monkeypatch.delenv('RECREATION_FINAL_SCRUB',raising=False)

    n=7
    source_seconds=57.533
    pieces=[{'index':i,'duration':source_seconds/n} for i in range(n)]
    keys=[f'paid-clips/{i}.mp4' for i in range(n)]
    job={'job_id':'unit-test','original_key':'private-original.mp4'}
    calls=[]
    monkeypatch.setattr(full_video,'_stored',lambda *_:False)
    monkeypatch.setattr(full_video.storage,'download_key',
                        lambda key,path:Path(path).write_bytes(b'x'))
    monkeypatch.setattr(full_video.storage,'upload_key',
                        lambda key,path: calls.append(('upload',key)))

    def probe(path):
        name=str(path)
        if name.endswith('silent_join.mp4'):
            return {'duration':57.750,'has_audio':False}
        if name.endswith('duration_corrected.mp4'):
            return {'duration':57.625,'has_audio':False}
        return {'duration':10.0,'has_audio':False}
    monkeypatch.setattr(ffmpeg,'probe',probe)
    monkeypatch.setattr(ffmpeg,'trim',lambda src,out,*a:Path(out).write_bytes(b'trim'))
    monkeypatch.setattr(ffmpeg,'extract_last_frame',
                        lambda src,out:Path(out).write_bytes(b'a'*900))
    monkeypatch.setattr(ffmpeg,'extract_first_frame',
                        lambda src,out:Path(out).write_bytes(b'b'*900))
    monkeypatch.setattr(ffmpeg,'frame_difference',lambda *a:0.05)
    monkeypatch.setattr(ffmpeg,'concatenate',
                        lambda paths,out:Path(out).write_bytes(b'joined'))
    def copytrim(src,out,duration):
        calls.append(('duration_corrected',round(duration,3)))
        Path(out).write_bytes(b'corrected')
        return {'duration':57.625,'has_audio':False}
    monkeypatch.setattr(ffmpeg,'trim_video_stream_copy',copytrim)
    def remux(video,source,out):
        calls.append(('original_audio_remux',Path(video).name))
        Path(out).write_bytes(b'final')
        return {'duration':57.625,'has_audio':True}
    monkeypatch.setattr(ffmpeg,'restore_source_audio',remux)
    monkeypatch.setattr(full_video.overlay_cleanup,'clean_motion_reference',
                        lambda *a:pytest.fail('Final scrub must be opt-in'))

    final=full_video._merge_original_sound(job,pieces,keys,motion_aware=True)
    assert final
    assert ('duration_corrected',57.533) in calls
    assert ('original_audio_remux','duration_corrected.mp4') in calls
    assert any(x[0]=='upload' for x in calls)
