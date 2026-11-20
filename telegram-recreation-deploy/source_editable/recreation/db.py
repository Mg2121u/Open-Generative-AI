"""Durable PostgreSQL job/step bookkeeping, with safe lease acquisition."""
import os
import uuid
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is required for persistent jobs')
    return psycopg.connect(url, row_factory=dict_row)


def initialize():
    from pathlib import Path
    sql = Path(__file__).resolve().parent.parent.joinpath('schema.sql').read_text()
    with connect() as conn:
        conn.execute(sql)


def create_draft(chat_id, owner_id):
    """Open a fresh draft, carrying forward only this owner's saved Nicole identity.

    No prior video, job outputs, scene, or payment approval is inherited.
    """
    job_id = uuid.uuid4()
    with connect() as conn:
        prev=conn.execute("""SELECT checkpoint->'identity' AS identity
            FROM recreation_jobs WHERE owner_id=%s AND character='NICOLE'
             AND checkpoint#>>'{identity,status}'='ready'
             AND checkpoint#>>'{identity,nicole_image_key}' IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1""",(owner_id,)).fetchone()
        checkpoint={'identity':prev['identity']} if prev else {}
        conn.execute('INSERT INTO recreation_jobs(job_id,chat_id,owner_id,checkpoint) VALUES (%s,%s,%s,%s)',
                     (job_id,chat_id,owner_id,Jsonb(checkpoint)))
    return str(job_id)



def create_redo_from_latest(chat_id, owner_id):
    """Create an UNBILLED quality-redo draft from this owner's latest finished video.

    Reuses the exact original media and archived Nicole identity. All previous
    outputs and provider steps remain immutable. Only a subsequent /generate
    permits new billable model work on the new job ID.
    """
    job_id = uuid.uuid4()
    with connect() as conn:
        previous = conn.execute("""SELECT job_id, character, model, source_file_id,
            original_key, scenery, outfit, instructions, voiceover_voice,
            checkpoint FROM recreation_jobs
            WHERE owner_id=%s AND state='COMPLETED'
              AND character='NICOLE' AND source_file_id IS NOT NULL
              AND original_key IS NOT NULL
              AND checkpoint#>>'{identity,status}'='ready'
              AND checkpoint#>>'{identity,nicole_image_key}' IS NOT NULL
              AND checkpoint->'scene_repair' IS NULL
            ORDER BY created_at DESC LIMIT 1""", (owner_id,)).fetchone()
        if not previous:
            return None
        cp = {'identity': previous['checkpoint']['identity']}
        scene=(previous.get('checkpoint') or {}).get('scenery_reference') or {}
        if scene.get('status')=='ready' and scene.get('scenery_image_key'):
            cp['scenery_reference']=scene
        prior_preflight=(previous['checkpoint'] or {}).get('local_preflight') or {}
        if (prior_preflight.get('complete')
                and prior_preflight.get('source_key') == previous['original_key']):
            cp['local_preflight']=prior_preflight
        row=conn.execute("""INSERT INTO recreation_jobs
            (job_id, chat_id, owner_id, character, model, source_file_id,
             original_key, scenery, outfit, instructions, voiceover_voice,
             checkpoint, stage)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UPLOAD_REFERENCE')
            RETURNING job_id""", (
            job_id, chat_id, owner_id, previous['character'],
            previous['model'], previous['source_file_id'],
            previous['original_key'], previous['scenery'], previous['outfit'],
            previous['instructions'], previous['voiceover_voice'], Jsonb(cp),
        )).fetchone()
    return {'job_id': str(row['job_id']), 'from_job': str(previous['job_id'])}



def completed_segment_job(owner_id, prefix=None):
    """Resolve a completed OWNED video, not a prior revision or somebody else's.

    The eight-character job prefix is display-only, never used as a dynamic
    table or storage identifier. The next stage verifies each archived clip.
    """
    import re
    if prefix is not None and not re.fullmatch(r'[0-9a-f]{8}',str(prefix).lower()):
        raise ValueError('Specify the 8-character job ID shown in /status')
    with connect() as conn:
        rows=conn.execute("""SELECT * FROM recreation_jobs
            WHERE owner_id=%s AND character='NICOLE' AND state='COMPLETED'
              AND stage='DONE' AND segment_total>0
              AND original_key IS NOT NULL
              AND checkpoint#>>'{identity,status}'='ready'
              AND checkpoint#>>'{local_preflight,source_key}'=original_key
              AND checkpoint->'segment_revision' IS NULL
              AND checkpoint->'scene_repair' IS NULL
              AND (%s::text IS NULL OR left(job_id::text,8)=%s::text)
            ORDER BY created_at DESC LIMIT 2""",
            (owner_id,prefix,prefix)).fetchall()
    if prefix and len(rows)>1:
        raise ValueError('The job ID prefix is ambiguous')
    return rows[0] if rows else None


