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
            ORDER BY created_at DESC LIMIT 1""", (owner_id,)).fetchone()
        if not previous:
            return None
        cp = {'identity': previous['checkpoint']['identity']}
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
            return conn.execute("""UPDATE recreation_jobs SET source_file_id=%s,original_key=NULL,checkpoint=jsonb_build_object('identity', COALESCE(checkpoint->'identity','{}'::jsonb)),outputs='[]'::jsonb,segment_total=0,current_segment=0,stage='UPLOAD',updated_at=now()
              WHERE job_id=%s AND owner_id=%s AND state='DRAFT' RETURNING *""",
                (value, job_id, owner_id)).fetchone()
        return conn.execute(f'UPDATE recreation_jobs SET {column}=%s,updated_at=now() WHERE job_id=%s AND owner_id=%s AND state=%s RETURNING *',
                            (value, job_id, owner_id, 'DRAFT')).fetchone()



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
            WHERE job_id=%s AND owner_id=%s AND state='DRAFT' AND source_file_id IS NOT NULL RETURNING *''',
            (job_id,owner_id)).fetchone()


def resume(job_id, owner_id):
    with connect() as conn:
        return conn.execute('''UPDATE recreation_jobs SET state='QUEUED',attempt=0,next_attempt_at=now(),lease_until=NULL,lease_owner=NULL,updated_at=now()
            WHERE job_id=%s AND owner_id=%s AND state='FAILED' RETURNING *''', (job_id,owner_id)).fetchone()



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
            AND stage IN ('INGEST','UPLOAD_REFERENCE','AUTO_PREFLIGHT','AUTO_GENERATE','LOCAL_PREFLIGHT','SALVAGE')
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
