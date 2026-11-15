"""Targeted staging-only fix for unlimited estimated spending and optional ElevenLabs.

The source archive is immutable. Apply this patch AFTER extracting the archive
and BEFORE the build tests. No credentials, provider calls or DB writes.
"""
from pathlib import Path

ROOT = Path("/app/recreation")
needle = "if not math.isfinite(cap) or not (0 < cap <= 100):"
replacement = "if math.isnan(cap) or cap <= 0:"

for file_name in ("app_queue.py", "full_video.py"):
    path = ROOT / file_name
    before = path.read_text()
    assert before.count(needle) == 1, f"Unexpected {file_name} cap guard"
    after = before.replace(
        needle,
        replacement,
        1,
    )
    path.write_text(after)
    print(f"RECREATION_CAP_FIX applied file={file_name} inf_allowed=True", flush=True)

path = ROOT / "full_video.py"
source = path.read_text()
start = source.index("def _guard_auto(")
end = source.index("def run_auto(", start)
block = source[start:end]
voice_guard = (
    "    if os.getenv('ENABLE_PAID_VOICE_GENERATION','false').lower()=='true':\n"
    "        raise RuntimeError('Original-audio mode must not generate paid voice')\n"
)
assert block.count(voice_guard) == 1, "Unexpected auto-guard voice gate"
block = block.replace(
    voice_guard,
    "    # An available ElevenLabs account must not block recreation of a\n"
    "    # source with original-audio mode selected. This pipeline never\n"
    "    # calls TTS; the final remux preserves the original soundtrack.\n",
    1,
)
path.write_text(source[:start] + block + source[end:])
print("RECREATION_VOICE_FIX available_api_does_not_block_original_audio=True", flush=True)

# The app must NEVER accidentally reuse older v2 paid segments or a first-canary
# image generated before its current scenery/outfit settings. Retain old outputs,
# but namespace all auto-app renders with a separate opt-in RUN_KEY.
path = ROOT / "full_video.py"
source = path.read_text()
old_key = "RUN_KEY = 'nicole_full_480p_continuity_v2_20261115'"
new_key = "RUN_KEY = os.getenv('RECREATION_RUN_KEY', 'nicole_full_480p_continuity_v2_20261115')"
assert source.count(old_key) == 1, "Unexpected historic run key"
source = source.replace(old_key, new_key, 1)
first_canary = "def _reusable_canary(job):"
assert source.count(first_canary) == 1, "Unexpected first-canary reuse function"
source = source.replace(
    first_canary,
    first_canary + "\n    # Fresh app runs must honor the current scenery/outfit, not an old canary.\n"
    "    if os.getenv('RECREATION_RUN_KEY'):\n"
    "        return False\n",
    1,
)
path.write_text(source)
print("RECREATION_RUN_ISOLATION new_run_namespace_supported=True first_canary_reuse_disabled=True", flush=True)

# Owner-driven recovery for an explicit failed config-only preflight.
# Never automatically resume unknown NEEDS_REVIEW states, or any unsettled
# provider request. Preserve source/identity/media and completed old outputs.
path = ROOT / "db.py"
source = path.read_text()
start = source.index("def resume(")
end = source.index("def cancel(", start)
block = source[start:end]
anchor = "WHERE job_id=%s AND owner_id=%s AND state='FAILED' RETURNING *"
replacement = (
    "WHERE job_id=%s AND owner_id=%s AND (state='FAILED' OR ("
    "state='NEEDS_REVIEW' AND stage='AUTO_PREFLIGHT' "
    "AND last_error='Per-video spending cap configuration invalid' "
    "AND original_key IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM recreation_steps rs "
    "WHERE rs.job_id=recreation_jobs.job_id "
    "AND rs.state IN ('SUBMITTING','SUBMITTED','UNCERTAIN'))"
    ")) RETURNING *"
)
assert block.count(anchor) == 1, "Unexpected resume SQL guard"
block = block.replace(anchor, replacement, 1)
block = block.replace(
    "lease_owner=NULL,updated_at=now()",
    "lease_owner=NULL,last_error=NULL,updated_at=now()",
    1,
)
path.write_text(source[:start] + block + source[end:])
print("RECREATION_RECOVERY explicit_review_resume_enabled=True unsettled_steps_protected=True", flush=True)

# Let /generate recover ONLY the now-resolved config-review path, preserving
# the existing public command and original owner authorization model.
path = ROOT / "telegram.py"
source = path.read_text()
start = source.index("    if cmd=='/generate':")
end = source.index("    if cmd=='/resume':", start)
block = source[start:end]
queue_line = "updated=db.queue_job(job['job_id'],owner)"
assert block.count(queue_line) == 1, "Unexpected Telegram /generate handler"
block = block.replace(
    queue_line,
    queue_line + "\n"
    "        if not updated and job.get('state') == 'NEEDS_REVIEW' and job.get('stage') == 'AUTO_PREFLIGHT':\n"
    "            updated=db.resume(job['job_id'],owner)",
    1,
)
path.write_text(source[:start] + block + source[end:])
print("RECREATION_RECOVERY generate_command_can_resume_config_review=True", flush=True)

# In the app queue, segment progress must not count completed steps from the
# previous v2 test namespace. Count only current RUN_KEY completed predictions.
path = ROOT / "app_queue.py"
source = path.read_text()
needle = "progress=db.count_auto_segments(job['job_id'])"
replacement = (
    "progress=db.count_auto_segments(job['job_id'])\n"
    "        if __import__('os').getenv('RECREATION_RUN_KEY'):\n"
    "            active_steps=[db.get_step(job['job_id'],full_video.step_key(i))\n"
    "                          for i in range(int(job.get('segment_total') or 0))]\n"
    "            progress=sum(1 for s in active_steps if s and s.get('state')=='COMPLETED')"
)
assert source.count(needle) == 1, "Unexpected application progress checkpoint"
path.write_text(source.replace(needle, replacement, 1))
print("RECREATION_PROGRESS_FIX current_run_only=True", flush=True)