def create_segment_revision(chat_id,owner_id,parent_prefix,section_number):
    """Unbilled draft for one original section; parent remains immutable."""
    import uuid as _uuid
    from . import quality,full_video
    original=completed_segment_job(owner_id,parent_prefix)
    if not original:
        raise ValueError('Completed original Nicole video not found for this owner')
    preflight=(original.get('checkpoint') or {}).get('local_preflight') or {}
    identity=(original.get('checkpoint') or {}).get('identity') or {}
    if not preflight.get('complete') or not identity.get('nicole_image_key'):
        raise ValueError('Original source or Nicole reference is missing')
    duration=(preflight.get('source_probe') or {}).get('duration')
    plan=quality.quality_plan(duration)
    if len(plan)!=int(original['segment_total']):
        raise ValueError('The completed video uses an incompatible segment plan; single-section replacement is blocked')
    if not 1<=int(section_number)<=len(plan):
        raise ValueError(f'Choose a section from 1 to {len(plan)}')
    # Verify the completed predictions from this exact run. Historical v2/v3
    # generations cannot be silently combined with the v4 output namespace.
    with connect() as conn:
        existing=conn.execute("""SELECT count(*) AS n FROM recreation_steps
            WHERE job_id=%s AND state='COMPLETED'
              AND step_key LIKE %s""",(
            original['job_id'],f'wan_{full_video.RUN_KEY}_segment_%')).fetchone()['n']
        if existing!=len(plan):
            raise ValueError('Only fully completed current-run sections may be replaced; older runs need a separate migration')
        rev_id=_uuid.uuid4()
        index=int(section_number)-1
        cp={
            'identity':identity,
            'local_preflight':preflight,
            'segment_revision':{
                'parent_job_id':str(original['job_id']),
                'parent_run_key':full_video.RUN_KEY,
                'section_index':index,
                'parent_sections':len(plan),
                'revision_version':1
            }
        }
        parent_scene=(original.get('checkpoint') or {}).get('scenery_reference') or {}
        if parent_scene.get('status')=='ready' and parent_scene.get('scenery_image_key'):
            cp['scenery_reference']=parent_scene
        conn.execute("""INSERT INTO recreation_jobs
            (job_id,chat_id,owner_id,character,model,source_file_id,
             original_key,scenery,outfit,instructions,voiceover_voice,
             checkpoint,stage,segment_total,current_segment)
            VALUES (%s,%s,%s,'NICOLE','wan-3.0',%s,%s,%s,%s,%s,%s,
                    %s,'SEGMENT_REDO',1,0)""",(
            rev_id,chat_id,owner_id,original['source_file_id'],
            original['original_key'],original['scenery'],original['outfit'],
            original['instructions'],original['voiceover_voice'],Jsonb(cp)))
    segment=plan[index]
    return {'job_id':str(rev_id),'parent_id':str(original['job_id']),
            'number':section_number,'total':len(plan),
            'start':segment['start'],'end':round(segment['start']+segment['duration'],2),
            'estimate':segment['estimated_usd']}


def queue_segment_revision(job_id,owner_id):
    """Only the owner's explicit /confirm_segment can queue a new paid call."""
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET state='QUEUED',next_attempt_at=now(),
                lease_until=NULL,lease_owner=NULL,attempt=0,last_error=NULL,
                updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT'
              AND stage='SEGMENT_REDO'
              AND checkpoint#>>'{segment_revision,parent_job_id}' IS NOT NULL
              AND COALESCE(checkpoint#>>'{scenery_reference,status}','ready')='ready'
            RETURNING job_id""",(job_id,owner_id)).fetchone()

def get_job(job_id, owner_id=None):
    with connect() as conn:
        return conn.execute('SELECT * FROM recreation_jobs WHERE job_id = %s AND (%s::bigint IS NULL OR owner_id = %s)',
                            (job_id, owner_id, owner_id)).fetchone()


def latest_job(owner_id):
    with connect() as conn:
        return conn.execute('SELECT * FROM recreation_jobs WHERE owner_id=%s ORDER BY created_at DESC LIMIT 1', (owner_id,)).fetchone()


def updates_once(update_id):
    with connect() as conn:
        return conn.execute('INSERT INTO recreation_updates(update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id',
                            (update_id,)).fetchone() is not None


def draft_update(job_id, owner_id, column, value):
    allowed = {'character','model','source_file_id','outfit','scenery','instructions','voiceover_text','voiceover_voice'}
    if column not in allowed:
        raise ValueError('Unknown draft field')
    with connect() as conn:
        if column == 'source_file_id':
            # Replacing the draft video invalidates any previous archive pointer.
            return conn.execute("""UPDATE recreation_jobs SET source_file_id=%s,original_key=NULL,checkpoint=jsonb_build_object('identity', COALESCE(checkpoint->'identity','{}'::jsonb), 'scenery_reference', COALESCE(checkpoint->'scenery_reference','{}'::jsonb)),outputs='[]'::jsonb,segment_total=0,current_segment=0,stage='UPLOAD',updated_at=now()
              WHERE job_id=%s AND owner_id=%s AND state='DRAFT' RETURNING *""",
                (value, job_id, owner_id)).fetchone()
        if column=='scenery':
            # Switching back to a text description disables the old photo.
            return conn.execute("""UPDATE recreation_jobs
                SET scenery=%s,checkpoint=COALESCE(checkpoint,'{}'::jsonb)-'scenery_reference',
                    updated_at=now()
                WHERE job_id=%s AND owner_id=%s AND state='DRAFT' RETURNING *""",
                (value,job_id,owner_id)).fetchone()
        return conn.execute(f'UPDATE recreation_jobs SET {column}=%s,updated_at=now() WHERE job_id=%s AND owner_id=%s AND state=%s RETURNING *',
                            (value, job_id, owner_id, 'DRAFT')).fetchone()




def await_scenery_image(job_id,owner_id):
    """User sent /scenery alone: arm the next photo without paid work."""
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(COALESCE(checkpoint,'{}'::jsonb),
                '{scenery_reference}',jsonb_build_object('status','awaiting'),true),
                scenery='use scenery reference image',updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT' RETURNING job_id""",
            (job_id,owner_id)).fetchone()


