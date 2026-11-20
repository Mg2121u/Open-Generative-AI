# Aria & Nicole — Salad Container Engine image worker

This is a draft-branch **image-only** pilot, not a running cloud deployment. The funded Salad organization is not accessible by the GitHub REST preflight (401); the practical workaround is deploying a Docker image through the Salad web portal, which does not require the REST API key. Do not paste Salad API keys into the conversation. Your existing Vercel preview UI supports the worker contract.

## Overview
- FastAPI on port 8000, single replica / one image at a time.
- POST /jobs and GET /jobs/:id require Authorization: Bearer WORKER_API_TOKEN; GET /healthz and GET /readyz are health probes.
- FLUX.2 Klein 4B text-to-image only (Apache-2.0 model weights; BFL Usage Policy applies), RTX 4090 24GB or better for initial tests.
- Supports private S3-compatible persistence OR optional STORAGE_DRIVER=ephemeral for the initial no-extra-service pilot. Ephemeral jobs/images are saved only on this container disk and lost on replacement. Signed image URLs expire after 15 minutes.
- No character identity LoRA, reference image conditioning, or video yet. Characters are labels, not face-locked identities.
- Costs accrue while Salad container instances run; **stop the group after testing**. Direct HTTPS does NOT automatically wake a stopped instance.
- Use content consistent with host/model licenses, adult age/consent, and the law where run.

## Publish Docker image
In GitHub Actions, manually run **Publish Salad image worker** against branch feature/aria-nicole-creator-studio, tag pilot-1. The intended output is:
  ghcr.io/mg2121u/aria-nicole-gpu-worker:pilot-1
This manual build has NOT been run. Grant Salad read access to the GHCR image or change package visibility as appropriate.

## Storage options: no-extra-service pilot versus private persistent bucket
**Pilot without new storage account:** set `STORAGE_DRIVER=ephemeral`, `MAX_REPLICAS=1`, `ENVIRONMENT=production`, and a unique `WORKER_API_TOKEN` of at least 32 random characters. No S3 credentials are needed. All jobs and images are temporary and can vanish on a container restart/preemption, so this mode is for first GPU verification only. Don't use it for private customer deliveries.

**For production:** create a private Cloudflare R2 or other S3-compatible bucket with a restricted API token. Salad container environment:
- S3_ENDPOINT = https://<account-id>.r2.cloudflarestorage.com
- S3_REGION = auto
- S3_BUCKET = <your-private-bucket>
- S3_ACCESS_KEY = <restricted-access-key>
- S3_SECRET_KEY = <restricted-secret>
- WORKER_API_TOKEN = <different-random-secret-with-32-or-more-characters>
- ENVIRONMENT = production
Leave MOCK_INFERENCE unset. No API credentials are embedded in source code. When using STORAGE_DRIVER=ephemeral, S3 variables above are not needed.

## SaladCloud Portal bypasses REST API 401

**Do not recreate or re-enter the SaladCloud API key.** Open https://portal.salad.com and choose the credited organization `mgg1981`. In Container Engine, create project `aria-nicole-studio` if missing, then create a container group with the published image. This uses the logged-in browser session instead of the failing GitHub REST key.

**GHCR access:** GHCR packages are often private by default; if Salad cannot pull the image, change the package visibility to Public from GitHub Packages settings or configure the supported registry authentication. Do not paste a GitHub PAT into chat.

## Salad Console settings for first safe test
1. Create project aria-nicole-studio under the already funded organization.
2. Container Engine > Create Container Group > image from GHCR above, linux/amd64, port 8000.
3. 1 replica; GPU class RTX 4090 24GB; Priority Lowest (Batch). If supply unavailable use compatible 24GB+ NVIDIA GPU.
4. HTTPS Container Gateway / load balancer enabled; note its hostname. A signed /assets/{jobid}.png link is served from that hostname in ephemeral mode.
5. Liveness HTTP /healthz and readiness HTTP /readyz; configure a **generous startup grace** for model weight download, preferably several minutes.
6. Do NOT enable automatic reload/scale to multiple replicas for this pilot; manual start and stop.
7. Keep group stopped until image, storage, token, policy and cost settings are checked.
8. Disable automatic Salad account recharge while using a $20 controlled budget.

## Vercel preview configuration (server-side, no NEXT_PUBLIC_)
- PRIVATE_GPU_ENDPOINT = https://<Salad gateway hostname>
- PRIVATE_GPU_TOKEN = same value as WORKER_API_TOKEN on Salad.
- STUDIO_ACCESS_TOKEN = separate 32+ character private owner password.
For now choose Independent GPU and **Image Studio** in the app. The worker rejects any reference URL or video requests until those features are built.

## Verification
- Container readiness responds 200 only after model and storage startup succeeds.
- Invalid private token returns 401.
- Single permitted image request should be 202, then IN_QUEUE > IN_PROGRESS > COMPLETED.
- Output must have a short-lived private link.
- Inspect actual consumed Salad credits; manually stop container once test completed.

## Important shortcomings
- SDXL inference and Salad image deployment have not been tested yet.
- Worker running on interruptible community GPU may lose active image generations; expired jobs surface ERROR after 20 minutes.
- Build image is large and may have substantial first-start latency; avoid promising instant cold boot.
- The FLUX.2 Klein 4B model has explicit restrictions on unlawful content and non-consensual intimate imagery; it does not guarantee any particular adult output.
- The Vercel page route itself needs additional access protection before using private reference photos (API token does not hide the HTML page).
- This pilot uses one process only. To scale to zero or multiple replicas properly, replace direct HTTP with durable Salad Queue/worker/controller flow.

## No-storage pilot risks
- `STORAGE_DRIVER=ephemeral` uses temporary local disk; no persistent history or reference photos; all output can disappear when a node preempts/restarts, and signed URLs will stop working.
- One replica ONLY; never use this driver with autoscaling. For real users, move to private S3 and a durable queue.
- A public Salad gateway is protected by the worker Bearer token on job endpoints; the image endpoints require short-lived HMAC-signed links. Protect the Vercel studio page before uploading personal photos.

## Salad Portal's accidental auto-converted environment variable names

Salad's saved configuration was inspected and its three variable names had been transformed into per-letter snake case. The pilot worker now accepts these three exact legacy names as aliases, with the standard names taking precedence:
- `_w_o_r_k_e_r__a_p_i__t_o_k_e_n` → `WORKER_API_TOKEN`
- `_s_t_o_r_a_g_e__d_r_i_v_e_r` → `STORAGE_DRIVER`
- `_e_n_v_i_r_o_n_m_e_n_t` → `ENVIRONMENT`

No API tokens are printed. Legacy alias handling does **not** remove the need to rotate the owner token once the pilot is online, since the token was included in prior configuration screenshots/messages. Use the updated image **after** its build and real HTTP tests have passed; do not restart a billable instance to test unverified builds.

## 2026-11-20 startup probe failure after binding IPv4-only
Container group v5 System Events show two instances reached Running with 47–48 seconds of uptime then **Instance Interrupted (Startup Probe Failure)**. Container logs show uvicorn running and loading model weights, with no Python exception. Salad's Container Gateway requires IPv6 support. The prior `--host 0.0.0.0` change enabled IPv4 only and made gateway/probe network reachability a strong failure hypothesis. Revert to `--host ::` and test real HTTP responses over IPv6 `[::1]` and IPv4 loopback in CI. Do not claim this establishes Salad runtime reachability without a successful live probe.
