# Aria & Nicole — Salad Container Engine image worker

This is a draft-branch **image-only** pilot, not a running cloud deployment. The funded Salad organization is not connected to ChatGPT with a Salad API. Do not paste Salad API keys into the conversation. Your existing Vercel preview UI supports the worker contract.

## Overview
- FastAPI on port 8000, single replica / one image at a time.
- POST /jobs and GET /jobs/:id require Authorization: Bearer WORKER_API_TOKEN; GET /healthz and GET /readyz are health probes.
- SDXL Base 1.0 text-to-image only, RTX 4090 24GB or better for initial tests.
- Private S3-compatible object storage persists each job and PNG. Results are presigned read URLs expiring after 15 minutes.
- No character identity LoRA, reference image conditioning, or video yet. Characters are labels, not face-locked identities.
- Costs accrue while Salad container instances run; **stop the group after testing**. Direct HTTPS does NOT automatically wake a stopped instance.
- Use content consistent with host/model licenses, adult age/consent, and the law where run.

## Publish Docker image
In GitHub Actions, manually run **Publish Salad image worker** against branch feature/aria-nicole-creator-studio, tag pilot-1. The intended output is:
  ghcr.io/mg2121u/aria-nicole-gpu-worker:pilot-1
This manual build has NOT been run. Grant Salad read access to the GHCR image or change package visibility as appropriate.

## Storage required (private bucket)
Create a private Cloudflare R2 or other S3-compatible bucket with a restricted API token. Salad container environment:
- S3_ENDPOINT = https://<account-id>.r2.cloudflarestorage.com
- S3_REGION = auto
- S3_BUCKET = <your-private-bucket>
- S3_ACCESS_KEY = <restricted-access-key>
- S3_SECRET_KEY = <restricted-secret>
- WORKER_API_TOKEN = <different-random-secret-with-32-or-more-characters>
- ENVIRONMENT = production
Leave MOCK_INFERENCE unset. No API credentials are embedded in source code.

## Salad Console settings for first safe test
1. Create project aria-nicole-studio under the already funded organization.
2. Container Engine > Create Container Group > image from GHCR above, linux/amd64, port 8000.
3. 1 replica; GPU class RTX 4090 24GB; Priority Lowest (Batch). If supply unavailable use compatible 24GB+ NVIDIA GPU.
4. HTTPS Container Gateway / load balancer enabled; ensure its hostname is known.
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
- The Vercel page route itself needs additional access protection before using private reference photos (API token does not hide the HTML page).
- This pilot uses one process only. To scale to zero or multiple replicas properly, replace direct HTTP with durable Salad Queue/worker/controller flow.
