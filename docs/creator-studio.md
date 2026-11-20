# Aria × Nicole Creator Studio — Qwen-only image models (isolated preview)

Updated November 20, 2026. Existing Telegram/Render/Nicole production agents are unchanged. This is a draft-branch Studio and GPU worker; production deployments are not updated automatically.

## Available image engines
- **Private Salad:** `Qwen/Qwen-Image-Edit-2511` using a Q4_K_M GGUF transformer + 4-bit text encoder. It **requires at least one private reference image**, and accepts identity plus scenery together. The worker runs the multi-image Qwen editing pipeline. Qwen-Image-Edit-2511 is Apache-2.0 licensed, allowing commercial use subject to the other terms/policies.
- **fal.ai Qwen image creation:** `fal-ai/qwen-image-2512` text-to-image, requiring the existing `FAL_KEY` in Vercel.
- **fal.ai Qwen image editing:** `fal-ai/qwen-image-edit-2511` using an explicitly supplied HTTPS image URL. Use the **private Salad** option for private photos rather than publicly hosted references.
- No previous image model remains selectable in the Studio UI, queue adapter, or worker. The separate hosted video tab still offers Wan I2V; the private GPU is image-only.
- **Important:** newer `Qwen-Image-2.1` has a *non-commercial research* license; the commercial-oriented pilot uses Apache-2.0 `Edit-2511` instead.

## Private reference data and geometry
- The Studio downsamples reference photos to maximum 1024px and JPEG-compresses them, keeping inputs in browser memory until submitted.
- Vercel forwards compact base64 JPEG inputs to the authenticated Salad worker over HTTPS, never writes them to public object storage or logs credentials.
- Identity reference is first, scenery second; the worker builds a camera-perspective and floor-placement instruction and allows foreground standing, rear standing or seated on sofa.
- Output quality requires manual QA: exact face/height, natural anatomy, room scale and occlusion are *not* guaranteed by either Qwen or prompting.

## Production constraints
- **Salad model not yet live-verified.** The upgraded worker needs its own completed image build and a manually approved rollout; rebuilding the image never updates a running instance.
- 24GB GPU VRAM and 24GB system RAM may not be sufficient for this Qwen 20B model even with quantization. Check GPU/CPU memory and image downloads before any billable launch. Cold start may take much longer than earlier models.
- GPU `/healthz` is not model readiness; use `/readyz` for inference availability, and check Salad Container Logs for failures.
- Existing Studio tokens, GPU tokens, Salad group settings, and other workflows are unchanged by this code revision.

## Vercel authentication and endpoints
Server-side preview env vars: `STUDIO_ACCESS_TOKEN`, `PRIVATE_GPU_ENDPOINT`, `PRIVATE_GPU_TOKEN`, and optional `FAL_KEY`. Never use `NEXT_PUBLIC_` for secrets.

`POST /api/creator-studio/queue` validates owner auth and provider, denies private GPU jobs until `/readyz` is healthy, and never submits image jobs automatically on page load.
`POST /api/creator-studio/status` fetches the async result for an existing provider job.

## Security
Short-lived image links and ephemeral storage mean generated images are not preserved across GPU restarts. This draft is not a paid media delivery system; persistent storage, retention policy, UI access restrictions and user consent/rights checks would be needed before commercial automation.