def record_scenery_image(job_id,owner_id,file_id,description=''):
    """Durably reserve private scene photo before downloading from Telegram."""
    if not file_id or len(file_id)>1024:
        raise ValueError('Invalid Telegram scenery image reference')
    description=' '.join(str(description or '').split())[:260]
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(COALESCE(checkpoint,'{}'::jsonb),
                '{scenery_reference}',jsonb_build_object(
                   'file_id',%s::text,'status','pending','description',%s::text),true),
                scenery='use scenery reference image',updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT'
            RETURNING job_id""",(file_id,description,job_id,owner_id)).fetchone()


def mark_scenery_ready(job_id,file_id,key):
    """A later photo supersedes an older pending download atomically."""
    with connect() as conn:
        row=conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(checkpoint,'{scenery_reference}',
                COALESCE(checkpoint->'scenery_reference','{}'::jsonb)
                || jsonb_build_object('status','ready','scenery_image_key',%s::text),
                true),
                updated_at=now()
            WHERE job_id=%s AND state='DRAFT'
              AND checkpoint#>>'{scenery_reference,status}'='pending'
              AND checkpoint#>>'{scenery_reference,file_id}'=%s
            RETURNING job_id""",(key,job_id,file_id)).fetchone()
        if row:
            conn.execute("""INSERT INTO recreation_events(job_id,kind,description)
                 VALUES (%s,'scenery_saved','Private scenery reference image archived (not a person identity source)')""",
                 (job_id,))
        return row


def mark_scenery_failed(job_id,file_id):
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(checkpoint,'{scenery_reference,status}',
                '"failed"'::jsonb,true),updated_at=now()
            WHERE job_id=%s AND state='DRAFT'
              AND checkpoint#>>'{scenery_reference,status}'='pending'
              AND checkpoint#>>'{scenery_reference,file_id}'=%s
            RETURNING job_id""",(job_id,file_id)).fetchone()

def record_identity_image(job_id, owner_id, file_id):
    """Persist a pending Nicole reference before doing any network I/O."""
    if not file_id or len(file_id) > 1024:
        raise ValueError('Invalid Telegram identity file reference')
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(COALESCE(checkpoint,'{}'::jsonb),'{identity}',
                jsonb_build_object('nicole_file_id',%s::text,'status','pending'),true),
                updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND character='NICOLE' AND state='DRAFT'
            RETURNING job_id""", (file_id,job_id,owner_id)).fetchone()


def mark_identity_ready(job_id, file_id, key):
    """Complete only the exact pending identity file, avoiding stale process races."""
    with connect() as conn:
        row=conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(checkpoint,'{identity}',
                jsonb_build_object('nicole_file_id',%s::text,'nicole_image_key',%s::text,'status','ready'),true),
                updated_at=now()
            WHERE job_id=%s AND state='DRAFT' AND character='NICOLE'
              AND checkpoint#>>'{identity,nicole_file_id}'=%s
            RETURNING job_id""",(file_id,key,job_id,file_id)).fetchone()
        if row:
            conn.execute("""INSERT INTO recreation_events(job_id,kind,description)
                VALUES (%s,'identity_saved','Nicole reference image archived privately for identity guidance')""",(job_id,))
        return row


def mark_identity_failed(job_id,file_id):
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET checkpoint=jsonb_set(checkpoint,'{identity,status}','"failed"'::jsonb,true),updated_at=now()
            WHERE job_id=%s AND state='DRAFT' AND checkpoint#>>'{identity,nicole_file_id}'=%s
            RETURNING job_id""",(job_id,file_id)).fetchone()

