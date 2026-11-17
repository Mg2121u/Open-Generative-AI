"""Staging-only, restart-safe full-video Nicole recreation with chained frames.

No billable calls unless an exact cost approval is configured on the *dedicated*
worker. Reuse the already approved and successfully archived first 3-second
Nicole identity segment, generating only subsequent source windows. Each segment
has an immutable provider step key and cannot be paid twice automatically.
Each new request receives Nicole image + the previous generated last frame +
the next source-motion clip, with local 0.2s seam blending. The final sound is
copied from the original archived video via FFmpeg.
"""
import logging
import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, unquote

from botocore.exceptions import ClientError

from . import db, ffmpeg, provider, storage, wan_canary, telegram, quality, overlay_cleanup

LOGGER = logging.getLogger('full-video')
RUN_KEY = 'nicole_full_480p_continuity_v2_20261115'
FIRST_CANARY_STEP = wan_canary.STEP_KEY
MAX_SOURCE_SECONDS = 7200.0
MAX_CLIPS = 2400
CLIP_SECONDS = 3.0
RATE_USD = 0.05
# An approval for the first single-clip canary is NOT an approval for the full video.
MAX_APPROVAL_USD = 10.0


def plan(duration, *, reuse_first=False):
    """Split the *entire* duration, with each reference 2-3.99s.

    Short remainders are redistributed to avoid <2-second output requests,
    which Wan 3.0 does not accept. Offsets are source-clock aligned.
    """
    try: duration = round(float(duration), 3)
    except (ValueError, TypeError): raise ValueError('Invalid video duration') from None
    if not math.isfinite(duration) or not 2 <= duration <= MAX_SOURCE_SECONDS:
        raise ValueError('Source duration must be between 2 seconds and the media processing limit')
    n = int(duration // CLIP_SECONDS)
    rem = round(duration - n * CLIP_SECONDS, 3)
    lengths = [CLIP_SECONDS] * n
    if rem >= 2:
        lengths.append(rem)
    elif rem > 0:
        if not lengths:
            lengths = [duration]
        elif lengths[-1] - (2 - rem) >= 2:
            lengths[-1] = round(lengths[-1] - (2 - rem), 3)
            lengths.append(2.0)
        else:
            lengths[-1] = round(lengths[-1] + rem, 3)
    if not lengths or len(lengths) > MAX_CLIPS:
        raise ValueError('Invalid full-video segment count')
    output = []
    start = 0.0
    for idx, length in enumerate(lengths):
        billed_output = max(2, math.ceil(length - 1e-7))
        if length < 2 or billed_output > 15 or billed_output + math.ceil(length) > 30:
            raise ValueError('Segment cannot be billed safely')
        output.append({'index':idx, 'start':round(start,3), 'duration':round(length,3),
                       'output_seconds':billed_output, 'reused':bool(reuse_first and idx == 0 and length == 3.0),
                       'estimated_usd':round((math.ceil(length) + billed_output) * RATE_USD,2)})
        start = round(start + length,3)
    if abs(start-duration) > 0.003:
        raise ValueError('Full duration not covered')
    return output


def estimate(segments):
    """WaveSpeed model estimate, excludes already archived successful canary."""
    return round(sum(s['estimated_usd'] for s in segments if not s['reused']), 2)


def _reusable_canary(job):
    """Only reuse a previously paid Nicole clip when it matches THIS source/photo.

    A replaced source video or identity photo must invalidate reuse. This function
    performs only DB / private storage reads, never a paid API request.
    """
    job_id=str(job['job_id'])
    step=db.get_step(job_id,FIRST_CANARY_STEP)
    if not step or step.get('state')!='COMPLETED': return False
    media=(step.get('input') or {}).get('payload') or {}
    images=media.get('reference_images') or []
    videos=media.get('reference_videos') or []
    current_image=((job.get('checkpoint') or {}).get('identity') or {}).get('nicole_image_key')
    if not (media.get('duration')==3 and images and videos and current_image): return False
    if current_image not in unquote(urlsplit(images[0]).path): return False
    if f'/recreation/{job_id}/canary/short_reference.mp4' not in unquote(urlsplit(videos[0]).path):return False
    if not _stored(f'recreation/{job_id}/canary/{FIRST_CANARY_STEP}.mp4'): return False
    # Do not reuse if the source MP4 was replaced AFTER that canary was made.
    original=storage.client().head_object(Bucket=storage.bucket(),Key=job['original_key'])
    created=step.get('created_at')
    modified=original.get('LastModified')
    if not created or not modified or modified > created:
        return False
    return True


def report(job):
    """Free estimate without provider calls. Data are read from FFmpeg preflight."""
    preflight=(job.get('checkpoint') or {}).get('local_preflight') or {}
    if preflight.get('source_key')!=job.get('original_key') or not preflight.get('complete'):
        raise ValueError('Archive and finish /preflight before full-video planning')
    source=preflight.get('source_probe') or {}
    if not source.get('has_audio'):
        raise ValueError('Source soundtrack not found; original-audio preservation needs an audio track')
    identity=(job.get('checkpoint') or {}).get('identity') or {}
    if job.get('character')!='NICOLE' or identity.get('status')!='ready' or not identity.get('nicole_image_key'):
        raise ValueError('Nicole private identity reference is required')
    if not (240 <= source['width'] <= 4096 and 240 <= source['height'] <= 4096):
        raise ValueError('Unsupported source dimensions')
    quality_mode=quality.enabled(job)
    segments=(quality.quality_plan(source['duration']) if quality_mode
              else plan(source['duration'],reuse_first=_reusable_canary(job)))
    return {'source_duration':source['duration'],'segments':segments,'remaining_estimate_usd':estimate(segments),
            'generated_sections':len([s for s in segments if not s['reused']]),
            'reused_first_canary':bool(segments[0]['reused']), 'audio':'original_source', 'run_key':RUN_KEY,
            'continuity':('canonical_photo_reset_per_segment_no_warp' if quality.reset_identity_each_segment(job)
                          else 'chained_generated_last_frame_plus_200ms_local_blend')}


def _stored(key):
    try:
        storage.client().head_object(Bucket=storage.bucket(),Key=key)
        return True
    except ClientError as exc:
        code=str(exc.response.get('Error',{}).get('Code',''))
        if code in ('404','NoSuchKey','NotFound'):
            return False
        raise


def segment_key(job_id, i):
    return f'recreation/{job_id}/full_video/{RUN_KEY}/segments/{i:03}.mp4'


def source_key(job_id, i):
    return f'recreation/{job_id}/full_video/{RUN_KEY}/inputs/{i:03}.mp4'



def continuity_frame_key(job_id, previous_index):
    """Durable frame consumed by the following generation, never a source frame."""
    return (f'recreation/{job_id}/full_video/{RUN_KEY}/continuity/'
            f'last_frame_{previous_index:03}.jpg')


def _prepare_continuity_frame(job_id, previous_index, previous_generated_key):
    """Never submit the next paid segment until its predecessor is archived.

    A restarted worker reuses exactly the same private JPEG instead of
    recomputing from another source or accidentally switching characters.
    """
    key=continuity_frame_key(job_id,previous_index)
    if not _stored(previous_generated_key):
        raise RuntimeError('Previous GENERATED segment not archived; next submission is blocked')
    if _stored(key): return key
    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        previous=root/'previous_generated.mp4'
        last=root/'last_frame.jpg'
        storage.download_key(previous_generated_key,previous)
        ffmpeg.extract_last_frame(previous,last)
        storage.upload_key(key,last,mime='image/jpeg')
    return key


def continuity_payload(source_url, *, width, height, nicole_image_url, last_frame_url):
    """Follow the exact supported reference-to-video schema (NOT first_frame).

    The WaveSpeed reference-to-video route has NO strict first_frame field;
    last-frame guidance must be Image 2 and the seamless opening is explicitly
    requested in the prompt. A local seam blend anchors the physical cut.
    """
    payload=wan_canary.payload_for(source_url,width,height,[nicole_image_url,last_frame_url])
    payload['prompt'] += (
        ' CONTINUITY CHAIN: Image 2 is the ACTUAL FINAL FRAME of the previously '
        'GENERATED Nicole segment, not a new character reference. BEGIN this '
        'segment with Nicole, her exact pose, body placement, clothing, lighting, '
        'background, camera framing and orientation matching Image 2. '
        'Seamlessly CONTINUE the ongoing motion from Image 2 while following '
        'the next movement interval in Video 1. No resets, jump cuts, pose '
        'changes, wardrobe changes or camera jumps at the opening. Image 1 '
        'remains the sole identity reference for Nicole; Video 1 is motion-only. '
    )
    return payload


def final_key(job_id):
    return f'recreation/{job_id}/full_video/{RUN_KEY}/nicole_with_original_audio.mp4'


def step_key(i):
    return f'wan_{RUN_KEY}_segment_{i:03}'


def _guard_approval(job):
    if os.getenv('WORKER_MODE','')!='full_video_once':
        raise RuntimeError('Full video requires isolated full_video_once worker mode')
    if os.getenv('FULL_VIDEO_ONE_SHOT','false').lower()!='true':
        raise RuntimeError('Full video requires an explicit one-shot flag')
    if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
        raise RuntimeError('Full video paid generation is disabled')
    if os.getenv('ENABLE_PAID_VOICE_GENERATION','false').lower()=='true':
        raise RuntimeError('Paid voiceover is not allowed during original-audio recreation')
    if os.getenv('FULL_VIDEO_APPROVAL_ID') != RUN_KEY:
        raise RuntimeError('Full-video cost approval not configured')
    if os.getenv('FULL_VIDEO_SOURCE_JOB_ID')!=str(job['job_id']):
        raise RuntimeError('Full-video job ID does not match the owner-approved source')
    if not os.getenv('WAVESPEED_API_KEY'):
        raise RuntimeError('WaveSpeed key missing')
    allowed={int(x.strip()) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()}
    if job.get('owner_id') not in allowed or job.get('state')!='DRAFT':
        raise RuntimeError('Full video requires the authorized owner draft')
    metadata=report(job)
    cap_raw=os.getenv('FULL_VIDEO_MAX_USD','')
    try: cap=float(cap_raw)
    except (ValueError,TypeError): raise RuntimeError('An exact full-video USD cap is required') from None
    if not math.isfinite(cap) or cap<=0 or cap>MAX_APPROVAL_USD:
        raise RuntimeError('Full-video USD cap out of allowed bounds')
    if metadata['remaining_estimate_usd']>cap:
        raise RuntimeError('Full-video plan exceeds approved estimated budget')
    return metadata


def _archive_completed_provider(job_id, idx, step):
    output_key=segment_key(job_id,idx)
    if _stored(output_key): return output_key
    if step['state']!='COMPLETED':
        raise ValueError('Cannot archive a non-completed model prediction')
    result=step.get('result') or {}
    urls=result.get('outputs') or []
    if not isinstance(urls,list) or not urls:
        raise provider.NeedsReview('Completed segment lacks an output video')
    storage.store_provider_url(urls[0],output_key)
    return output_key


def _get_reused_first(job_id):
    step=db.get_step(job_id,FIRST_CANARY_STEP)
    if not step or step['state']!='COMPLETED':
        raise RuntimeError('First Nicole segment has no successful saved paid prediction')
    # Validate that the original provider request included the Nicole image.
    payload=(step.get('input') or {}).get('payload') or {}
    if not (payload.get('reference_images') and payload.get('reference_videos') and payload.get('duration')==3):
        raise RuntimeError('First canary is not an identity-specific 3-second result')
    key=f'recreation/{job_id}/canary/{FIRST_CANARY_STEP}.mp4'
    if not _stored(key):
        raise RuntimeError('Already paid Nicole result missing from private storage')
    return key


def _prepare_reference(job, segment):
    job_id=str(job['job_id'])
    key=source_key(job_id,segment['index'])
    if _stored(key): return key
    with tempfile.TemporaryDirectory() as td:
        f=Path(td);src=f/'source.mp4';clip=f/'segment.mp4'
        storage.download_key(job['original_key'],src)
        details=ffmpeg.trim(src,clip,segment['start'],segment['duration'])
        if not (2<=details['duration']<=15) or not (240<=min(details['width'],details['height'])<=4096):
            raise RuntimeError('Input segment failed media validation')
        if (quality.enabled(job) and
                os.getenv('RECREATION_REFERENCE_SCRUB','on').lower() != 'off'):
            cleaned=f/'motion_reference_clean.mp4'
            if overlay_cleanup.clean_motion_reference(clip,cleaned):
                clip=cleaned
                LOGGER.info('REFERENCE_OVERLAY_CLEANED segment=%d clean=true',segment['index'])
        storage.upload_key(key,clip)
    return key


def _merge_original_sound(job, segments, keys, *, destination_override=None, motion_aware=False, force_no_warp=False):
    """Locally smooth joins with previous frame, preserving original audio sync."""
    job_id=str(job['job_id']); destination=destination_override or final_key(job_id)
    if _stored(destination): return destination
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);original=root/'original.mp4';joined=root/'silent_join.mp4';final=root/'final.mp4'
        storage.download_key(job['original_key'],original)
        clips=[]
        for seg,key in zip(segments,keys):
            dest=root/f'seg_{seg["index"]:03}.mp4'
            raw=root/f'raw_{seg["index"]:03}.mp4'
            storage.download_key(key,raw)
            info=ffmpeg.probe(raw)
            if info['duration'] + .12 < seg['duration']:
                raise provider.NeedsReview('Generated clip shorter than the required source segment')
            ffmpeg.trim(raw,dest,0,seg['duration'])
            if clips:
                prev_end=root/f'boundary_{seg["index"]:03}_last.jpg'
                next_first=root/f'boundary_{seg["index"]:03}_first.jpg'
                adjusted=root/f'continuity_{seg["index"]:03}.mp4'
                ffmpeg.extract_last_frame(clips[-1],prev_end)
                if (force_no_warp or
                    (os.getenv('RECREATION_QUALITY_MODE','').lower()=='identity_lock'
                     and os.getenv('RECREATION_SEAM_MODE','').lower()=='cut_no_warp')):
                    # Existing 250ms dense optical flow can deform Nicole's
                    # face, hands, skirt and limbs. Do not synthesize anatomy
                    # to conceal a bad model boundary. Compare RAW frames.
                    ffmpeg.extract_first_frame(dest,next_first)
                    raw_error=ffmpeg.frame_difference(prev_end,next_first)
                    LOGGER.info('IDENTITY_LOCK seam_index=%d raw_frame_difference=%.4f no_warp=true',
                                seg['index'],raw_error)
                    if raw_error > .28:
                        # A full-frame mismatch can be genuine camera movement,
                        # not proof of face morphing. Never discard thirteen
                        # already-paid segments based only on this heuristic.
                        LOGGER.warning('IDENTITY_LOCK seam_warning=%d difference=%.4f '
                                       'unblended_hard_cut=true no_provider_retry=true',
                                       seg['index'], raw_error)
                    clips.append(dest)
                    continue
                if motion_aware:
                    ffmpeg.motion_interpolate_start(clips[-1],dest,adjusted,seconds=0.25)
                else:
                    ffmpeg.seam_blend_start(prev_end,dest,adjusted,seconds=0.20)
                ffmpeg.extract_first_frame(adjusted,next_first)
                error=ffmpeg.frame_difference(prev_end,next_first)
                if motion_aware:
                    quality=ffmpeg.seam_motion_metrics(clips[-1],adjusted)
                    LOGGER.info('MOTION_REMUX seam_index=%d first_frame_difference=%.4f motion_metrics=%s',
                                seg['index'],error,quality)
                else:
                    LOGGER.info('FULL_VIDEO seam_index=%d first_frame_difference=%.4f',seg['index'],error)
                if error > .10:
                    raise provider.NeedsReview('Seam continuity verification failed; video retained for review')
                clips.append(adjusted)
            else:
                clips.append(dest)
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=concat_start clips=%d',job_id[:8],len(clips))
        ffmpeg.concatenate(clips,joined)
        merged=ffmpeg.probe(joined)
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=concat_complete duration=%.3f audio=%s',
                    job_id[:8],merged['duration'],merged['has_audio'])
        # Motion references are cleaned before paid generation; final cleanup
        # is optional. A separate FFmpeg/inpainting pass can be costly and
        # must never make an otherwise deliverable video fail or lose paid clips.
        final_scrub=(os.getenv('RECREATION_FINAL_SCRUB','off').lower()=='on'
                     and os.getenv('RECREATION_QUALITY_MODE','').lower()=='identity_lock')
        if final_scrub:
            LOGGER.info('ASSEMBLY_PHASE job=%s stage=final_scrub_start',job_id[:8])
            cleaned_joined=root/'cleaned_join.mp4'
            try:
                if overlay_cleanup.clean_motion_reference(joined,cleaned_joined):
                    candidate=ffmpeg.probe(cleaned_joined)
                    if abs(candidate['duration']-merged['duration'])<.20:
                        joined=cleaned_joined
                        LOGGER.info('ASSEMBLY_PHASE job=%s stage=final_scrub_applied',
                                    job_id[:8])
                    else:
                        LOGGER.warning('ASSEMBLY_PHASE job=%s stage=final_scrub_skip reason=duration_mismatch',job_id[:8])
            except (RuntimeError,ValueError,TimeoutError) as exc:
                LOGGER.warning('ASSEMBLY_PHASE job=%s stage=final_scrub_skip error_type=%s',
                               job_id[:8],type(exc).__name__)
        duration_expected=sum(seg['duration'] for seg in segments)
        prepared=ffmpeg.probe(joined)
        overrun=prepared['duration']-duration_expected
        if 0.075<overrun<=max(0.5,len(segments)*.06):
            # Discrete per-clip 24fps output adds up to a few extra frames.
            # Stream-copy the assembled video down to the source timeline.
            # This avoids a third lossy video encode and keeps source audio synced.
            LOGGER.info('ASSEMBLY_PHASE job=%s stage=duration_correction_start overrun=%.3f',job_id[:8],overrun)
            corrected=root/'duration_corrected.mp4'
            measured=ffmpeg.trim_video_stream_copy(joined,corrected,duration_expected)
            LOGGER.info('ASSEMBLY_PHASE job=%s stage=duration_correction_complete actual=%.3f expected=%.3f',
                        job_id[:8],measured['duration'],duration_expected)
            if abs(measured['duration']-duration_expected)>.18:
                raise RuntimeError('Source timeline correction could not reach safe frame tolerance')
            joined=corrected
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=restore_audio_start',job_id[:8])
        info=ffmpeg.restore_source_audio(joined,original,final)
        duration_diff=abs(info['duration']-duration_expected)
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=restore_audio_complete actual=%.3f expected=%.3f difference=%.3f has_audio=%s',
                    job_id[:8],info['duration'],duration_expected,duration_diff,info['has_audio'])
        if not info['has_audio'] or duration_diff>.20:
            raise RuntimeError(
                'Final original-audio merge failed duration validation: '
                f'expected={duration_expected:.3f} actual={info["duration"]:.3f}')
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=upload_start',job_id[:8])
        storage.upload_key(destination,final)
        LOGGER.info('ASSEMBLY_PHASE job=%s stage=upload_complete',job_id[:8])
    return destination


