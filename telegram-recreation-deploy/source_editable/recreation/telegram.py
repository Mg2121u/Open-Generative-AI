"""Telegram input/output and commands. Keep all bot tokens in environment variables."""
import os
import logging
import subprocess
import sys
import requests
from fastapi import APIRouter,Header,HTTPException,Request
from . import db,voiceover
from .telegram_setup import bot_token

logger=logging.getLogger(__name__)
router=APIRouter()

BOT_TOKEN=bot_token()
BOT_API_URL=os.environ.get('TELEGRAM_BOT_API_URL','https://api.telegram.org').rstrip('/')
WEBHOOK_SECRET=os.environ.get('TELEGRAM_WEBHOOK_SECRET','')
ALLOWED_USERS={int(x) for x in os.environ.get('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()}


def telegram(method,payload):
    if not BOT_TOKEN:
        raise RuntimeError('TELEGRAM_BOT_TOKEN missing')
    try:
        result=requests.post(f'{BOT_API_URL}/bot{BOT_TOKEN}/{method}',json=payload,timeout=35)
        result.raise_for_status()
        data=result.json()
    except (requests.RequestException,ValueError) as exc:
        raise RuntimeError('Telegram connection failed ('+type(exc).__name__+')') from None
    if not data.get('ok'):
        raise RuntimeError(f'Telegram {method}: {data.get("description", "unknown")}')
    return data.get('result')


def say(chat,text):
    return telegram('sendMessage',{'chat_id':chat,'text':text[:4096]})


def show(job):
    if not job: return 'No recreation job found. Send /new to begin.'
    saved=len(job['outputs'])
    if (job.get('checkpoint') or {}).get('segment_revision'):
        with db.connect() as conn:
            saved=conn.execute("""SELECT count(*) AS n FROM recreation_steps
                WHERE job_id=%s AND step_key='wan_individual_section_revision_v1'
                  AND state='COMPLETED'""",(job['job_id'],)).fetchone()['n']
    elif job['stage'] in ('AUTO_GENERATE','QA_OWNER_APPROVED','DONE'):
        from .full_video import RUN_KEY
        with db.connect() as conn:
            saved=conn.execute('SELECT count(*) AS n FROM recreation_steps WHERE job_id=%s AND step_key LIKE %s AND state=%s',
                               (job['job_id'],f'wan_{RUN_KEY}_segment_%','COMPLETED')).fetchone()['n']
    return (f'Job {str(job["job_id"])[:8]} · {job["state"]}\n'
        f'Stage: {job["stage"]}\n'
        f'Character: {job["character"]} | Model: {job["model"]} | 480p\n'
        f'Quality: {"identity-lock / no warping" if os.getenv("RECREATION_QUALITY_MODE","").lower()=="identity_lock" else "standard"}\n'
        f'Scenery: {job["scenery"]}\n'
        f'Scenery image: {((job.get("checkpoint") or {}).get("scenery_reference") or {}).get("status","none")}\n'
        f'Clothes: {job["outfit"]}\nVoice: ElevenLabs v4 · {job.get("voiceover_voice", "AUTO")} · {"on" if job.get("voiceover_text") else "off (original audio)"}\n'
        f'Source archive: {"saved" if job.get("original_key") else "not yet stored"}\n'
        f'Nicole identity: {((job.get("checkpoint") or {}).get("identity") or {}).get("status", "missing")}\n'
        f'Generated sections: {saved} / {job["segment_total"] or "pending plan"}\n'
        f'Local FFmpeg preview: {"complete" if (job.get("checkpoint") or {}).get("local_preflight",{}).get("complete") else "not completed"}\n'
        + (f'Issue: {job["last_error"][:400]}' if job['last_error'] else '')
        + ('\\nQA: owner accepted a failed face match (NOT verified)' if
           ((job.get('checkpoint') or {}).get('quality_override') or {}).get('type')=='owner_accepted_facial_mismatch'
           else '')
        + (f'\\nQA: {len((job.get("checkpoint") or {}).get("face_qa_approvals") or {})} section(s) owner accepted as imperfect (not verified)' if
            (job.get('checkpoint') or {}).get('face_qa_approvals') else '')
        + (f'\\nReview commands: /review_qa {str(job["job_id"])[:8]} then /approve_qa {str(job["job_id"])[:8]}' if
           job.get('state')=='NEEDS_REVIEW' and
           str(job.get('last_error') or '').startswith('Nicole facial identity QA ') else ''))


def _image_attachment(message, *, allow_reply=False):
    """Select Telegram image photo or JPEG/PNG/WEBP document, never video."""
    attached=message
    if allow_reply and not message.get('photo') and not (
        (message.get('document') or {}).get('mime_type','').lower().startswith('image/')):
        attached=message.get('reply_to_message') or {}
    photos=attached.get('photo') or []
    if photos:return photos[-1]
    document=attached.get('document') or {}
    if document.get('mime_type','').lower() in ('image/jpeg','image/png','image/webp'):
        return document
    return None


def process_message(message):
    chat=message.get('chat',{}).get('id')
    owner=message.get('from',{}).get('id')
    if not chat or not owner: return
    body=(message.get('text') or message.get('caption') or '').strip()
    cmd,_,value=body.partition(' ')
    cmd=cmd.lower().split('@',1)[0]
    if chat != owner:
        say(chat,'Please message this app in a private chat.');return
    if cmd == '/id':
        say(chat,f'Your Telegram user ID: {owner}. Save this as ALLOWED_TELEGRAM_USER_IDS in the staging Render service.')
        return
    if not ALLOWED_USERS or int(owner) not in ALLOWED_USERS:
        say(chat,'This app is private. Send /id to retrieve your ID for access.'); return
    if cmd in ('/start','/help'):
        say(chat,'Video Recreation Studio (continuous mode)\n/id — show your Telegram ID\n/new — new job\n/redo — new quality redo from latest completed video (no upload)\n/segments [job8] — list saved sections and time ranges\n/view_segment [job8] N — preview one saved section\n/redo_segment [job8] N — draft replacement of only section N (NO CHARGE)\n/confirm_segment — approve ONE paid Wan generation and final reassembly\nSend an MP4 video or video document\n/character Nicole | Aria | Aria_Nicole\n/scenery <description> or photo with caption /scenery or reply /scenery to photo\n/scenery — arm the NEXT photo as a scenery reference\n/clothes <description>\n/instructions <extra requirements>\n/model wan-3.0 (original reference pipeline)\n/voice Nicole | Aria | Auto — selected voice\n/voiceover <text> — optional spoken overlay\n/voiceover off — original audio only\n/status — saved progress\n/archive — save original video in private storage (no AI calls)\n/preflight — trim and merge a short source preview locally (no AI calls)\n/fullplan — full source duration, segments and estimated cost (no AI calls)\n/identity — attach a clear Nicole reference photo with caption /identity, or reply /identity to a photo\n/generate — start paid quality run\n/salvage — assemble already-paid segments (no new Wan calls)\n/finish JOB8 — recover a fully generated video after storage failure (NO WAN CHARGES)\n/review_qa JOB8 — preview a face-QA rejected clip\n/approve_qa JOB8 — accept archived face-QA mismatch; continue remaining sections (paid) or deliver one\n/resume — retry a failed stage\n/cancel — cancel job\n\n480p default. Source movement and original audio are preserved as targets.')
        return
    if cmd=='/new':
        job=db.create_draft(chat,owner)
        say(chat,f'New job {job[:8]}. Your saved Nicole reference is reused if available. Send an MP4, optionally set /scenery /clothes /instructions, then /generate.');return
    if cmd=='/redo':
        result=db.create_redo_from_latest(chat,owner)
        if not result:
            say(chat,'No completed Nicole job with a saved original video and identity photo is available to redo.');return
        say(chat, f'Quality redo draft {result["job_id"][:8]} created from '
                  f'{result["from_job"][:8]}. Original MP4, Nicole reference, '
                  'scenery and outfit are reused. The old result stays untouched. '
                  'No new AI charges yet. Use /fullplan to see the updated plan, '
                  'or /generate to authorize the new paid Wan 480p run.')
        return
    if cmd=='/fix_scenery':
        say(chat,'The local cutout/green-screen workaround has been retired. '
            'To remake the scene with Wan references, send /redo to create '
            'a new draft from a completed source, check that your scenery '
            'photo is ready in /status, then explicitly approve the new paid '
            'generation using /generate. This command did not start a render.')
        return
    if cmd=='/finish':
        try:
            saved=db.queue_final_assembly(owner,value.strip())
            if saved:
                say(chat,(
                    f'Local-only final assembly queued for {str(saved["job_id"])[:8]}. '
                    f'All {saved["segment_total"]} paid Wan sections must already exist; '
                    'none will be regenerated or repurchased. The final video '
                    'will be prepared within the storage size limit with original audio, '
                    'then delivered privately. /status tracks the result.'
                ))
            else:
                # Do not steal an active worker lease or cause two independent
                # video deliveries. Register the owner's recovery preference
                # on an active, fully-generated job and honor it if it fails.
                pending=db.defer_final_assembly(owner,value.strip())
                if pending:
                    say(chat,(
                        f'Job {str(pending["job_id"])[:8]} already has a worker '
                        f'assembling its {pending["segment_total"]} completed '
                        'Wan sections. Your /finish request is SAVED. '
                        'If the current assembly succeeds it will deliver normally; '
                        'if it fails, Studio will recover using LOCAL video tools '
                        'without recharging or regenerating any section. '
                        'Original audio and owner QA approvals remain preserved. '
                        'No further command needed; check /status.'
                    ))
                else:
                    say(chat,(
                        'No recovery was queued. The job may be completed, '
                        'already recovering, have pending/uncertain provider '
                        'requests, or no longer match the saved source. '
                        'Use /status for its current stage. '
                        'No Wan generation was started by /finish.'
                    ))
        except (ValueError,TypeError,KeyError) as exc:
            say(chat,'Archive recovery: '+str(exc)[:320])
        return
    if cmd in ('/review_qa','/approve_qa'):
        from . import full_video
        prefix=value.strip().lower()
        try:
            review=db.qa_review_job(owner,prefix)
            if not review:
                say(chat,'No matching paused Nicole face-QA job was found. Check the exact 8-character job ID in /status. Provider or submission errors cannot be overridden.')
                return
            if int(review.get('segment_total') or 0)>1:
                from . import qa_continue
                info=qa_continue.reviewable(review)
                if cmd=='/review_qa':
                    ratio=(f'{info["matched"]}/{info["detected"]}' if
                           info.get('detected') is not None else 'unverified')
                    cost=info['remaining_estimated_usd']
                    telegram('sendVideo',{'chat_id':chat,
                        'video':full_video.storage.presign(info['key'],3600),
                        'caption':(
                            f'PAUSED QA REVIEW {prefix} | section {info["number"]}/{info["total"]}. '
                            f'Nicole face check {info["status"]} ({ratio}). This paid clip is archived. '
                            f'Next sections: {info["remaining_count"]}, estimated additional Wan cost '
                            f'${cost:.2f} (actual provider billing may differ). '
                            f'Accept this imperfect section and RESUME remaining paid sections: '
                            f'/approve_qa {prefix}. No existing section will be regenerated.'
                        )})
                    return
                queued=db.approve_qa_continue(
                    review['job_id'],owner,index=info['index'],
                    expected_step=info['source_step'],expected_key=info['key'],
                    qa_status=info['status'])
                if queued:
                    say(chat,(
                        f'Approved archived section {info["number"]}/{info["total"]} for job {prefix}. '
                        'The original failed-face QA evidence remains recorded; it is NOT a pass. '
                        f'Resuming {info["remaining_count"]} remaining sections (estimated '
                        f'additional Wan cost ${info["remaining_estimated_usd"]:.2f}, actual billing may differ). '
                        'Completed provider IDs are preserved; no previously paid section is regenerated. '
                        'Another face mismatch may still pause for your approval. /status tracks progress.'
                    ))
                else:
                    say(chat,'Approval not queued. The job has changed or has an ambiguous provider request. '
                        'No additional Wan request was authorized; use /status.')
                return
            job_id=str(review['job_id'])
            clip=full_video.segment_key(job_id,0)
            step=db.get_step(job_id,full_video.step_key(0))
            if (not step or step.get('state')!='COMPLETED'
                    or not full_video._stored(clip)):
                say(chat,'The previously paid clip is not fully archived, so approval is blocked. No new generation was triggered.')
                return
            if cmd=='/review_qa':
                telegram('sendVideo',{'chat_id':chat,
                    'video':full_video.storage.presign(clip,3600),
                    'caption':('Review ONLY: job '+prefix+
                               ' FAILED Nicole face QA. '+str(review['last_error'])[:150]+
                               ' To ACCEPT the mismatch as-is, send /approve_qa '+prefix+
                               '. This does not improve the face or charge Wan.')})
                return
            queued=db.approve_qa_review(job_id,owner,expected_step=full_video.step_key(0))
            say(chat,('Owner QA override accepted. Reassembling the already-paid clip '
                      'and original soundtrack for private delivery. No new Wan request '
                      'will be made. The failed facial match remains recorded. Check /status.'
                      if queued else 'Approval not queued: the saved job is not eligible or was already approved. No new Wan charge.'))
        except (ValueError,TypeError,KeyError) as exc:
            say(chat,'QA review command unavailable: '+str(exc)[:300])
        return
    if cmd in ('/segments','/view_segment','/redo_segment'):
        # One-based section numbers to avoid exposing internal zero-based
        # indices. Optional job ID is an EXACT eight-character owner prefix.
        from . import quality,full_video
        args=value.strip().split()
        try:
            if cmd=='/segments':
                if len(args)>1:raise ValueError('Use /segments or /segments f6634b83')
                parent=db.completed_segment_job(owner,args[0].lower() if args else None)
                if not parent:raise ValueError('No completed original Nicole video available')
                duration=parent['checkpoint']['local_preflight']['source_probe']['duration']
                segments=quality.quality_plan(duration)
                if len(segments)!=parent['segment_total']:
                    raise ValueError('This video uses an older section plan; do not mix versions')
                lines=[f'Completed video {str(parent["job_id"])[:8]}: {len(segments)} sections']
                lines.extend(f'{seg["index"]+1}: {seg["start"]:.1f}s–{seg["start"]+seg["duration"]:.1f}s'
                             for seg in segments[:55])
                lines.append('Preview: /view_segment '+str(parent["job_id"])[:8]+' N')
                lines.append('Redo one: /redo_segment '+str(parent["job_id"])[:8]+' N')
                say(chat,'\n'.join(lines))
            else:
                if len(args)==1:
                    prefix=None
                    number=int(args[0])
                elif len(args)==2:
                    prefix=args[0].lower()
                    number=int(args[1])
                else:
                    raise ValueError('Use /redo_segment f6634b83 3 (or /view_segment f6634b83 3)')
                if cmd=='/redo_segment':
                    draft=db.create_segment_revision(chat,owner,prefix,number)
                    say(chat, f'Single-section draft {draft["job_id"][:8]} ready.\n'
                        f'Original video: {draft["parent_id"][:8]}\n'
                        f'Only section {number}/{draft["total"]}: {draft["start"]:.1f}–{draft["end"]:.1f} seconds\n'
                        f'Estimated one-section Wan cost: ${draft["estimate"]:.2f} (provider actual may vary)\n'
                        'All other sections and the original audio will be reused. '
                        'Old completed video is unchanged.\n'
                        'Use /instructions to adjust this section if needed. '
                        'Send /confirm_segment to authorize the ONE paid Wan generation. '
                        'Nothing has been charged yet.')
                else:
                    parent=db.completed_segment_job(owner,prefix)
                    if not parent:raise ValueError('Completed original Nicole video not found')
                    segments=quality.quality_plan(
                        parent['checkpoint']['local_preflight']['source_probe']['duration'])
                    if len(segments)!=parent['segment_total'] or not 1<=number<=len(segments):
                        raise ValueError('Invalid section number for this completed video')
                    clip=full_video.segment_key(str(parent['job_id']),number-1)
                    if not full_video._stored(clip):
                        raise ValueError('This archived section is currently unavailable')
                    telegram('sendVideo',{'chat_id':chat,'video':full_video.storage.presign(clip,3600),
                                         'caption':f'Original video {str(parent["job_id"])[:8]} · section {number}/{len(segments)} · preview (no AI charge)'})
        except (ValueError,TypeError,KeyError) as exc:
            say(chat,'Section command unavailable: '+str(exc)[:300])
        return
    job=db.latest_job(owner)
    if cmd=='/confirm_segment':
        if job and ((job.get('checkpoint') or {}).get('scenery_reference') or {}).get('status') in ('awaiting','pending','failed'):
            say(chat,'Scenery image is not archived yet. Finish sending /scenery photo and confirm it is ready in /status before paying.')
            return
        if not job or job.get('stage')!='SEGMENT_REDO':
            say(chat,'Create a section draft first with /redo_segment f6634b83 3.');return
        if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
            say(chat,'Paid Wan generation is disabled. Your single-section draft is preserved.');return
        updated=db.queue_segment_revision(job['job_id'],owner)
        say(chat,('Confirmed: exactly ONE new Wan section has been queued. '
                  'The other sections will be reused without provider calls. '
                  'Your original video is untouched. Use /status to follow progress.'
                  if updated else 'Section revision is already running or was previously confirmed; no duplicate charge queued.'))
        return
    # Sending a video is the ordinary way to begin another recreation. If the
    # previous job has already started/completed, create a fresh draft and carry
    # forward only this owner's saved Nicole identity image. No AI is charged.
    incoming_video=message.get('video') or (message.get('document') if
        (message.get('document') or {}).get('mime_type','').lower() in ('video/mp4','application/octet-stream') else None)
    if incoming_video and (not job or job.get('state')!='DRAFT'):
        fresh=db.create_draft(chat,owner)
        job=db.get_job(fresh,owner)
    # A photo may be sent together with caption /scenery, as a REPLY to a
    # previously sent photo, or as the next message after bare /scenery.
    scene_status=(((job or {}).get('checkpoint') or {}).get('scenery_reference') or {}).get('status')
    selected_photo=_image_attachment(message,allow_reply=(cmd=='/scenery'))
    if cmd=='/scenery' and not selected_photo and not value.strip():
        if not job or job.get('state')!='DRAFT':
            say(chat,'Scenery reference can only be changed in an editable draft. Use /new or /redo.')
            return
        if db.await_scenery_image(job['job_id'],owner):
            say(chat,'Send the scenery photo next (JPEG, PNG, WEBP, max 10 MB), '
                'or send a photo with caption /scenery. This replaces the background '
                'reference only. No paid AI call yet.')
        else:
            say(chat,'Unable to arm scenery image on this draft.')
        return
    if selected_photo and (cmd=='/scenery' or (scene_status=='awaiting' and not cmd.startswith('/'))):
        if not job or job.get('state')!='DRAFT':
            say(chat,'Scenery image can only be changed on an editable draft. Use /new or /redo.')
            return
        if selected_photo.get('file_size',0)>10_000_000:
            say(chat,'Scenery image must be under 10 MB. Send a JPEG, PNG or WEBP.')
            return
        details=value.strip() if cmd=='/scenery' else (message.get('caption') or '').strip()
        if not db.record_scenery_image(job['job_id'],owner,selected_photo['file_id'],details):
            say(chat,'Could not save scenery reference to this draft.')
            return
        try:
            subprocess.Popen([sys.executable,'-m','recreation.scenery_ingest_once',
                              str(job['job_id'])],close_fds=True)
            say(chat,'Scenery image received. Archiving it privately. Check /status '
                'for Scenery image: ready before /generate or /confirm_segment. '
                'Nicole identity and original audio stay unchanged. No AI charge.')
        except Exception:
            logger.exception('Scenery upload subprocess failed')
            say(chat,'Scenery image recorded but archiving did not start. '
                'Resend the photo with caption /scenery. No paid request was made.')
        return
    if cmd=='/status': say(chat,show(job));return
    if not job:
        say(chat,'Send /new first.');return
    if (job.get('checkpoint') or {}).get('segment_revision') and cmd in ('/generate','/archive','/preflight','/identity'):
        say(chat,'This draft replaces one section only. Use /confirm_segment to authorize it, or /new for a new video.');return
    if (job.get('checkpoint') or {}).get('segment_revision') and cmd in ('/character','/model','/voice','/voiceover'):
        say(chat,'Section revisions retain Nicole, Wan 3.0 and the original soundtrack. Change only /instructions, /scenery or /clothes.');return
    if cmd in ('/scenery','/clothes','/instructions','/character','/model','/voice','/voiceover'):
        if not value.strip():
            say(chat,f'Send {cmd} followed by your requested value.');return
        col={'/scenery':'scenery','/clothes':'outfit','/instructions':'instructions',
             '/character':'character','/model':'model',
             '/voice':'voiceover_voice','/voiceover':'voiceover_text'}[cmd]
        if col=='character':
            value=value.strip().upper().replace('+','_').replace(' ','_')
            if value not in ('NICOLE','ARIA','ARIA_NICOLE'):
                say(chat,'Characters: Nicole, Aria, Aria_Nicole');return
        if col=='model':
            if value.strip().lower() not in ('wan-3.0',):
                say(chat,'The original verified 67-node workflow uses Wan 3.0 reference-to-video. Other models require separate parity testing before activation.');return
            value='wan-3.0'
        if col=='voiceover_voice':
            value=value.strip().upper()
            try:
                voiceover.voice_id_for(job['character'],value)
            except (ValueError,voiceover.VoiceConfigurationError) as exc:
                say(chat,str(exc));return
        if col=='voiceover_text':
            if value.strip().lower() in ('off','none','disable'):
                value=''
            else:
                try: value=voiceover.validate_text(value)
                except ValueError as exc:
                    say(chat,str(exc));return
                try:voiceover.voice_id_for(job['character'],job.get('voiceover_voice','AUTO'))
                except (ValueError,voiceover.VoiceConfigurationError) as exc:
                    say(chat,str(exc));return
        if not db.draft_update(job['job_id'],owner,col,value.strip()):
            say(chat,'This job is already running. Send /new to change requirements.');return
        say(chat,f'{col.title()} saved.');return
    if cmd=='/identity':
        if job.get('state')!='DRAFT' or job.get('character')!='NICOLE':
            say(chat,'Choose /character Nicole in an editable draft first.');return
        attached=message
        if not attached.get('photo') and not ((attached.get('document') or {}).get('mime_type','').lower().startswith('image/')):
            attached=message.get('reply_to_message') or {}
        photo=(attached.get('photo') or [])
        file_obj=(photo[-1] if photo else (attached.get('document') if (attached.get('document') or {}).get('mime_type','').lower() in ('image/jpeg','image/png','image/webp') else None))
        if not file_obj:
            say(chat,'Attach one clear Nicole reference photo and add /identity as its caption, or reply /identity to the photo. One face, no collage. No AI calls will run.');return
        if file_obj.get('file_size',0)>10_000_000:
            say(chat,'Reference photo exceeds 10 MB. Please send a smaller JPEG, PNG, or WEBP.');return
        if not db.record_identity_image(job['job_id'],owner,file_obj['file_id']):
            say(chat,'Could not save Nicole reference on this draft.');return
        try:
            subprocess.Popen([sys.executable,'-m','recreation.identity_ingest_once',str(job['job_id'])],close_fds=True)
            say(chat,'Nicole reference received. Archiving it privately; check /status for Identity: ready. Send /generate when your source video is ready. No AI is called until then.')
        except Exception:
            logger.exception('Identity ingestion subprocess failed')
            say(chat,'Reference recorded but archive did not start. Resend the image with caption /identity.')
        return
    media=incoming_video
    if media:
        if (job.get('checkpoint') or {}).get('segment_revision'):
            say(chat,'Cannot change the source on a single-section revision. Use /new for a different video.');return
        size=media.get('file_size',0)
        if size>20_000_000 and BOT_API_URL=='https://api.telegram.org':
            say(chat,'This source is over the cloud Bot API 20 MB download limit. Configure the local Telegram Bot API server first so the large file can be processed without shrinking it.');return
        if not db.draft_update(job['job_id'],owner,'source_file_id',media['file_id']):
            say(chat,'This job has started. Send /new for another source.');return
        say(chat,f'Video ready ({size/1048576:.1f} MB). Nicole identity is reused when available. Send /generate to start, or /identity to replace her photo.');return
    if cmd=='/archive':
        if not job.get('source_file_id'):
            say(chat,'Upload a source video first.'); return
        if job.get('original_key'):
            say(chat,'Original video is already archived. Send /status.'); return
        if job['state'] != 'DRAFT':
            say(chat,'Archiving is available for drafts only. Send /status.'); return
        try:
            subprocess.Popen([sys.executable,'-m','recreation.ingest_once'], close_fds=True)
            say(chat,'Safe archive requested. No AI generation will run. Check /status shortly.')
        except Exception:
            logger.exception('Could not launch staging archiver')
            say(chat,'Archive service did not start. Your source reference is still saved.')
        return
    if cmd=='/preflight':
        updated=db.queue_local_preflight(job['job_id'],owner)
        say(chat,'FFmpeg source preflight queued (no AI calls). Use /status to track checkpoints.' if updated else 'First save an uploaded video with /archive, then retry /preflight.')
        return
    if cmd=='/fullplan':
        try:
            from . import full_video
            rpt=full_video.report(job)
            say(chat, f'Nicole full-video staging plan (no paid calls):\n'
                f'Duration: {rpt["source_duration"]:.1f}s\n'
                f'Segments: {len(rpt["segments"])}\n'
                f'Already generated and reusable: {"first 3-second clip" if rpt["reused_first_canary"] else "none"}\n'
                f'New Wan generations: {rpt["generated_sections"]}\n'
                f'Estimated additional 480p cost: ${rpt["remaining_estimate_usd"]:.2f}\n'
                f'Final audio: original source soundtrack\n'
                f'Continuity: {"canonical Nicole photo reset per segment, hard cuts, no warp" if os.getenv("RECREATION_QUALITY_MODE","").lower()=="identity_lock" else "legacy chained frames + local seam blending"}\n'
                'Charges occur only after /generate; actual provider billing may differ.')
        except (ValueError,KeyError,TypeError) as exc:
            say(chat, 'Full-video plan unavailable: '+str(exc)[:350])
        return
    if cmd=='/generate':
        if (((job.get('checkpoint') or {}).get('scenery_reference') or {}).get('status')
                in ('awaiting','pending','failed')):
            say(chat,'Scenery photo must finish archiving before paid generation. '
                'Check /status for Scenery image: ready, or use /scenery keep source scenery to clear it.')
            return
        if os.getenv('ENABLE_PAID_GENERATION','false').lower()!='true':
            say(chat,'Paid generation is disabled in staging. Your video can be archived without starting Wan 3.0.');return
        if job.get('character')!='NICOLE':
            say(chat,'Continuous mode is verified for Nicole. Choose /character Nicole for this generation.');return
        if job.get('character')=='NICOLE' and not (((job.get('checkpoint') or {}).get('identity') or {}).get('status')=='ready' or os.getenv('NICOLE_IDENTITY_URLS','').strip()):
            say(chat,'Nicole reference missing. Attach her photo with caption /identity before paid character replacement.');return
        if (job.get('voiceover_text') or '').strip():
            try:
                voiceover.voice_id_for(job['character'],job.get('voiceover_voice','AUTO'))
                voiceover.validate_text(job['voiceover_text'])
            except (ValueError,voiceover.VoiceConfigurationError) as exc:
                say(chat,'Voice setting: '+str(exc));return
        if (job.get('voiceover_text') or '').strip():
            say(chat,'This verified continuity mode preserves the original soundtrack. Set /voiceover off before /generate.');return
        updated=db.queue_job(job['job_id'],owner)
        scene_ready=(((job.get('checkpoint') or {}).get('scenery_reference') or {}).get('status')=='ready')
        message=('Generation queued: Image 1 Nicole identity and outfit, '
                 'Image 2 Nicole facial close-up, Image 3 uploaded scenery, '
                 'Video 1 choreography and camera timing only, on every segment. '
                 'Original soundtrack preserved. New paid Wan generation authorized; '
                 'use /status to track.'
                 if scene_ready else
                 'Generation queued with Nicole identity reset on every segment. '
                 'Saved source and original audio preserved. Use /status to track it.')
        say(chat,message if updated else 'Send a source MP4 to your draft first.');return
    if cmd=='/salvage':
        updated=db.queue_salvage(job['job_id'],owner)
        say(chat,('Local recovery queued: assembling ALL the already-paid clips without a new Wan generation. Original sound will be restored; existing AI face drift may remain. Use /status.' if updated else 'Only the saved completed-segments seam-review job can be recovered without new provider calls.'));return
    if cmd=='/resume':
        updated=db.resume(job['job_id'],owner)
        say(chat,'Failed stage queued for recovery. Completed segments will not be regenerated.' if updated else 'Only a known failed job can be resumed. An uncertain provider submission requires review to avoid duplicate charges.');return
    if cmd=='/cancel':
        updated=db.cancel(job['job_id'],owner)
        say(chat,'Cancelled. Existing provider requests may still incur charges; their saved outputs remain in storage.' if updated else 'No cancellable job.');return
    say(chat,'Use /help to see the commands.')


@router.post('/telegram/webhook')
async def webhook(request: Request,x_telegram_bot_api_secret_token: str|None=Header(None)):
    if not WEBHOOK_SECRET:
        raise HTTPException(503,'Webhook not configured')
    if x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(403,'Invalid webhook secret')
    data=await request.json()
    update=data.get('update_id')
    if not isinstance(update,int):
        raise HTTPException(400,'Bad Telegram update')
    if not db.updates_once(update):
        return {'ok':True,'duplicate':True}
    try:
        msg=data.get('message')
        if msg: process_message(msg)
    except Exception:
        logger.exception('Update processing failed; update_id=%s',update)
        # Remove the receipt so Telegram can retry rather than lose an update silently.
        db.release_update(update)
        raise HTTPException(500,'Webhook handler failed')
    return {'ok':True}
