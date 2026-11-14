FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY n8n-ffmpeg-bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY n8n-ffmpeg-bridge/app.py .
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
