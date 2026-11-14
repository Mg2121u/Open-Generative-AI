FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY telegram-recreation-deploy/source.tar.xz /tmp/recreation-source.tar.xz
RUN tar -xJf /tmp/recreation-source.tar.xz -C /app --strip-components=1 && rm /tmp/recreation-source.tar.xz
RUN pip install --no-cache-dir -r requirements.txt
RUN PYTHONPATH=/app pytest -q /app/tests
RUN python -c "from recreation.main import app; assert app.title == 'Telegram Recreation Studio'"
CMD ["sh", "-c", "if [ \"${APP_ROLE:-web}\" = worker ]; then exec python -m recreation.worker; else exec uvicorn recreation.main:app --host 0.0.0.0 --port ${PORT:-10000}; fi"]
