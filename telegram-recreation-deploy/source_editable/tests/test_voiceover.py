"""ElevenLabs v4 identity, opt-in and paid-generation recovery tests, fully offline."""
from pathlib import Path

import pytest

from recreation import voiceover, ffmpeg, pipeline


def test_saved_voices_are_distinct_and_v4(monkeypatch):
    monkeypatch.delenv('ELEVENLABS_VOICE_ID_NICOLE', raising=False)
    monkeypatch.delenv('ELEVENLABS_VOICE_ID_ARIA', raising=False)
    assert voiceover.MODEL_ID == 'eleven_v4'
    assert voiceover.voice_id_for('NICOLE') == ('NICOLE', 'DWQWHtcQpAHZJPTy19u1')
    assert voiceover.voice_id_for('ARIA') == ('ARIA', 'CLyJ3auZqUC7B8ZKF0cF')
    assert voiceover.voice_id_for('ARIA_NICOLE', 'ARIA')[0] == 'ARIA'
    assert voiceover.voice_id_for('ARIA_NICOLE', 'NICOLE')[0] == 'NICOLE'


@pytest.mark.parametrize('character,requested', [
    ('NICOLE', 'ARIA'), ('ARIA', 'NICOLE'), ('ARIA_NICOLE', 'AUTO')
])
def test_no_voice_identity_mixup(character, requested):
    with pytest.raises(ValueError):
        voiceover.voice_id_for(character, requested)


def test_reuses_completed_audio_without_billing(monkeypatch):
    monkeypatch.setattr(voiceover.db, 'get_step', lambda *args: {
        'state': 'COMPLETED', 'result': {'audio_key': 'existing/voice.mp3'}})
    def never_post(*args, **kwargs):
        pytest.fail('Already charged voice generated twice!')
    monkeypatch.setattr(voiceover.requests, 'post', never_post)
    assert voiceover.synthesize_once('example-job', 'NICOLE', 'AUTO', 'Hello habibi') == 'existing/voice.mp3'


def test_uncertain_voice_request_is_not_resubmitted(monkeypatch):
    monkeypatch.setattr(voiceover.db, 'get_step', lambda *args: {'state': 'SUBMITTING'})
    monkeypatch.setattr(voiceover.requests, 'post', lambda *a, **k: pytest.fail('Double billed'))
    with pytest.raises(voiceover.NeedsReview):
        voiceover.synthesize_once('example-job', 'ARIA', 'AUTO', 'Hello')


def test_disabled_voice_makes_no_paid_request(monkeypatch):
    monkeypatch.setattr(voiceover.db, 'get_step', lambda *args: None)
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION', 'false')
    monkeypatch.setattr(voiceover.requests, 'post', lambda *a, **k: pytest.fail('Paid voice request when disabled'))
    with pytest.raises(voiceover.VoiceConfigurationError):
        voiceover.synthesize_once('example-job', 'ARIA', 'AUTO', 'Hello')


def test_elevenlabs_request_uses_v4_and_correct_voice(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION', 'true')
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'fake-test-api-key')
    monkeypatch.setattr(voiceover.db, 'get_step', lambda *args: None)
    records=[]
    monkeypatch.setattr(voiceover.db, 'reserve_one_shot',lambda *args: (records.append(('intent',args)) or True))
    monkeypatch.setattr(voiceover.db, 'external_finished',lambda *args: records.append(('completed',args)))
    monkeypatch.setattr(voiceover.storage, 'upload_key',lambda key,path,mime: records.append(('upload',key,bytes(Path(path).read_bytes()),mime)))
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self,chunk_size): yield b'ID3'+b'0'*256
    def fake_post(url, **kwargs):
        records.append(('post',url,kwargs))
        return FakeResponse()
    monkeypatch.setattr(voiceover.requests, 'post', fake_post)
    key=voiceover.synthesize_once('job-123','NICOLE','AUTO','Habibi, hi!')
    assert key=='recreation/job-123/voice/elevenlabs_nicole_v4.mp3'
    assert records[0][0]=='intent'
    assert records[1][0]=='post'
    assert records[1][1].endswith('DWQWHtcQpAHZJPTy19u1')
    assert records[1][2]['json']=={'text':'Habibi, hi!','model_id':'eleven_v4'}
    assert records[1][2]['headers']['xi-api-key']=='fake-test-api-key'
    assert records[-1][0]=='completed'


def test_source_audio_default_does_not_invoke_tts(monkeypatch):
    job={
      'job_id':'job-123', 'chat_id':2, 'lease_owner':'worker-0',
      'stage':'RESTORE_AUDIO', 'state':'RUNNING','checkpoint':{'merged_key':'merged.mp4','context':{'character':'NICOLE'}},
      'outputs':[], 'original_key':'original.mp4','voiceover_text':'',
    }
    monkeypatch.setattr(pipeline.voiceover, 'synthesize_once', lambda *a, **k: pytest.fail('Original audio changed'))
    monkeypatch.setattr(pipeline.storage, 'download_key', lambda key,p: Path(p).write_bytes(b'fake video'))
    monkeypatch.setattr(pipeline.ffmpeg, 'restore_source_audio', lambda merged,original,out: Path(out).write_bytes(b'fake final'))
    monkeypatch.setattr(pipeline.storage, 'upload_key', lambda *a,**k: None)
    calls=[]
    monkeypatch.setattr(pipeline.db, 'checkpoint', lambda *args,**kwargs: calls.append(kwargs) or {'saved':True})
    pipeline.process(job)
    assert calls[0]['stage']=='DELIVER'
    assert calls[0]['fields']['checkpoint']['final_key'].endswith('final_with_audio.mp4')


def test_ffmpeg_explicit_voice_overlay_keeps_video_and_audio(tmp_path):
    a=tmp_path/'source.mp4';b=tmp_path/'voice.mp3';out=tmp_path/'mix.mp4'
    ffmpeg.run('ffmpeg','-hide_banner','-loglevel','error','-y',
         '-f','lavfi','-i','color=c=blue:s=320x568:r=12:d=2',
         '-f','lavfi','-i','sine=frequency=220:duration=2',
         '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(a))
    ffmpeg.run('ffmpeg','-hide_banner','-loglevel','error','-y',
         '-f','lavfi','-i','sine=frequency=440:duration=1',str(b))
    ffmpeg.overlay_voiceover(a,b,out)
    m=ffmpeg.probe(out)
    assert m['has_audio'] and 1.9 < m['duration'] < 2.1
    assert m['width']==320 and m['height']==568


def test_concurrent_voice_submission_reservation_is_safe(monkeypatch):
    monkeypatch.setenv('ENABLE_PAID_VOICE_GENERATION','true')
    monkeypatch.setenv('ELEVENLABS_API_KEY','fake-api-key')
    monkeypatch.setattr(voiceover.db,'get_step',lambda *a:None)
    monkeypatch.setattr(voiceover.db,'reserve_one_shot',lambda *a:False)
    monkeypatch.setattr(voiceover.requests,'post',lambda *a,**k:pytest.fail('Duplicate request submitted'))
    with pytest.raises(voiceover.NeedsReview):
        voiceover.synthesize_once('job-123','ARIA','AUTO','Hello from Aria')
