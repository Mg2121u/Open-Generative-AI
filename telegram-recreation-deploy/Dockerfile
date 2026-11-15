FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY telegram-recreation-deploy/source.tar.xz /tmp/recreation-source.tar.xz
RUN tar -xJf /tmp/recreation-source.tar.xz -C /app --strip-components=1 && rm /tmp/recreation-source.tar.xz
COPY telegram-recreation-deploy/fix_unlimited_spending.py /tmp/fix_unlimited_spending.py
RUN python /tmp/fix_unlimited_spending.py && rm /tmp/fix_unlimited_spending.py
RUN pip install --no-cache-dir -r requirements.txt
RUN PYTHONPATH=/app pytest -q /app/tests
RUN python -c "from recreation.main import app; assert app.title == 'Telegram Recreation Studio'"
# Nonsecret diagnostic for the two spending-cap parsing sites and job review workflow.
RUN sed -n '35,100p' /app/recreation/app_queue.py && sed -n '320,380p' /app/recreation/full_video.py && grep -R -n -E 'Per-video spending cap configuration invalid|AUTO_PREFLIGHT|NEEDS_REVIEW|/retry|retry_job' /app/recreation --include='*.py' | head -n 70

# Non-secret build diagnostic: verify the archived app's supported cost and voice configuration names.
RUN grep -R -n -E 'APP_MAX_ESTIMATED_USD|ELEVENLABS_API_KEY|ELEVENLABS_MODEL|NICOLE.*VOICE|ARIA.*VOICE|PAID_VOICE' /app/recreation --include='*.py' | head -n 90 || true
CMD ["sh", "-c", "python -c 'import os; print(\"RECREATION_VOICE_READINESS key_present=%s enabled=%s cost_cap=%s\" % (bool(os.getenv(\"ELEVENLABS_API_KEY\")), os.getenv(\"ENABLE_PAID_VOICE_GENERATION\"), os.getenv(\"APP_MAX_ESTIMATED_USD\")))'; if [ \"${APP_ROLE:-web}\" = worker ]; then exec python -m recreation.worker; else exec uvicorn recreation.main:app --host 0.0.0.0 --port ${PORT:-10000}; fi"]
