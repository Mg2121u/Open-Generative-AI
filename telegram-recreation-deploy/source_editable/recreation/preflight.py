"""Resumable, non-billable FFmpeg preflight for archived source videos.

This is a diagnostic only: it never substitutes for Grok's artistic segment plan,
changes the paid recreation stages, or calls any external generation provider.
Each source segment is durably checkpointed before attempting the next one.
"""
import math
import tempfile
from pathlib import Path
from . import db, ffmpeg, storage, telegram

STAGE='LOCAL_PREFLIGHT'
MAX_CLIPS=3
CLIP_SECONDS=4.0


def plan(duration):
    if not math.isfinite(duration) or duration < 0.25 or duration > 7200:
        raise ValueError('Unsupported source duration for FFmpeg preflight')
    result=[]
    time=0.0
    while time < min(duration, MAX_CLIPS*CLIP_SECONDS)-0.08 and len(result)<MAX_CLIPS:
        length=min(CLIP_SECONDS,duration-time)
        result.append({'start':round(time,3),'duration':round(length,3)})
        time+=length
    if not result:
        raise ValueError('No usable source segments')
    return result


def _save(job,details,event,completed=False):
    payload=dict(job.get('checkpoint') or {})
    payload['local_preflight']=details
    return db.checkpoint(job['job_id'],job['lease_owner'],
          stage='UPLOAD_REFERENCE' if completed else STAGE,
          state='DRAFT' if completed else 'QUEUED',
          fields={'checkpoint':payload,'attempt':0,'last_error':None},
          event=event)


def run_step(job):
    job_id=str(job['job_id'])
    original=job.get('original_key')
    if not original:
        raise ValueError('Source must be archived before local preflight')
    cp=dict(job.get('checkpoint') or {})
    saved=cp.get('local_preflight')
    if saved and saved.get('source_key')!=original:
        saved=None
    if not saved:
        with tempfile.TemporaryDirectory() as folder:
            original_path=Path(folder)/'original.mp4'
            storage.download_key(original,original_path)
            info=ffmpeg.probe(original_path)
        saved={'source_key':original,'source_probe':info,
               'segments':plan(info['duration']),'segment_keys':[],
               'merged_key':None,'complete':False}
        return _save(job,saved,('preflight_analyzed','Source video inspected locally; no AI calls'))

    keys=list(saved.get('segment_keys',[]))
    segments=list(saved['segments'])
    if len(keys) < len(segments):
        i=len(keys);segment=segments[i]
        key=f'recreation/{job_id}/preflight/segment_{i:02}.mp4'
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder);orig=f/'original.mp4';clip=f/'segment.mp4'
            storage.download_key(original,orig)
            ffmpeg.trim(orig,clip,segment['start'],segment['duration'])
            storage.upload_key(key,clip)
        saved={**saved,'segment_keys':keys+[key]}
        return _save(job,saved,('preflight_segment_saved',f'FFmpeg source segment {i+1} archived'))

    if len(keys)!=len(segments):
        raise ValueError('Preflight checkpoint has unexpected segment count')
    merged_key=f'recreation/{job_id}/preflight/merged_preview.mp4'
    with tempfile.TemporaryDirectory() as folder:
        f=Path(folder);inputs=[]
        for i,key in enumerate(keys):
            path=f/f'segment_{i:02}.mp4'
            storage.download_key(key,path);inputs.append(path)
        out=f/'preview.mp4'
        if len(inputs)==1:
            out.write_bytes(inputs[0].read_bytes())
        else:
            ffmpeg.concatenate(inputs,out)
        merged_info=ffmpeg.probe(out)
        storage.upload_key(merged_key,out)
    saved={**saved,'merged_key':merged_key,'merged_probe':merged_info,'complete':True}
    result=_save(job,saved,('preflight_complete','FFmpeg trim and concat preview archived without paid AI calls'),completed=True)
    if result:
        try:
            telegram.say(job['chat_id'],f'Source preflight complete: {len(keys)} clips trimmed and merged in private storage. Use /status. Paid video generation remains disabled.')
        except Exception:
            # Notification failure cannot invalidate an already durable checkpoint.
            pass
    return result
