FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/hf ENVIRONMENT=production
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /service
RUN pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py local_store.py qwen_engine.py ./
RUN python -m py_compile app.py qwen_engine.py local_store.py
RUN python -c "from diffusers import QwenImageEditPlusPipeline, QwenImageTransformer2DModel, GGUFQuantizationConfig; from transformers import BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration; import gguf"
RUN useradd -m -u 10001 worker && mkdir -p /data/hf && chown worker:worker /data/hf
USER worker
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "::", "--port", "8000", "--workers", "1"]
