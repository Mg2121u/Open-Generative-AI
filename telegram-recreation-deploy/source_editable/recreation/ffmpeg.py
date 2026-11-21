"""Local FFmpeg for fal.ai trim/concat/audio equivalence; no fal.ai access needed."""
import json
import subprocess
from pathlib import Path


def run(*args,timeout=600):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        detail=(p.stderr or '').strip()[-1200:] or 'no stderr; process may have been terminated by resource limits'
        raise RuntimeError(f'{args[0]} exit={p.returncode}: {detail}')
    return p.stdout


def probe(file):
    result=json.loads(run('ffprobe','-v','error','-show_format','-show_streams','-of','json',str(file),timeout=40))
    video=next((s for s in result['streams'] if s['codec_type']=='video'),None)
    if not video: raise ValueError('Missing video stream')
    return {'duration':float(result['format'].get('duration') or video.get('duration') or 0),
        'width':int(video['width']),'height':int(video['height']),
        'has_audio':any(s['codec_type']=='audio' for s in result['streams'])}


def trim(source,output,start,duration):
    if float(start)<0 or float(duration)<=0: raise ValueError('Invalid trim interval')
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-threads','1','-filter_threads','1','-ss',str(start),'-i',str(source),'-t',str(duration),
        '-map','0:v:0','-an','-vf',"scale=trunc(iw/2)*2:trunc(ih/2)*2",'-c:v','libx264','-threads','1','-preset','veryfast','-crf','17',
        '-pix_fmt','yuv420p','-movflags','+faststart',str(output))
    return probe(output)


def normalize(source,output,*,width=480,height=854,with_audio=True):
    src=probe(source)
    vf=f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24'
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source)]
    if with_audio and not src['has_audio']:
        cmd+=['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=44100']
    cmd+=['-map','0:v:0']
    if with_audio: cmd+=['-map','0:a:0' if src['has_audio'] else '1:a:0']
    cmd+=['-vf',vf,'-c:v','libx264','-preset','medium','-crf','16',
          '-pix_fmt','yuv420p','-movflags','+faststart']
    if with_audio: cmd+=['-c:a','aac','-ar','44100','-ac','2','-t',str(src['duration'])]
    else:cmd+=['-an']
    run(*cmd,str(output))
    return probe(output)


def concatenate(inputs,output):
    if not inputs: raise ValueError('No input clips')
    folder=Path(output).parent
    parts=[]
    for i,p in enumerate(inputs):
        part=folder/f'norm_{i:03}.mp4'
        normalize(p,part,with_audio=False)
        parts.append(part)
    playlist=folder/'join.txt'
    playlist.write_text(''.join(f"file '{p.resolve()}'\n" for p in parts))
    run('ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0',
        '-i',str(playlist),'-c','copy','-movflags','+faststart',str(output))
    return probe(output)



def trim_video_stream_copy(source, output, duration):
    """Remove small accumulated concat overrun without re-encoding a single frame.

    H.264 B-frame reordering can retain a few frames beyond the requested
    time; the caller validates the actual probed output rather than guessing.
    No new video or audio is synthesized here.
    """
    if float(duration)<=0:
        raise ValueError('Invalid final source duration')
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i',str(source),'-t',f'{float(duration):.6f}',
        '-map','0:v:0','-an','-c:v','copy',
        '-movflags','+faststart',str(output),timeout=180)
    return probe(output)



