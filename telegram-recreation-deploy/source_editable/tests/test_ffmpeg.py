from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import shutil
import pytest
from recreation.ffmpeg import probe,trim,concatenate,restore_source_audio

@pytest.mark.skipif(shutil.which('ffmpeg') is None,reason='ffmpeg unavailable')
def test_video_segments_and_original_audio():
    with TemporaryDirectory() as tmp:
        p=Path(tmp)
        original=p/'original.mp4'; first=p/'first.mp4';second=p/'second.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-f','lavfi','-i','testsrc2=size=320x570:rate=24:duration=2',
            '-f','lavfi','-i','sine=frequency=500:sample_rate=44100:duration=2',
            '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(original)],check=True)
        trim(original,first,0,1)
        trim(original,second,1,1)
        assert not probe(first)['has_audio']
        merged=p/'merged.mp4'
        info=concatenate([first,second],merged)
        assert not info['has_audio']
        assert abs(info['duration']-2)<0.2
        final=p/'final.mp4'
        restored=restore_source_audio(merged,original,final)
        assert restored['has_audio']
        assert abs(restored['duration']-2)<0.2
        assert restored['width']==480


def test_invalid_trim():
    with pytest.raises(ValueError):
        trim('nonexistent.mp4','out.mp4',-1,1)
