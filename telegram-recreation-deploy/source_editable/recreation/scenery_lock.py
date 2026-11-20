"""STRICT PHOTO BACKGROUND: deterministic local human matting, no model provider.

The former 3-image Wan method tried to convince a generative model to separate
Nicole's identity, choreography and a replacement setting; it visibly
crossfaded back to the indoor reference at each segment reset.

This optional, owner-preview-only module replaces EVERY non-person pixel
with the uploaded scenery photo in every frame, preserving Nicole's
unrepainted pixel colors, movement, timing and source audio. It never calls
WaveSpeed, ElevenLabs, or any paid endpoint. The original video is immutable.

MIT licensed portrait-matting model/weights: Longpipe (Longpipe/longpipe),
onnx/large/matting_portrait.onnx (built into the isolated image at deploy).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request,urlopen

import cv2
import numpy as np
from PIL import Image,ImageOps

from . import ffmpeg,provider,storage

LOG=logging.getLogger("scenery-lock")
ROOT=Path(os.getenv('RECREATION_SCENE_MODEL_DIR','/app/scene_models'))
WEIGHTS=ROOT/'longpipe_large_matting_portrait_v005.onnx'
MODEL_NAME='Longpipe MIT-licensed large/portrait v0.0.5'
MODEL_URLS=(
    'https://cdn.longpipe.dev/models/v/0.0.5/onnx/large/matting_portrait.onnx',
    'https://huggingface.co/Longpipe/longpipe/resolve/main/onnx/large/matting_portrait.onnx?download=true'
)
OUTPUT_VERSION='strict_local_scene_v1'


def _session():
    if not WEIGHTS.is_file() or not 5_000_000 <= WEIGHTS.stat().st_size <= 45_000_000:
        raise provider.NeedsReview('Local scenery matte model is not installed; no paid calls')
    import onnxruntime as ort
    opts=ort.SessionOptions()
    opts.intra_op_num_threads=2
    opts.inter_op_num_threads=1
    session=ort.InferenceSession(
        str(WEIGHTS),sess_options=opts,providers=['CPUExecutionProvider'])
    ins=session.get_inputs()
    if len(ins)!=1 or ins[0].name!='rgb':
        raise RuntimeError('Unexpected scenery matte network inputs')
    dims=ins[0].shape
    if len(dims)!=4 or dims[:2]!=[1,3]:
        raise RuntimeError('Unexpected scenery matte input layout')
    h,w=int(dims[2]),int(dims[3])
    if not 180<=w<=1300 or not 180<=h<=1300:
        raise RuntimeError('Unexpected matting resolution')
    return session,(w,h)


def install_model():
    """Build-time only. Fail deploy if official MIT model cannot be validated."""
    ROOT.mkdir(parents=True,exist_ok=True)
    if not WEIGHTS.is_file() or not 5_000_000<=WEIGHTS.stat().st_size<=45_000_000:
        last=None
        for url in MODEL_URLS:
            tmp=WEIGHTS.with_suffix('.download')
            try:
                req=Request(url,headers={'User-Agent':'RecreationSceneMatteBuild/1.0'})
                total=0
                with urlopen(req,timeout=95) as res,tmp.open('wb') as dst:
                    while True:
                        data=res.read(1024*1024)
                        if not data:break
                        total+=len(data)
                        if total>45_000_000:
                            raise ValueError('Matting weights exceeded size limit')
                        dst.write(data)
                if total<5_000_000:
                    raise ValueError('Incomplete matting model download')
                tmp.replace(WEIGHTS)
                break
            except (OSError,URLError,ValueError) as exc:
                last=exc
                tmp.unlink(missing_ok=True)
        if not WEIGHTS.is_file():
            raise RuntimeError('Official Longpipe matting model installation failed: '
                               +type(last).__name__)
    session,(w,h)=_session()
    # Verify actual inference, not just that the file exists.
    result=session.run(None,{'rgb':np.zeros((1,3,h,w),dtype=np.float32)})
    if not result or result[0].shape!=(1,1,h,w):
        raise RuntimeError('Matting model produced an incompatible alpha tensor')
    LOG.info('SCENERY_MODEL_READY local_only=true license=MIT shape=%sx%s',w,h)


def _background(path,width,height):
    with Image.open(path) as photo:
        photo=ImageOps.exif_transpose(photo).convert('RGB')
        if min(photo.size)<128:
            raise provider.NeedsReview('Scenery image is too small for consistent background')
        # Match output aspect ratio without stretching the scene's architecture.
        fitted=ImageOps.fit(photo,(width,height),method=Image.Resampling.LANCZOS,
                            centering=(.5,.5))
        return cv2.cvtColor(np.array(fitted),cv2.COLOR_RGB2BGR)


def _matte(frame,session,size):
    width,height=size
    rgb=cv2.cvtColor(cv2.resize(frame,(width,height),interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2RGB)
    tensor=rgb.astype(np.float32).transpose(2,0,1)[None]/255.
    alpha=session.run(None,{'rgb':tensor})[0]
    if alpha.shape!=(1,1,height,width) or not np.isfinite(alpha).all():
        raise provider.NeedsReview('Scenery matting produced invalid alpha; original archive preserved')
    alpha=cv2.resize(np.clip(alpha[0,0],0,1),
                     (frame.shape[1],frame.shape[0]),interpolation=cv2.INTER_LINEAR)
    # Never introduce washed-out room pixels as ghostly transparency.
    alpha=np.clip((alpha-.035)/.92,0,1)
    alpha=cv2.GaussianBlur(alpha,(3,3),.55)
    return alpha.astype(np.float32)


def render(input_video,scene_image,output_video,*,session=None):
    """Video + exact scenery photo -> video with original audio, no paid inference.

    Atomic to caller: failed quality checks leave output_video absent. No frame
    is created by an image-to-video model. The model only estimates human alpha.
    """
    src=Path(input_video);bg_file=Path(scene_image);dest=Path(output_video)
    if not src.is_file() or not bg_file.is_file():
        raise ValueError('Input video or scenery photo is missing')
    info=ffmpeg.probe(src)
    if info['width']<240 or info['height']<240 or info['duration']<=0:
        raise provider.NeedsReview('Archived video unsuitable for scenery repair')
    if info['duration']>300:
        raise provider.NeedsReview('Scenery preview limited to five-minute videos')
    cap=cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise provider.NeedsReview('Cannot decode archived video for scenery preview')
    w,h=info['width'],info['height']
    fps=cap.get(cv2.CAP_PROP_FPS)
    if not 10<=fps<=90:
        cap.release()
        raise provider.NeedsReview('Unexpected video frame rate')
    scene=_background(bg_file,w,h).astype(np.float32)
    if session is None:
        session,size=_session()
    else:
        dims=session.get_inputs()[0].shape
        size=(int(dims[3]),int(dims[2]))
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix('.partial.mp4')
    cmd=[
        'ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','rawvideo','-pix_fmt','bgr24','-s',f'{w}x{h}',
        '-r',f'{fps:.5f}','-i','pipe:0',
        '-i',str(src),
        '-map','0:v:0','-map','1:a:0?',
        '-c:v','libx264','-pix_fmt','yuv420p','-crf','19',
        '-preset','veryfast','-c:a','copy','-shortest',
        '-movflags','+faststart',str(tmp)
    ]
    ff=None
    processed=0;bad=0;areas=[]
    try:
        ff=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE,
                            stdout=subprocess.DEVNULL)
        while True:
            ok,frame=cap.read()
            if not ok:break
            a=_matte(frame,session,size)
            coverage=float((a>.50).mean())
            areas.append(coverage)
            if not .008<=coverage<=.91:
                bad+=1
            # Exact pixel replacement, not a generative hallucination.
            composite=(frame.astype(np.float32)*a[:,:,None]
                       +scene*(1.-a[:,:,None]))
            pixels=np.clip(composite,0,255).astype(np.uint8)
            ff.stdin.write(pixels.tobytes())
            processed+=1
            if processed>60*90*5:
                raise provider.NeedsReview('Scenery processing exceeded frame cap')
        ff.stdin.close()
        error=ff.stderr.read().decode('utf-8',errors='replace')[:900]
        code=ff.wait(timeout=60)
        if code:
            raise RuntimeError('Local scenery video encode failed: '+error[-250:])
        expected_frames=round(info['duration']*fps)
        if processed<3 or abs(processed-expected_frames)>max(6,.05*expected_frames):
            raise provider.NeedsReview('Scenery video lost source frames; archived original untouched')
        if bad>max(3,int(processed*.15)):
            raise provider.NeedsReview(
                f'Foreground mask unstable ({bad}/{processed} frames); '
                'local preview rejected without any paid AI call')
        out_info=ffmpeg.probe(tmp)
        if abs(out_info['duration']-info['duration'])>.23:
            raise provider.NeedsReview('Scenery preview lost source timing; original kept')
        if info['has_audio'] and not out_info['has_audio']:
            raise provider.NeedsReview('Scenery preview lost original soundtrack; original kept')
        tmp.replace(dest)
        report={'frames':processed,'duration':out_info['duration'],
                'foreground_fraction_median':round(float(np.median(areas)),4),
                'uncertain_mask_frames':bad,'matte_model':MODEL_NAME}
        LOG.info('STRICT_SCENE complete=true frames=%d bad_mask=%d audio_preserved=%s '
                 'paid_provider_calls=0',processed,bad,out_info['has_audio'])
        return report
    finally:
        cap.release()
        if ff is not None and ff.poll() is None:
            ff.kill()
            ff.wait(timeout=5)
        tmp.unlink(missing_ok=True)