def run_once():
    job_id=os.getenv('FULL_VIDEO_SOURCE_JOB_ID','')
    if not job_id: raise RuntimeError('Full-video source job not selected')
    job=db.get_job(job_id)
    if not job: raise RuntimeError('Unknown owner draft')
    info=_guard_approval(job)
    return _run_segments(job,info,guard=_guard_approval)


def _guard_auto(job):
    """Owner-initiated /generate is the authorization for this job's provider usage.

    No job-count, daily or monthly quotas. Existing at-most-once provider IDs
    and owner isolation remain mandatory. The total estimate is shown in chat.
    """
    if os.getenv('WORKER_MODE')!='app_always_on' or os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
        raise RuntimeError('Recreation app is not enabled')
    if os.getenv('ENABLE_PAID_VOICE_GENERATION','false').lower()=='true':
        raise RuntimeError('Original-audio mode must not generate paid voice')
    allowed={int(x.strip()) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()}
    if job.get('owner_id') not in allowed or job.get('state') not in ('RUNNING','WAITING_PROVIDER'):
        raise RuntimeError('Recreation job was not explicitly started by its owner')
    if not os.getenv('WAVESPEED_API_KEY'):
        raise RuntimeError('WaveSpeed API key missing on isolated worker')
    info=report(job)
    # This is a per-job spend guard, not a daily or monthly usage quota.
    # The user still starts each paid job by sending /generate.
    try: cap=float(os.getenv('APP_MAX_ESTIMATED_USD','10.00'))
    except (ValueError, TypeError): raise RuntimeError('Invalid app per-video cost cap') from None
    if not math.isfinite(cap) or not (0 < cap <= 100):
        raise RuntimeError('Invalid app per-video cost cap')
    if info['remaining_estimate_usd'] > cap:
        raise provider.NeedsReview(
            f'Estimated video cost ${info["remaining_estimate_usd"]:.2f} exceeds '
            f'the per-video spending guard ${cap:.2f}. No paid generation submitted.')
    return info