def fit_mp4_under_storage_limit(source, output, *, max_bytes=44_000_000):
    """Local-only final MP4 re-encode under an S3-compatible object-size cap.

    Preserve the full video, framing, original audio, and duration; NEVER
    re-request source generation. Under-sized videos are copied losslessly.
    One-pass ABR aims below the limit with overhead headroom; up to three
    progressively smaller encodes are allowed if content is difficult.
    """
    import math
    import shutil
    source=Path(source);output=Path(output)
    if not source.is_file() or not 12_000_000 <= max_bytes <= 2_000_000_000:
        raise ValueError('Invalid final-video source or storage cap')
    before=probe(source)
    if not before['has_audio'] or not 2 <= before['duration'] <= 7200:
        raise ValueError('Final file must contain the source soundtrack')
    if source.stat().st_size <= max_bytes:
        shutil.copyfile(source,output)
        return {'before_bytes':source.stat().st_size,
                'after_bytes':output.stat().st_size,'encoded':False}
    duration=before['duration']
    audio_bps=112_000
    # Fit safely under storage limits including MP4 headers and rate-control
    # overshoot. Two smaller attempts remain available without another Wan call.
    budget_bps=max(230_000,int((max_bytes*8/duration-audio_bps)*.85))
    for factor in (1.0,.75,.55):
        video_bps=max(160_000,int(budget_bps*factor))
        maxrate=int(video_bps*1.3)
        run('ffmpeg','-hide_banner','-loglevel','error','-y',
            '-i',str(source),
            '-map','0:v:0','-map','0:a:0',
            '-c:v','libx264','-preset','medium',
            '-b:v',str(video_bps),'-maxrate',str(maxrate),
            '-bufsize',str(maxrate*2),
            '-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','112k','-ar','44100',
            '-movflags','+faststart',str(output),timeout=1200)
        size=output.stat().st_size
        actual=probe(output)
        if (not actual['has_audio'] or
                actual['width']!=before['width'] or
                actual['height']!=before['height'] or
                abs(actual['duration']-duration)>.20):
            raise RuntimeError('Storage-sized final video lost duration, frames, or original soundtrack')
        if size<=max_bytes:
            return {'before_bytes':source.stat().st_size,
                    'after_bytes':size,'encoded':True}
    raise RuntimeError('Could not fit completed video under the configured storage size cap')

def extract_last_frame(video, output):
    """Decode the actual final frame from a generated MP4, not a source frame.

    The image2 muxer's update=1 overwrites the same JPEG for every decoded
    frame; the file left on disk is therefore the last decoded video frame.
    """
    if not Path(video).is_file():
        raise ValueError('Previous generated segment is missing')
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i',str(video),'-map','0:v:0','-an',
        '-q:v','2','-update','1','-f','image2',str(output),timeout=180)
    if not Path(output).is_file() or Path(output).stat().st_size < 512:
        raise RuntimeError('Final-frame extraction failed')
    return output


def extract_first_frame(video, output):
    """Read the frame actually displayed at the start of a generated segment."""
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i',str(video),'-map','0:v:0','-an',
        '-frames:v','1','-q:v','2',str(output),timeout=60)
    if not Path(output).is_file() or Path(output).stat().st_size < 512:
        raise RuntimeError('First-frame extraction failed')
    return output


def seam_blend_start(previous_last_jpg, next_clip, output, *, seconds=0.20):
    """Start the next clip with the prior exact frame, then fade to its video.

    The blend occupies the first 0.2s INSIDE the next segment, so it doesn't
    change its duration or shift the original source soundtrack. This local
    postprocess is not a paid provider call and adds no additional runtime.
    """
    if not 0.08 <= seconds <= 0.35:
        raise ValueError('Seam blend must be 80-350ms')
    if not Path(previous_last_jpg).is_file():
        raise ValueError('Missing previous ending frame')
    duration = probe(next_clip)['duration']
    expression = f'A*max(0\\,1-T/{seconds:.3f})+B*(1-max(0\\,1-T/{seconds:.3f}))'
    filtergraph = (
        '[0:v]fps=24,scale=480:854,setsar=1,format=yuv420p[prev];'
        '[1:v]fps=24,scale=480:854:force_original_aspect_ratio=decrease,'
        'pad=480:854:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[cur];'
        f'[prev][cur]blend=all_expr={expression}:shortest=1,format=yuv420p[video]'
    )
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-loop','1','-framerate','24','-i',str(previous_last_jpg),
        '-i',str(next_clip),'-filter_complex',filtergraph,
        '-map','[video]','-an','-c:v','libx264','-preset','veryfast','-crf','19',
        '-pix_fmt','yuv420p','-movflags','+faststart',str(output),timeout=180)
    result=probe(output)
    if abs(result['duration']-duration)>0.15:
        raise RuntimeError('Continuity smoothing changed the source timing')
    return result