def queue_job(job_id, owner_id):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_jobs SET state='QUEUED',stage=CASE WHEN original_key IS NULL THEN 'INGEST' ELSE 'UPLOAD_REFERENCE' END,next_attempt_at=now(),updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT' AND source_file_id IS NOT NULL
              AND COALESCE(checkpoint#>>'{scenery_reference,status}','ready')='ready'
              RETURNING *''',
            (job_id,owner_id)).fetchone()


def resume(job_id, owner_id):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_jobs SET state='QUEUED',attempt=0,next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='FAILED' RETURNING *''', (job_id,owner_id)).fetchone()




def qa_review_job(owner_id, job_prefix):
    """Only this owner's explicit short job ID, and only a saved QA mismatch."""
    import re
    if not re.fullmatch(r'[0-9a-f]{8}',str(job_prefix).lower()):
        raise ValueError('Use an eight-character job ID, for example 59bc3ef4')
    with connect() as conn:
        rows=conn.execute("""SELECT * FROM recreation_jobs
            WHERE owner_id=%s AND left(job_id::text,8)=%s
              AND state='NEEDS_REVIEW' AND stage='AUTO_GENERATE'
              AND character='NICOLE' AND segment_total>=1
              AND (last_error LIKE 'Nicole facial identity QA mismatch:%%'
                   OR last_error LIKE 'Nicole facial identity QA unverified:%%')
            LIMIT 2""",(owner_id,job_prefix.lower())).fetchall()
    if len(rows)>1:
        raise ValueError('Job ID prefix is not unique')
    return rows[0] if rows else None



def auto_provider_steps(job_id):
    """All saved Wan requests for the *current* run; preserve their IDs."""
    from .full_video import RUN_KEY
    with connect() as conn:
        return conn.execute("""SELECT step_key,state,external_request_id
            FROM recreation_steps WHERE job_id=%s AND step_key LIKE %s
            ORDER BY step_key""",
            (job_id,f'wan_{RUN_KEY}_segment_%')).fetchall()