def run_auto(job):
    info=_guard_auto(job)
    return _run_segments(job,info,guard=_guard_auto)


def _run_segments(job,info,*,guard):
    job_id=str(job['job_id'])
    segments=info['segments'];keys=[]
    # Re-check approval before EVERY new paid provider request; an operator can
    # disable the service between segments without losing the saved prediction.
    for seg in segments:
        idx=seg['index']
        if seg['reused']:
            keys.append(_get_reused_first(job_id))
            LOGGER.info('FULL_VIDEO existing_first_nicole_segment_reused=true')
            continue
        key=segment_key(job_id,idx)
        if _stored(key):
            keys.append(key);continue
        step=db.get_step(job_id,step_key(idx))
        if step is None:
            guard(db.get_job(job_id))
            # The previous generated output, never the ORIGINAL source, is the
            # continuity anchor for the next model submission. This is an
            # actual dependency: if it is missing we fail closed without cost.
            if len(keys) != idx:
                raise RuntimeError('Segment order invalid; cannot chain continuity')
            # The FIRST segment of a fresh job uses Nicole's identity only.
            # Canonical quality mode always uses Nicole's SAVED identity photo.
            # Recursive Wan-frame chaining compounds artifacts over long clips.
            reset=quality.reset_identity_each_segment(job)
            last_key=(_prepare_continuity_frame(job_id,idx-1,keys[-1])
                      if idx>0 and not reset else None)
            ref=_prepare_reference(job,seg)
            identity=(job['checkpoint'] or {}).get('identity') or {}
            image=storage.presign(identity['nicole_image_key'],3600)
            reference=storage.presign(ref,3600)
            source_media=job['checkpoint']['local_preflight']['source_probe']
            if last_key:
                previous_frame=storage.presign(last_key,3600)
                payload=continuity_payload(reference,width=source_media['width'],
                    height=source_media['height'],nicole_image_url=image,
                    last_frame_url=previous_frame)
            else:
                payload=wan_canary.payload_for(reference,source_media['width'],source_media['height'],[image])
                payload['prompt']+=' Opening segment: establish Nicole from Image 1; follow Video 1 motion only.'
            if quality.enabled(job):
                # Explicitly honor the owner's scenery and outfit instead of
                # the legacy prompt, which unintentionally kept Video 1 scenery.
                payload['prompt']=quality.build_prompt(job,chained=bool(last_key))
                payload['seed']=quality.fixed_seed(job)
            payload['duration']=seg['output_seconds']
            LOGGER.info('FULL_VIDEO submitting_segment=%d estimate_usd=%.2f',idx,seg['estimated_usd'])
            step=provider.submit_once(job_id,step_key(idx),wan_canary.MODEL,payload)
            LOGGER.info('FULL_VIDEO submitted_segment=%d prediction_saved=%s',idx,bool(step.get('external_request_id')))
        # On restart, the saved provider request MUST carry the chained frame
        # and the current Nicole identity. This prevents reverting to v1-style
        # independent-segment requests under the same step key.
        if step['state'] in ('SUBMITTED','POLLING','COMPLETED'):
            submitted=((step.get('input') or {}).get('payload') or {})
            refs=submitted.get('reference_images') or []
            expected=continuity_frame_key(job_id,idx-1) if idx>0 else None
            current_identity=(job.get('checkpoint') or {}).get('identity',{}).get('nicole_image_key')
            identity_ok=bool(refs and current_identity and current_identity in unquote(urlsplit(refs[0]).path))
            reset=quality.reset_identity_each_segment(job)
            continuity_ok=((len(refs)==1 and (idx==0 or reset)) or
                           (not reset and idx>0 and len(refs)>=2 and
                            expected in unquote(urlsplit(refs[1]).path)))
            if not (identity_ok and continuity_ok):
                raise provider.NeedsReview('Submitted segment lacks verified Nicole identity or expected chained-frame continuity inputs')
        if step['state'] in ('SUBMITTING','UNCERTAIN','FAILED'):
            raise provider.NeedsReview('Uncertain provider request; manual reconciliation required')
        if step['state']!='COMPLETED':
            result=provider.poll(step)
            if str(result.get('status','')).lower()!='completed':
                LOGGER.info('FULL_VIDEO waiting_for_segment=%d prediction_saved=true',idx)
                return 'processing'
            step=db.get_step(job_id,step_key(idx))
        keys.append(_archive_completed_provider(job_id,idx,step))
        LOGGER.info('FULL_VIDEO saved_segment=%d',idx)
    final=_merge_original_sound(job,segments,keys,motion_aware=(guard is _guard_auto))
    LOGGER.info('FULL_VIDEO complete=true original_audio=true segments=%d archive_saved=true',len(keys))
    _deliver_once(job,final)
    return 'completed'