def frame_difference(image_a, image_b):
    """Mean RGB mismatch in [0,1] (0 is a pixel-identical boundary)."""
    from PIL import Image, ImageChops, ImageStat
    with Image.open(image_a) as ai, Image.open(image_b) as bi:
        a=ai.convert('RGB').resize((96,168), Image.Resampling.BILINEAR)
        b=bi.convert('RGB').resize((96,168), Image.Resampling.BILINEAR)
        return sum(ImageStat.Stat(ImageChops.difference(a,b)).mean) / (3*255)


def restore_source_audio(video,original,output):
    """Copy the video track; use source audio from t=0; pad or cut at final duration."""
    info=probe(video)
    if not probe(original)['has_audio']:
        raise ValueError('The original has no audio; choose silent delivery instead')
    run('ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(video),'-i',str(original),
        '-filter_complex','[1:a:0]apad[a]','-map','0:v:0','-map','[a]',
        '-c:v','copy','-c:a','aac','-b:a','192k','-t',str(info['duration']),
        '-movflags','+faststart',str(output))
    return probe(output)


def overlay_voiceover(video_with_original_audio, speech_mp3, output):
    """Explicit opt-in mix: preserve original as one input, add character voice.

    Mix is only used when /voiceover was set. Video stream is copied,
    original soundtrack is retained. Voice clips that exceed video are cut.
    """
    info=probe(video_with_original_audio)
    if not info['has_audio']:
        raise ValueError('Original soundtrack must be restored before optional voiceover')
    run('ffmpeg','-hide_banner','-loglevel','error','-y',
        '-i',str(video_with_original_audio),'-i',str(speech_mp3),
        '-filter_complex',
        '[0:a:0]volume=1[original];[1:a:0]apad[voice];'
        '[original][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=1[mix]',
        '-map','0:v:0','-map','[mix]',
        '-c:v','copy','-c:a','aac','-b:a','192k',
        '-t',str(info['duration']),'-movflags','+faststart',str(output))
    return probe(output)