def approve_qa_continue(job_id,owner_id,*,index,expected_step,expected_key,qa_status):
    """Owner accepts one archived failed-face clip and resumes remaining sections.

    Absolutely no step reset and no re-submission of paid original segments.
    """
    from .full_video import RUN_KEY,segment_key,step_key
    if not isinstance(index,int) or not 0<=index<2400:
        raise ValueError('Invalid section index')
    if (expected_step!=step_key(index) or
            expected_key!=segment_key(str(job_id),index) or
            qa_status not in ('mismatch','unverified')):
        raise ValueError('Approval is restricted to one exact archived paid clip')
    pattern=f'wan_{RUN_KEY}_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
          SET state='QUEUED',stage='AUTO_GENERATE',attempt=0,
              next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,
              last_error=NULL,
              checkpoint=jsonb_set(COALESCE(j.checkpoint,'{}'::jsonb),
                  '{face_qa_approvals}',
                  COALESCE(j.checkpoint->'face_qa_approvals','{}'::jsonb)
                   || jsonb_build_object(%s::text,
                        jsonb_build_object(
                          'type','owner_accepted_facial_mismatch_for_continuation',
                          'owner_id',%s::bigint,
                          'segment_key',%s::text,'source_step',%s::text,
                          'accepted_status',%s::text,
                          'original_failure',j.last_error,
                          'approved_at',to_char(now() AT TIME ZONE 'UTC',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"'))),
                  true),
              updated_at=now()
          WHERE j.job_id=%s AND j.owner_id=%s
            AND j.state='NEEDS_REVIEW' AND j.stage='AUTO_GENERATE'
            AND j.character='NICOLE' AND j.segment_total>1
            AND j.segment_total>%s AND j.original_key IS NOT NULL
            AND j.checkpoint#>>'{identity,status}'='ready'
            AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
            AND j.last_error LIKE %s
            AND EXISTS (SELECT 1 FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key=%s
                   AND s.state='COMPLETED'
                   AND NULLIF(s.external_request_id,'') IS NOT NULL)
            AND (SELECT COUNT(*) FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key LIKE %s)=%s
            AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                   AND s.state!='COMPLETED')
            RETURNING j.*""",
            (str(index),owner_id,expected_key,expected_step,qa_status,
             job_id,owner_id,index,
             'Nicole facial identity QA '+qa_status+':%',
             expected_step,pattern,index+1,pattern)).fetchone()


def approve_qa_review(job_id, owner_id, *, expected_step):
    """Atomic explicit owner authorization to DELIVER ONE ALREADY PAID clip.

    No step is reset, no new provider prediction is reserved. All other QA
    failures, pending calls and incomplete jobs stay blocked.
    """
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
            SET state='QUEUED',stage='QA_OWNER_APPROVED',attempt=0,
                next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,
                checkpoint=jsonb_set(COALESCE(j.checkpoint,'{}'::jsonb),
                    '{quality_override}',
                    jsonb_build_object('type','owner_accepted_facial_mismatch',
                        'owner_id',%s::bigint,
                        'approved_at',to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                        'original_failure',j.last_error,
                        'source_step',%s::text),
                    true),
                updated_at=now()
            WHERE j.job_id=%s AND j.owner_id=%s AND j.state='NEEDS_REVIEW'
              AND j.stage='AUTO_GENERATE' AND j.character='NICOLE'
              AND j.segment_total=1
              AND j.last_error LIKE 'Nicole facial identity QA mismatch:%%'
              AND j.original_key IS NOT NULL
              AND j.checkpoint#>>'{identity,status}'='ready'
              AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
              AND EXISTS (SELECT 1 FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key=%s AND s.state='COMPLETED')
              AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.state IN
                 ('SUBMITTING','SUBMITTED','POLLING','UNCERTAIN'))
            RETURNING j.*""",
            (owner_id,expected_step,job_id,owner_id,expected_step)).fetchone()



def auto_recover_finished_uploads():
    """One-time local-only recovery of failed oversized final S3 uploads.

    Recovery occurs only AFTER all paid model predictions are complete.
    The checkpoint prevents indefinite reassembly on an undersized bucket.
    It never clears provider steps or issues new external predictions.
    """
    from .full_video import RUN_KEY
    owners=[int(x) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',')
            if x.strip().isdigit()]
    if not owners:return []
    pattern=f'wan_{RUN_KEY}_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
            SET stage='FINAL_ASSEMBLE',state='QUEUED',attempt=0,
                next_attempt_at=now(),lease_owner=NULL,lease_until=NULL,
                last_error=NULL,
                checkpoint=jsonb_set(COALESCE(j.checkpoint,'{}'::jsonb),
                  '{final_recovery}',
                  jsonb_build_object('auto_attempted',true,
                    'reason','S3UploadFailedError',
                    'queued_at',to_char(now() AT TIME ZONE 'UTC',
                         'YYYY-MM-DD"T"HH24:MI:SS"Z"')),true),
                updated_at=now()
            WHERE j.owner_id=ANY(%s::bigint[]) AND j.character='NICOLE'
              AND j.stage='AUTO_GENERATE'
              AND j.state IN ('FAILED','NEEDS_REVIEW','QUEUED','WAITING_PROVIDER')
              AND j.lease_owner IS NULL
              AND (j.lease_until IS NULL OR j.lease_until<now())
              AND (j.last_error LIKE 'Processing stage failed: S3UploadFailedError%%'
                   OR j.last_error LIKE 'Final MP4%%'
                   OR j.checkpoint#>>'{final_recovery,owner_requested}'='true')
              AND COALESCE(j.checkpoint#>>'{final_recovery,auto_attempted}','false')!='true'
              AND j.segment_total>0 AND j.original_key IS NOT NULL
              AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
              AND j.checkpoint#>>'{identity,status}'='ready'
              AND (SELECT count(*) FROM recreation_steps s
                   WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                     AND s.state='COMPLETED'
                     AND NULLIF(s.external_request_id,'') IS NOT NULL)=j.segment_total
              AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                   WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                     AND s.state!='COMPLETED')
            RETURNING j.job_id,j.segment_total""",
            (owners,pattern,pattern)).fetchall()


def defer_final_assembly(owner_id,job_prefix):
    """Remember /finish when the final assembled video is already in progress.

    DO NOT change a running job's lease, stage, state, or paid steps.
    A follow-on watchdog will switch to local-only recovery if the active
    assembly fails; successful completion needs no extra work.
    """
    import re
    from .full_video import RUN_KEY
    prefix=str(job_prefix or '').lower()
    if not re.fullmatch(r'[0-9a-f]{8}',prefix):
        raise ValueError('Use /finish followed by an eight-character job ID')
    pattern=f'wan_{RUN_KEY}_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
            SET checkpoint=jsonb_set(COALESCE(j.checkpoint,'{}'::jsonb),
                '{final_recovery}',
                COALESCE(j.checkpoint->'final_recovery','{}'::jsonb)
                   || jsonb_build_object('owner_requested',true,
                       'requested_at',to_char(now() AT TIME ZONE 'UTC',
                                            'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
                true),updated_at=now()
            WHERE j.owner_id=%s AND left(j.job_id::text,8)=%s
              AND j.character='NICOLE' AND j.stage='AUTO_GENERATE'
              AND j.state='RUNNING'
              AND j.lease_owner IS NOT NULL AND j.lease_until>now()
              AND j.segment_total>0 AND j.original_key IS NOT NULL
              AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
              AND j.checkpoint#>>'{identity,status}'='ready'
              AND (SELECT count(*) FROM recreation_steps s
                   WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                     AND s.state='COMPLETED'
                     AND NULLIF(s.external_request_id,'') IS NOT NULL)=j.segment_total
              AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                   WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                     AND s.state!='COMPLETED')
            RETURNING j.job_id,j.segment_total,j.stage,j.state""",
            (owner_id,prefix,pattern,pattern)).fetchone()


