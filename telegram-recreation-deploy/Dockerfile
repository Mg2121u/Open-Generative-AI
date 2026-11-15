FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY telegram-recreation-deploy/source.tar.xz /tmp/recreation-source.tar.xz
RUN tar -xJf /tmp/recreation-source.tar.xz -C /app --strip-components=1 && rm /tmp/recreation-source.tar.xz
COPY telegram-recreation-deploy/fix_unlimited_spending.py /tmp/fix_unlimited_spending.py
RUN python /tmp/fix_unlimited_spending.py && rm /tmp/fix_unlimited_spending.py
RUN printf '\\n=== DIAGNOSTIC SOURCE FILES ===\\n' && find /app/recreation -maxdepth 1 -name '*.py' -printf '%f\\n' && printf '\\n=== TELEGRAM COMMAND AND REVIEW PATH ===\\n' && grep -R -n -C 10 -E '(\/generate|NEEDS_REVIEW|requeue|resume|def generate|def .*command|def .*owner)' /app/recreation/telegram.py /app/recreation/db.py /app/recreation/main.py /app/recreation/worker.py 2>/dev/null | head -n 350
RUN printf '\\n=== FULL VIDEO STEP AND AUTO FLOW ===\\n' && grep -n -E '^def (step_key|segment_key|report|run_auto|_run_segments|_get_reused_first|_guard_auto)|RUN_KEY|CHAI[N]|SEGMENT|STEP_PREFIX' /app/recreation/full_video.py && sed -n '20,105p' /app/recreation/full_video.py && sed -n '210,325p' /app/recreation/full_video.py && sed -n '355,500p' /app/recreation/full_video.py && printf '\\n=== RECOVERY DB / TELEGRAM ===\\n' && sed -n '107,125p' /app/recreation/db.py && sed -n '182,214p' /app/recreation/telegram.py
RUN RECREATION_RUN_KEY=nonsecret_probe_key PYTHONPATH=/app python -c "from recreation import full_video; assert full_video.RUN_KEY == 'nonsecret_probe_key'; assert full_video._reusable_canary({}) is False; print('RECREATION_RUNTIME_NAMESPACE_TEST passed=true')"
RUN pip install --no-cache-dir -r requirements.txt
RUN PYTHONPATH=/app pytest -q /app/tests
RUN python -c "from recreation.main import app; assert app.title == 'Telegram Recreation Studio'"
# Nonsecret diagnostic for the two spending-cap parsing sites and job review workflow.
RUN sed -n '35,100p' /app/recreation/app_queue.py && sed -n '320,380p' /app/recreation/full_video.py && grep -R -n -E 'Per-video spending cap configuration invalid|AUTO_PREFLIGHT|NEEDS_REVIEW|/retry|retry_job' /app/recreation --include='*.py' | head -n 70

# Non-secret build diagnostic: verify the archived app's supported cost and voice configuration names.
RUN grep -R -n -E 'APP_MAX_ESTIMATED_USD|ELEVENLABS_API_KEY|ELEVENLABS_MODEL|NICOLE.*VOICE|ARIA.*VOICE|PAID_VOICE' /app/recreation --include='*.py' | head -n 90 || true
CMD ["sh","-c","python -c 'import os; from recreation import full_video; print(\"RECREATION_RUN_NAMESPACE env=%s effective=%s\" % (os.getenv(\"RECREATION_RUN_KEY\"), full_video.RUN_KEY))'; python -c 'import os; print(\"RECREATION_VOICE_READINESS key_present=%s enabled=%s cost_cap=%s\" % (bool(os.getenv(\"ELEVENLABS_API_KEY\")), os.getenv(\"ENABLE_PAID_VOICE_GENERATION\"), os.getenv(\"APP_MAX_ESTIMATED_USD\")))'; if [ \"${APP_ROLE:-web}\" = worker ]; then exec python -m recreation.worker; else exec uvicorn recreation.main:app --host 0.0.0.0 --port ${PORT:-10000}; fi"]