def _deliver_once(job, key, *, delivery_key=None, caption=None):
    """At-most-once Telegram DM delivery; archive survives Telegram failure.

    An uncertain sendMessage/sendVideo outcome is never blindly retried.
    """
    job_id=str(job['job_id'])
    delivery_key=delivery_key or f'{RUN_KEY}_telegram_delivery'
    previous=db.get_step(job_id,delivery_key)
    if previous:
        if previous['state']=='COMPLETED': return
        raise provider.NeedsReview('Telegram delivery outcome uncertain; inspect the conversation')
    reserved=db.reserve_one_shot(job_id,delivery_key,'telegram',{'video_key':key,'purpose':'full_video_nicole'})
    if not reserved:
        raise provider.NeedsReview('Telegram delivery was reserved by another worker')
    try:
        result=telegram.telegram('sendVideo',{'chat_id':job['chat_id'], 'video':storage.presign(key,3600),
            'caption':(caption or 'Nicole recreation finished. Original soundtrack restored. 480p staging.')})
        db.external_finished(job_id,delivery_key,{'message_id':result.get('message_id')})
    except Exception as exc:
        raise provider.NeedsReview('Final file archived, but Telegram delivery needs manual inspection') from exc



# The former identity-lock v3 generated thirteen already PAID 4s segments
# before rejecting a harmless full-frame seam metric. This explicit salvage
# pathway never calls WaveSpeed, ElevenLabs, or any model provider.
SALVAGE_SOURCE_RUN_KEY = 'telegram_recreation_cleangraphics_v3_20261115'
SALVAGE_OUTPUT_VERSION = 'local_hardcuts_v1'


