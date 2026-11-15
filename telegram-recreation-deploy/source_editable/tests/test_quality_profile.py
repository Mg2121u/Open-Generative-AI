"""Identity-lock regression tests; no network/provider calls or paid generation."""
import shutil
import subprocess

import pytest
from recreation import full_video, quality, ffmpeg


def job(**kwargs):
    base = {
        'job_id': 'quality-test-uuid', 'character': 'NICOLE', 'owner_id': 123,
        'chat_id': 123, 'state': 'RUNNING', 'stage': 'AUTO_GENERATE',
        'original_key': 'source.mp4',
        'scenery': 'Beirut skyline during sunset',
        'outfit': 'the same as her identity image',
        'checkpoint': {
            'identity': {'status': 'ready', 'nicole_image_key': 'identity/nicole.jpg'},
            'local_preflight': {
                'source_key': 'source.mp4', 'complete': True,
                'source_probe': {'duration': 18.2, 'width': 480, 'height': 854,
                                 'has_audio': True},
            },
        },
    }
    base.update(kwargs)
    return base


def test_prompts_use_owner_scene_outfit_and_image_authorities():
    p = quality.build_prompt(job(), chained=True)
    assert 'Beirut skyline during sunset' in p
    assert 'Replace the source location completely' in p
    assert 'Image 1' in p and 'IMAGE 2' in p and 'VIDEO 1' in p
    assert 'the same as her identity image' not in p
    assert 'wardrobe authority' in p
    assert 'do not' in p.lower()
    assert 'No flickering facial features' in p
    assert 'social-media handles' in p and 'NO visible writing' in p
    assert 'Do not borrow the Video 1' in p


def test_source_scene_and_source_outfit_are_opt_in():
    p = quality.build_prompt(job(scenery='keep source scenery',
                                 outfit='keep source clothing'), chained=False)
    assert 'Keep the original background' in p
    assert 'Copy the complete wardrobe from Video 1' in p
    assert 'Image 2 is' not in p


def test_qa_plan_uses_short_equal_motion_windows():
    segs = quality.quality_plan(18.2)
    assert len(segs) == 5
    assert all(3 <= s['duration'] < 4 for s in segs)
    assert all(s['duration'] == 3.64 for s in segs)
    assert segs[-1]['start'] == pytest.approx(14.56)
    assert sum(s['duration'] for s in segs) == pytest.approx(18.2)
    assert all(s['reused'] is False for s in segs)
    assert len(quality.quality_plan(9.366667)) == 3


def test_same_job_deterministic_seed_uses_valid_wan_range():
    j = job()
    assert quality.fixed_seed(j) == quality.fixed_seed(j)
    assert 0 <= quality.fixed_seed(j) < 2147483648
    assert quality.fixed_seed(j) != quality.fixed_seed(job(job_id='other'))


def test_quality_profile_requires_auto_stage(monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE', 'identity_lock')
    assert quality.enabled(job(stage='AUTO_GENERATE'))
    assert quality.enabled(job(stage='AUTO_PREFLIGHT'))
    assert not quality.enabled(job(stage='DRAFT'))
    assert not quality.enabled(job(stage='DONE'))


def test_report_uses_quality_windows_and_never_reuses_old_paid_canary(monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE', 'identity_lock')
    monkeypatch.setattr(full_video, '_reusable_canary', lambda _: pytest.fail('old canary reused'))
    result = full_video.report(job())
    assert len(result['segments']) == 5
    assert result['reused_first_canary'] is False
    assert result['source_duration'] == 18.2


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg unavailable')
def test_no_warp_merge_preserves_audio_and_never_uses_optical_flow(tmp_path, monkeypatch):
    monkeypatch.setenv('RECREATION_QUALITY_MODE', 'identity_lock')
    monkeypatch.setenv('RECREATION_SEAM_MODE', 'cut_no_warp')
    source = tmp_path / 'original.mp4'
    subprocess.run([
        'ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','color=c=blue:size=480x854:rate=24:duration=4.0',
        '-f','lavfi','-i','sine=frequency=550:sample_rate=44100:duration=4.0',
        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast',
        '-c:a','aac',str(source)
    ], check=True)
    parts = []
    objects = {'source.mp4': source}
    for i in range(2):
        clip = tmp_path / f'clip_{i}.mp4'
        ffmpeg.trim(source, clip, i * 2, 2)
        key = f'clip_{i}'
        objects[key] = clip
        parts.append(key)
    def download(key, path):
        shutil.copyfile(objects[key], path)
    monkeypatch.setattr(full_video.storage, 'download_key', download)
    monkeypatch.setattr(full_video, '_stored', lambda key: False)
    monkeypatch.setattr(ffmpeg, 'motion_interpolate_start',
                        lambda *a, **k: pytest.fail('anatomy optical flow must not run'))
    result = {}
    def upload(key, path):
        final = tmp_path / 'final.mp4'
        shutil.copyfile(path, final)
        result['file'] = final
    monkeypatch.setattr(full_video.storage, 'upload_key', upload)
    j = {'job_id': 'local-qa-job', 'original_key': 'source.mp4'}
    segments = [{'index': 0, 'duration': 2.0},
                {'index': 1, 'duration': 2.0}]
    full_video._merge_original_sound(j, segments, parts, motion_aware=True)
    media = ffmpeg.probe(result['file'])
    assert media['has_audio']
    assert media['duration'] == pytest.approx(4, abs=.15)
