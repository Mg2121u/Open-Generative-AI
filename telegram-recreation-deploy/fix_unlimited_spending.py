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
