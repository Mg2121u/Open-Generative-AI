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
    if job['stage'] in ('AUTO_GENERATE','DONE'):
        from .full_video import RUN_KEY
        with db.connect() as conn:
            saved=conn.execute('SELECT count(*) AS n FROM recreation_steps WHERE job_id=%s AND step_key LIKE %s AND state=%s',
                               (job['job_id'],f'wan_{RUN_KEY}_segment_%','COMPLETED')).fetchone()['n']
    return (f'Job {str(job["job_id"])[:8]} · {job["state"]}\n'
        f'Stage: {job["stage"]}\n'
        f'Character: {job["character"]} | Model: {job["model"]} | 480p\n'
        f'Quality: {"identity-lock / no warping" if os.getenv("RECREATION_QUALITY_MODE","").lower()=="identity_lock" else "standard"}\n'
        f'Scenery: {job["scenery"]}\nClothes: {job["outfit"]}\nVoice: ElevenLabs v4 · {job.get("voiceover_voice", "AUTO")} · {"on" if job.get("voiceover_text") else "off (original audio)"}\n'
        f'Source archive: {"saved" if job.get("original_key") else "not yet stored"}\n'
        f'Nicole identity: {((job.get("checkpoint") or {}).get("identity") or {}).get("status", "missing")}\n'
        f'Generated sections: {saved} / {job["segment_total"] or "pending plan"}\n'
        f'Local FFmpeg preview: {"complete" if (job.get("checkpoint") or {}).get("local_preflight",{}).get("complete") else "not completed"}\n'
        + (f'Issue: {job["last_error"][:400]}' if job['last_error'] else ''))


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
        say(chat,'Video Recreation Studio (continuous mode)\n/id — show your Telegram ID\n/new — new job\n/redo — new quality redo from latest completed video (no upload)\nSend an MP4 video or video document\n/character Nicole | Aria | Aria_Nicole\n/scenery <description>\n/clothes <description>\n/instructions <extra requirements>\n/model wan-3.0 (original reference pipeline)\n/voice Nicole | Aria | Auto — selected voice\n/voiceover <text> — optional spoken overlay\n/voiceover off — original audio only\n/status — saved progress\n/archive — save original video in private storage (no AI calls)\n/preflight — trim and merge a short source preview locally (no AI calls)\n/fullplan — full source duration, segments and estimated cost (no AI calls)\n/identity — attach a clear Nicole reference photo with caption /identity, or reply /identity to a photo\n/generate — start paid quality run\n/salvage — assemble already-paid segments (no new Wan calls)\n/resume — retry a failed stage\n/cancel — cancel job\n\n480p default. Source movement and original audio are preserved as targets.')
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
    job=db.latest_job(owner)
    # Sending a video is the ordinary way to begin another recreation. If the
    # previous job has already started/completed, create a fresh draft and carry
    # forward only this owner's saved Nicole identity image. No AI is charged.
    incoming_video=message.get('video') or (message.get('document') if
        (message.get('document') or {}).get('mime_type','').lower() in ('video/mp4','application/octet-stream') else None)
    if incoming_video and (not job or job.get('state')!='DRAFT'):
        fresh=db.create_draft(chat,owner)
        job=db.get_job(fresh,owner)
    if cmd=='/status': say(chat,show(job));return
    if not job:
        say(chat,'Send /new first.');return
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
        say(chat,'Generation queued with Nicole identity reset on every segment. Saved source and original audio preserved. Use /status to track it.' if updated else 'Send a source MP4 to your draft first.');return
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
