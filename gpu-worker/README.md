# Aria & Nicole Creator Studio — Qwen Image Edit 2511 Salad pilot

The isolated GPU worker in this branch now uses **Qwen-Image-Edit-2511**, not the previous image generator. This branch does not change the existing Nicole or Aria Telegram/Render workflows.

## Engine and licenses
- Model: `Qwen/Qwen-Image-Edit-2511` (Apache-2.0).
- Worker engine: `QwenImageEditPlusPipeline`, with `unsloth/Qwen-Image-Edit-2511-GGUF` Q4_K_M transformer and quantized 4-bit Qwen2.5-VL text encoder.
- Reference roles: `identity` and `scenery`; at least one is **required** for GPU editing. The worker is now **image-to-image only**. For reference-free text-to-image, choose hosted `fal-ai/qwen-image-2512` in the Creator Studio if FAL_KEY is configured.
- Video on the Salad GPU remains disabled. Existing hosted video flow is separate.
- **Licensing:** Qwen-Image-2.1 and its Turbo model use a restricted research license, so this commercial-oriented pilot deliberately uses the separately Apache-2.0-licensed Edit-2511 instead.
- This Q4+NF4 memory profile is a first engineering candidate for the 24GB-VRAM RTX 3090, **not yet runtime verified**. Model startup may require substantial downloads and can hit the 24GB RAM limit. Do not start or replace the existing running Salad image until readiness, memory budget and cost have been reviewed.
- Compared with earlier small models, cold start may be significantly slower and image identity/geometry are not guaranteed.

## API contract
- `GET /healthz` — process alive.
- `GET /readyz` — 200 only once Qwen and storage are initialized; 503 during loading or failure.
- `POST /jobs` and `GET /jobs/{id}` — Bearer WORKER_API_TOKEN. Job example:

```json
{
  "character": "Nicole",
  "mode": "image",
  "prompt": "Nicole naturally standing on the foreground carpet",
  "aspect_ratio": "9:16",
  "placement": "foreground_floor",
  "reference_images": {
    "identity": "data:image/jpeg;base64,REDACTED",
    "scenery": "data:image/jpeg;base64,REDACTED"
  }
}
```

Reference images are resized in-browser before submission and decoded in memory; they are not stored by the worker. The worker accepts at most two compact JPEGs and never fetches user-provided URLs. Actual results and error states are stored only in private S3 or the local ephemeral store.

## Salad configuration (manual browser setup)
- Existing group: `mgg1981/aria-nicole-studio/aria-nicole-image-pilot`.
- Private GHCR image pushed by the **Publish Salad image worker** GitHub Action.
- Image tag/digest must be copied only from a **successful, completed** GitHub build for the new commit. A new build does not modify Salad automatically.
- Networking HTTP port **8000**, IPv6 listener `::`, health probes on TCP port **8000**.
- One replica; RTX 3090 24GB class; configure sufficient **system RAM** and disk/cache capacity for model download, then validate in an isolated paid test only if approved.
- Priority, replica count, and machine availability can change real costs.
- Valid Salad environment values: `WORKER_API_TOKEN`, `STORAGE_DRIVER=ephemeral`, `ENVIRONMENT=production`. The worker supports legacy per-letter underscore aliases found in a previously saved Salad group. No keys should be copied into source control.
- Never enable `MOCK_INFERENCE` in production.
- Your existing tokens were **not changed** during the Qwen migration.

## Startup and storage behavior
- Qwen models are not bundled into the image. They are downloaded from their repositories on container startup. Without a persistent cache, replacement/preemption loses the downloads.
- The image build and GitHub CPU/mock unit tests do **not** validate Qwen model loading, GPU memory use, fidelity, or runtime speed.
- `STORAGE_DRIVER=ephemeral` stores jobs and generated images only on local container disk. They are lost on container replacement; output links expire after 15 minutes.
- For production, use private S3 object storage and a durable queue with a lifetime/cost limit. Do not use this ephemeral pilot to automatically deliver paid content.

## Security and quality
- Do not print, share, or rotate existing tokens without owner instruction.
- Never expose user reference photos through a public link for self-hosted editing.
- One image at a time on the pilot. Check both physical proportions and identity/scenery consistency.
- Keep current production Telegram, Supabase, Stars and Fanvue integrations untouched. 
