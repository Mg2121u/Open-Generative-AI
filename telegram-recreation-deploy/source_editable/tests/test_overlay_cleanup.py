"""Offline watermark cleanup regression tests; no OCR, providers or paid calls."""
import hashlib
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from recreation import overlay_cleanup, quality


def _synthetic_video(path, *, with_overlay):
    fps=24
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),
                           fps,(480,854))
    assert writer.isOpened()
    for i in range(48):
        frame=np.full((854,480,3),(170,175,177),dtype=np.uint8)
        # Bright clothing in the middle must NOT trigger a top-right logo.
        cv2.rectangle(frame,(165,270),(270,640),(250,75,40),-1)
        if with_overlay:
            # Compact saturated icon, matching the detection path without
            # depending on reading a real username or licensed artwork.
            cv2.rectangle(frame,(414,220),(439,246),(255,0,245),2)
            cv2.putText(frame,'@EXAMPLE', (341,281),
                        cv2.FONT_HERSHEY_SIMPLEX,.40,(244,244,244),1)
        writer.write(frame)
    writer.release()


def test_colored_overlay_detected_and_removed_without_editing_original(tmp_path):
    src=tmp_path/'logo.mp4'
    dst=tmp_path/'clean.mp4'
    _synthetic_video(src,with_overlay=True)
    before=hashlib.sha256(src.read_bytes()).hexdigest()
    assert overlay_cleanup.clean_motion_reference(src,dst)
    assert src.is_file() and hashlib.sha256(src.read_bytes()).hexdigest()==before
    assert dst.is_file()
    before_vid=cv2.VideoCapture(str(src))
    after_vid=cv2.VideoCapture(str(dst))
    try:
        assert int(after_vid.get(cv2.CAP_PROP_FRAME_COUNT))==48
        assert int(after_vid.get(cv2.CAP_PROP_FRAME_WIDTH))==480
        assert int(after_vid.get(cv2.CAP_PROP_FRAME_HEIGHT))==854
        ok1,original=before_vid.read()
        ok2,cleaned=after_vid.read()
        assert ok1 and ok2
        # The bright social icon disappears; central body/clothing stays intact.
        zone=(slice(218,249),slice(410,445))
        h1=cv2.cvtColor(original[zone],cv2.COLOR_BGR2HSV)
        h2=cv2.cvtColor(cleaned[zone],cv2.COLOR_BGR2HSV)
        assert np.mean(h2[:,:,1])<np.mean(h1[:,:,1])*.45
        assert np.mean(np.abs(cleaned[300:450,175:260].astype(float)-
                                 original[300:450,175:260].astype(float)))<18
    finally:
        before_vid.release()
        after_vid.release()


def test_no_overlay_no_reencoding(tmp_path):
    src=tmp_path/'clean_source.mp4'
    dst=tmp_path/'unused.mp4'
    _synthetic_video(src,with_overlay=False)
    original=src.read_bytes()
    assert overlay_cleanup.clean_motion_reference(src,dst) is False
    assert not dst.exists()
    assert src.read_bytes()==original


def test_no_forced_logo_cleanup_on_unrecognized_source():
    frame=np.full((854,480,3),128,dtype=np.uint8)
    assert overlay_cleanup._upper_color_logo_center(frame) is None
    assert overlay_cleanup._lower_companion_present(frame) is False


def test_longer_shots_reduce_recompression_and_preserve_duration():
    segments=quality.quality_plan(18.2)
    assert len(segments)==3
    assert [s['output_seconds'] for s in segments]==[7]*3
    assert abs(sum(s['duration'] for s in segments)-18.2)<.01


def test_prompt_explicitly_bans_overlay_reproduction():
    job={'job_id':'test','stage':'AUTO_GENERATE','scenery':'Beirut skyline',
         'outfit':'same as reference photo','instructions':'Dance as in video'}
    p=quality.build_prompt(job,chained=True).lower()
    for item in ('logos','watermarks','subtitles','usernames','instagram',
                 'do not reproduce'):
        assert item in p