def motion_interpolate_start(previous_clip, next_clip, output, *, seconds=0.25):
    """Motion-compensated transition without a frozen-frame hold or timing shift.

    The first decoded frame equals the preceding generated final frame; the
    frame at the end of the transition equals the *actual* next clip at that
    timestamp. Intermediate frames use bidirectional dense optical flow rather
    than alpha-fading a static picture over moving video. The number of output
    frames equals the next clip's 24-fps frame count; original audio is restored
    later in the assembly. This costs no AI provider credits.
    """
    if not 0.12 <= seconds <= 0.40:
        raise ValueError('Motion bridge must be 120-400ms')
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError('Motion bridge requires opencv-python-headless') from exc
    import tempfile

    fps=24
    width,height=480,854
    n=max(3,round(seconds*fps))
    with tempfile.TemporaryDirectory() as td:
        prev=Path(td)/'previous.mp4'
        next_normalized=Path(td)/'next.mp4'
        normalize(previous_clip,prev,width=width,height=height,with_audio=False)
        normalize(next_clip,next_normalized,width=width,height=height,with_audio=False)
        cp=cv2.VideoCapture(str(prev))
        tail=[]
        try:
            while True:
                ok,frame=cp.read()
                if not ok:break
                tail=(tail+[frame])[-2:]
        finally:cp.release()
        if len(tail)<2:
            raise ValueError('Previous segment has fewer than two decoded frames')
        cn=cv2.VideoCapture(str(next_normalized))
        try:
            first=[]
            for _ in range(n+2):
                ok,frame=cn.read()
                if not ok:break
                first.append(frame)
            if len(first)<n+1:
                raise ValueError('Next clip is too short for motion-aware seam')
            a=tail[-1]
            b=first[n]
            # Compute bidirectional optical flow at half-resolution for
            # bounded memory/time even with 480p portrait videos.
            def flow_between(x,y):
                xg=cv2.resize(cv2.cvtColor(x,cv2.COLOR_BGR2GRAY),(240,427))
                yg=cv2.resize(cv2.cvtColor(y,cv2.COLOR_BGR2GRAY),(240,427))
                flow=cv2.calcOpticalFlowFarneback(xg,yg,None,0.5,3,21,3,5,1.2,0)
                return cv2.resize(flow,(width,height),interpolation=cv2.INTER_LINEAR)*2
            forward=flow_between(a,b)
            backward=flow_between(b,a)
            grid_x,grid_y=np.meshgrid(np.arange(width,dtype=np.float32),np.arange(height,dtype=np.float32))
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y',
                 '-f','rawvideo','-pix_fmt','bgr24','-s',f'{width}x{height}',
                 '-r',str(fps),'-i','pipe:0','-an','-c:v','libx264',
                 '-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
                 '-movflags','+faststart',str(output)]
            p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            frames_written=0
            try:
                for i,frame in enumerate(first):
                    if i==0: out=a
                    elif i>=n:out=frame
                    else:
                        t=i/n
                        af=cv2.remap(a,grid_x-t*forward[:,:,0],grid_y-t*forward[:,:,1],
                                     cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
                        bf=cv2.remap(b,grid_x-(1-t)*backward[:,:,0],grid_y-(1-t)*backward[:,:,1],
                                     cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
                        # Flow evolves linearly in time; blend opacity eases in.
                        mix=t*t*(3-2*t)
                        out=cv2.addWeighted(af,1-mix,bf,mix,0)
                    p.stdin.write(out.tobytes())
                    frames_written+=1
                while True:
                    ok,frame=cn.read()
                    if not ok:break
                    p.stdin.write(frame.tobytes())
                    frames_written+=1
                p.stdin.close()
                err=p.stderr.read()
                code=p.wait(timeout=180)
                if code:
                    raise RuntimeError('Motion interpolation encode failed: '+err.decode(errors='replace')[-700:])
            except Exception:
                p.kill();p.wait()
                raise
        finally:cn.release()
    info=probe(output)
    expected=probe(next_clip)['duration']
    if abs(info['duration']-expected)>0.09:
        raise RuntimeError('Motion-aware seam altered video duration')
    return {**info,'bridge_frames':n,'frames_written':frames_written,
            'method':'bidirectional_optical_flow','source_seconds':round(seconds,3)}


def seam_motion_metrics(previous_clip, new_clip):
    """Read motion, not only endpoint pixel similarity, around the boundary."""
    import cv2
    import numpy as np
    p=cv2.VideoCapture(str(previous_clip));a=[]
    try:
        while True:
            ok,f=p.read()
            if not ok:break
            a=(a+[cv2.resize(cv2.cvtColor(f,cv2.COLOR_BGR2GRAY),(120,214))])[-3:]
    finally:p.release()
    p=cv2.VideoCapture(str(new_clip));b=[]
    try:
        for _ in range(4):
            ok,f=p.read()
            if not ok:break
            b.append(cv2.resize(cv2.cvtColor(f,cv2.COLOR_BGR2GRAY),(120,214)))
    finally:p.release()
    if len(a)<3 or len(b)<3:raise ValueError('Need two motion frames around seam')
    # Mean pixel changes are not a perfect motion metric, but are sensitive to
    # a frozen first few frames and to a sudden motion spike at the cut.
    frames=a+b
    deltas=[float(np.mean(cv2.absdiff(x,y)))/255.0 for x,y in zip(frames,frames[1:])]
    cut=deltas[2]
    adjacent=[deltas[1],deltas[3],deltas[4]]
    normal=max(0.002,float(np.median(adjacent)))
    return {'cut_delta':round(cut,4), 'adjacent_median':round(normal,4),
            'motion_jump_ratio':round(cut/normal,3),
            'first_frame_motion':round(deltas[3],4)}