def salvage_existing_v3(job):
    """Join only archived completed outputs; no paid submissions or regen.

    It cannot remove morphing that already exists INSIDE a Wan clip, but
    delivers a source-audio-synced recovered video for review.
    """
    if job.get('stage') != 'SALVAGE' or job.get('state') not in ('RUNNING','WAITING_PROVIDER'):
        raise RuntimeError('Salvage must be explicitly queued by the authorized owner')
    if job.get('character')!='NICOLE' or not job.get('original_key'):
        raise RuntimeError('Saved Nicole source is required')
    job_id=str(job['job_id'])
    cp=(job.get('checkpoint') or {}).get('local_preflight') or {}
    original=(cp.get('source_probe') or {})
    if cp.get('source_key')!=job['original_key'] or not cp.get('complete'):
        raise RuntimeError('Archived source preflight missing')
    duration=round(float(original.get('duration') or 0),3)
    n=math.ceil(duration/3.99)
    if n!=int(job['segment_total']) or not 1<=n<=MAX_CLIPS:
        raise RuntimeError('Old saved segment count does not match source')
    lengths=[round(duration/n,3)]*n
    lengths[-1]=round(duration-sum(lengths[:-1]),3)
    at=0.0
    segments=[];keys=[]
    for index,length in enumerate(lengths):
        if not 2<=length<=3.99:
            raise RuntimeError('Unexpected saved segment timeline')
        step=db.get_step(job_id,f'wan_{SALVAGE_SOURCE_RUN_KEY}_segment_{index:03}')
        if not step or step.get('state')!='COMPLETED':
            raise provider.NeedsReview('A paid source segment is missing or not completed; no regeneration attempted')
        key=f'recreation/{job_id}/full_video/{SALVAGE_SOURCE_RUN_KEY}/segments/{index:03}.mp4'
        if not _stored(key):
            raise provider.NeedsReview('A completed source segment is missing from storage; no regeneration attempted')
        segments.append({'index':index,'start':round(at,3),'duration':length})
        keys.append(key)
        at+=length
    destination=(f'recreation/{job_id}/full_video/{SALVAGE_SOURCE_RUN_KEY}/'
                 f'{SALVAGE_OUTPUT_VERSION}/nicole_with_original_audio.mp4')
    _merge_original_sound(job,segments,keys,destination_override=destination,
                          force_no_warp=True,motion_aware=False)
    _deliver_once(
        job,destination,
        delivery_key=f'{SALVAGE_SOURCE_RUN_KEY}_{SALVAGE_OUTPUT_VERSION}_telegram_delivery',
        caption=('Recovered 13 already-paid 480p segments with source audio and no '
                 'AI regeneration. The cuts are unwarped; character drift already '
                 'inside the generated clips may remain.'),
    )
    LOGGER.info('SALVAGE completed=true reused_paid_clips=%d new_provider_calls=0',n)
    return 'completed'