def queue_archive_delivery(owner_id,job_prefix):
    """Owner explicitly authorizes ONE multipart send of an already archived MP4.

    Prior sendVideo-by-URL can have an uncertain result; we neither reset
    its reservation nor assume that Telegram did not deliver it.
    The new multipart attempt receives its own one-shot idempotency key.
    """
    import re
    from .full_video import RUN_KEY
    prefix=str(job_prefix or '').lower()
    if not re.fullmatch(r'[0-9a-f]{8}',prefix):
        raise ValueError('Specify the 8-character job identifier from /status')
    original=f'{RUN_KEY}_telegram_delivery'
    retry=f'{RUN_KEY}_telegram_archive_multipart_v1'
    pattern=f'wan_{RUN_KEY}_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
            SET stage='ARCHIVE_DELIVER',state='QUEUED',attempt=0,
                next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,
                last_error=NULL,updated_at=now()
            WHERE j.owner_id=%s AND left(j.job_id::text,8)=%s
              AND j.character='NICOLE'
              AND j.stage='AUTO_GENERATE' AND j.state='NEEDS_REVIEW'
              AND (j.lease_until IS NULL OR j.lease_until<now())
              AND j.last_error='Final file archived, but Telegram delivery needs manual inspection'
              AND j.segment_total>0 AND j.original_key IS NOT NULL
              AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
              AND j.checkpoint#>>'{identity,status}'='ready'
              AND EXISTS (SELECT 1 FROM recreation_steps t
                  WHERE t.job_id=j.job_id AND t.step_key=%s
                    AND t.state IN ('SUBMITTING','UNCERTAIN','FAILED'))
              AND NOT EXISTS (SELECT 1 FROM recreation_steps t
                  WHERE t.job_id=j.job_id AND t.step_key=%s)
              AND (SELECT count(*) FROM recreation_steps s
                  WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                    AND s.state='COMPLETED'
                    AND NULLIF(s.external_request_id,'') IS NOT NULL)=j.segment_total
              AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                  WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                    AND s.state!='COMPLETED')
            RETURNING j.*""",
            (owner_id,prefix,original,retry,pattern,pattern)).fetchone()


def queue_final_assembly(owner_id, job_prefix):
    """Owner-only local recovery after all paid Wan sections completed.

    This stage has no generation path, and does not alter ANY provider
    request or user-approved face-QA exceptions. An active worker lease
    always wins to avoid simultaneous video-assembly/delivery attempts.
    """
    import re
    from .full_video import RUN_KEY
    prefix=str(job_prefix or '').lower()
    if not re.fullmatch(r'[0-9a-f]{8}',prefix):
        raise ValueError('Use /finish and the eight-character job ID from /status')
    pattern=f'wan_{RUN_KEY}_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
          SET stage='FINAL_ASSEMBLE',state='QUEUED',attempt=0,
              next_attempt_at=now(),lease_owner=NULL,lease_until=NULL,
              last_error=NULL,updated_at=now()
          WHERE j.owner_id=%s AND left(j.job_id::text,8)=%s
            AND j.character='NICOLE' AND j.stage='AUTO_GENERATE'
            AND j.state IN ('FAILED','NEEDS_REVIEW','RUNNING','QUEUED','WAITING_PROVIDER')
            AND (j.lease_until IS NULL OR j.lease_until<now())
            AND (j.last_error LIKE 'Processing stage failed: S3UploadFailedError%%'
                 OR j.last_error LIKE 'Final MP4%%')
            AND j.segment_total>0 AND j.original_key IS NOT NULL
            AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
            AND j.checkpoint#>>'{identity,status}'='ready'
            AND (SELECT count(*) FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                   AND s.state='COMPLETED'
                   AND NULLIF(s.external_request_id,'') IS NOT NULL)=j.segment_total
            AND NOT EXISTS (SELECT 1 FROM recreation_steps s
                 WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                   AND s.state!='COMPLETED')
            RETURNING j.*""",
          (owner_id,prefix,pattern,pattern)).fetchone()


def queue_salvage(job_id, owner_id):
    """Owner-approved local-only assembly for the known v3 seam review.

    All previous provider predictions must already be COMPLETED. Nothing is
    submitted, cleared, cloned or charged by this command.
    """
    prefix='wan_telegram_recreation_cleangraphics_v3_20261115_segment_%'
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs AS j
            SET state='QUEUED',stage='SALVAGE',attempt=0,
                next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,
                last_error=NULL,updated_at=now()
            WHERE j.job_id=%s AND j.owner_id=%s
              AND j.state='NEEDS_REVIEW' AND j.stage='AUTO_GENERATE'
              AND j.last_error LIKE 'Adjacent generated poses/scenes differ too much.%%'
              AND j.original_key IS NOT NULL
              AND j.checkpoint#>>'{local_preflight,source_key}'=j.original_key
              AND j.segment_total > 0
              AND j.segment_total=(
                  SELECT count(*) FROM recreation_steps s
                  WHERE s.job_id=j.job_id AND s.step_key LIKE %s
                    AND s.state='COMPLETED')
              AND NOT EXISTS (
                  SELECT 1 FROM recreation_steps s
                  WHERE s.job_id=j.job_id
                    AND s.state IN ('SUBMITTING','SUBMITTED','POLLING','UNCERTAIN'))
            RETURNING j.*""",(job_id,owner_id,prefix)).fetchone()

def cancel(job_id, owner_id):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_jobs SET state='CANCELLED',updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state IN ('DRAFT','QUEUED','RUNNING','WAITING_PROVIDER','FAILED','NEEDS_REVIEW') RETURNING *''',
            (job_id,owner_id)).fetchone()


def claim(worker, seconds=90):
    """Leases can expire after a crash. Lost provider submissions are never blindly repeated."""
    with connect() as conn:
        return conn.execute('''WITH next AS (
          SELECT job_id FROM recreation_jobs
          WHERE state IN ('QUEUED','RUNNING','WAITING_PROVIDER')
            AND next_attempt_at<=now()
            AND (lease_until IS NULL OR lease_until < now())
          ORDER BY next_attempt_at,created_at
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE recreation_jobs j SET lease_owner=%s,lease_until=now()+make_interval(secs=>%s),
          state=CASE WHEN j.state='QUEUED' THEN 'RUNNING' ELSE j.state END,updated_at=now()
        FROM next WHERE j.job_id=next.job_id RETURNING j.*''', (worker,seconds)).fetchone()


def checkpoint(job_id, worker, *, stage=None, state=None, fields=None, delay_seconds=0, event=None):
    fields = fields or {}
    safe_columns = {'source_file_id','original_key','current_segment','segment_total','outputs','checkpoint','last_error','attempt'}
    if set(fields) - safe_columns:
        raise ValueError('Unexpected checkpoint fields')
    assignments = ['updated_at=now()','lease_until=NULL','lease_owner=NULL',
                   'next_attempt_at=now()+make_interval(secs=>%s)']
    values = [delay_seconds]
    if stage is not None:
        assignments.append('stage=%s');values.append(stage)
    if state is not None:
        assignments.append('state=%s');values.append(state)
    for col,value in fields.items():
        assignments.append(f'{col}=%s')
        values.append(Jsonb(value) if col in {'checkpoint','outputs'} else value)
    values.extend((job_id,worker))
    with connect() as conn:
        row = conn.execute('UPDATE recreation_jobs SET '+','.join(assignments)+" WHERE job_id=%s AND lease_owner=%s AND state!='CANCELLED' RETURNING *",values).fetchone()
        if event and row:
            conn.execute('INSERT INTO recreation_events(job_id,kind,description) VALUES (%s,%s,%s)',
                         (job_id,event[0],event[1]))
        return row


def prepare_external(job_id, step_key, provider, payload):
    """Atomically reserve a one-shot external request BEFORE contacting a provider.

    ON CONFLICT DO NOTHING is essential: two workers can both observe that no
    row exists. Only one may acquire the reservation, so only one may POST.
    A failed or ambiguous submission must be reconciled manually, not reset.
    """
    with connect() as conn:
        return conn.execute('''INSERT INTO recreation_steps(job_id,step_key,state,provider,input,attempts)
          VALUES (%s,%s,'SUBMITTING',%s,%s,1)
          ON CONFLICT(job_id,step_key) DO NOTHING
          RETURNING *''',(job_id,step_key,provider,Jsonb(payload))).fetchone()


def reserve_one_shot(job_id, step_key, provider, payload):
    """Atomic reservation for synchronous, billable APIs such as ElevenLabs.

    Returns False if any previous attempt exists, preventing two workers from
    submitting the same TTS request during a concurrent lease handover.
    """
    with connect() as conn:
        return conn.execute("""INSERT INTO recreation_steps(job_id,step_key,state,provider,input,attempts)
            VALUES (%s,%s,'SUBMITTING',%s,%s,1)
            ON CONFLICT(job_id,step_key) DO NOTHING RETURNING step_key""",
            (job_id,step_key,provider,Jsonb(payload))).fetchone() is not None


def external_submitted(job_id,step_key,external_id):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_steps SET state='SUBMITTED',external_request_id=%s,updated_at=now()
            WHERE job_id=%s AND step_key=%s AND state='SUBMITTING' RETURNING *''',
            (external_id,job_id,step_key)).fetchone()


def external_finished(job_id,step_key,result):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_steps SET state='COMPLETED',result=%s,updated_at=now()
            WHERE job_id=%s AND step_key=%s RETURNING *''',
            (Jsonb(result),job_id,step_key)).fetchone()


def get_step(job_id,step_key):
    with connect() as conn:
        return conn.execute('SELECT * FROM recreation_steps WHERE job_id=%s AND step_key=%s',(job_id,step_key)).fetchone()


def uncertain_old_submissions(age_seconds=120):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_steps SET state='UNCERTAIN',updated_at=now()
            WHERE state='SUBMITTING' AND created_at<now()-make_interval(secs=>%s)
            RETURNING job_id,step_key''',(age_seconds,)).fetchall()


def heartbeat(job_id, worker, seconds=90):
    with connect() as conn:
        return conn.execute("UPDATE recreation_jobs SET lease_until=now()+make_interval(secs=>%s) WHERE job_id=%s AND lease_owner=%s AND state NOT IN ('CANCELLED','COMPLETED') RETURNING job_id",
            (seconds,job_id,worker)).fetchone()


def review_uncertain_submissions(age_seconds=180):
    """Watchdog: mark ambiguous paid requests for manual reconciliation, never resubmit."""
    with connect() as conn:
        rows=conn.execute('''UPDATE recreation_steps SET state='UNCERTAIN',updated_at=now()
            WHERE state='SUBMITTING' AND updated_at < now()-make_interval(secs=>%s)
            RETURNING job_id,step_key''',(age_seconds,)).fetchall()
        for row in rows:
            conn.execute('''UPDATE recreation_jobs SET state='NEEDS_REVIEW',last_error=%s,lease_until=NULL,lease_owner=NULL,updated_at=now()
                WHERE job_id=%s AND state NOT IN ('CANCELLED','COMPLETED')''',
                ('Uncertain provider submission '+row['step_key']+'; check provider dashboard to avoid duplicate billing',row['job_id']))
        return rows


def release_update(update_id):
    with connect() as conn:
        conn.execute('DELETE FROM recreation_updates WHERE update_id=%s',(update_id,))


def set_segment_total(job_id, total):
    if not (1 <= total <= 120):
        raise ValueError('Invalid segment count')
    with connect() as conn:
        return conn.execute('UPDATE recreation_jobs SET segment_total=%s,updated_at=now() WHERE job_id=%s RETURNING job_id',
                            (total,job_id)).fetchone()


def queue_local_preflight(job_id, owner_id):
    """Only an archived owner draft may request non-paid FFmpeg diagnostics."""
    with connect() as conn:
        return conn.execute("""UPDATE recreation_jobs
            SET state='QUEUED',stage='LOCAL_PREFLIGHT',next_attempt_at=now(),
                lease_owner=NULL,lease_until=NULL,attempt=0,last_error=NULL,updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT'
              AND original_key IS NOT NULL RETURNING *""",
            (job_id,owner_id)).fetchone()

def claim_local_preflight(worker, seconds=180):
    """Safest possible lease: only local FFmpeg stages, never paid providers."""
    owner_ids=[int(x.strip()) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()]
    if not owner_ids:
        raise RuntimeError('Worker owner allowlist is required')
    with connect() as conn:
        return conn.execute("""WITH selected AS (
           SELECT job_id FROM recreation_jobs
           WHERE stage='LOCAL_PREFLIGHT'
             AND owner_id=ANY(%s::bigint[])
             AND state IN ('QUEUED','RUNNING')
             AND next_attempt_at<=now()
             AND (lease_until IS NULL OR lease_until < now())
           ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE recreation_jobs j SET lease_owner=%s,
           lease_until=now()+make_interval(secs=>%s),
           state='RUNNING',updated_at=now()
        FROM selected WHERE j.job_id=selected.job_id RETURNING j.*""",
        (owner_ids,worker,seconds)).fetchone()


def claim_app(worker,seconds=240):
    """Atomic queue lease, restricted to owner IDs and the always-on app stages.

    Never claim Nicole's production work or the older one-off test drafts.
    """
    owners=[int(x) for x in os.getenv('ALLOWED_TELEGRAM_USER_IDS','').split(',') if x.strip().isdigit()]
    if not owners:
        raise RuntimeError('Owner allowlist required for paid app queue')
    with connect() as conn:
        return conn.execute("""WITH selected AS (
            SELECT job_id FROM recreation_jobs WHERE owner_id=ANY(%s::bigint[])
            AND stage IN ('INGEST','UPLOAD_REFERENCE','AUTO_PREFLIGHT','AUTO_GENERATE','LOCAL_PREFLIGHT','SALVAGE','SEGMENT_REDO','QA_OWNER_APPROVED','FINAL_ASSEMBLE','ARCHIVE_DELIVER')
            AND state IN ('QUEUED','RUNNING','WAITING_PROVIDER')
            AND next_attempt_at<=now()
            AND (lease_until IS NULL OR lease_until<now())
            ORDER BY next_attempt_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE recreation_jobs j SET lease_owner=%s,
            lease_until=now()+make_interval(secs=>%s),
            state='RUNNING',updated_at=now() FROM selected
            WHERE j.job_id=selected.job_id RETURNING j.*""",(owners,worker,seconds)).fetchone()


def count_auto_segments(job_id):
    from .full_video import RUN_KEY
    with connect() as conn:
        return conn.execute("""SELECT count(*) AS n FROM recreation_steps
          WHERE job_id=%s AND step_key LIKE %s AND state='COMPLETED'""",
          (job_id,f'wan_{RUN_KEY}_segment_%')).fetchone()['n']
