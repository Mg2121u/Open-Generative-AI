# Aria × Nicole Creator Studio — isolated preview

This is a prototype at /creator-studio inside the existing Next.js application. It does not modify Telegram, Render, Supabase, or the root studio. Keep on a draft feature branch until reviewed.

## Working pieces
- Browser-session-only gallery; independent Aria and Nicole selection; private access token in component memory only.
- fal.ai submit + async status/result polling for FLUX.2 Dev, FLUX.2 Klein 9B and Wan 3.0 image-to-video. Uses current published model schemas.
- Model safeguards remain **enabled**. fal explicitly prohibits sexually explicit content and bypassing protective controls. This app does not disable them.
- Separate HTTPS-only GPU adapter. Its contract is below. Salad worker source and a manual GHCR build workflow are now included in gpu-worker/. GPU provider connection and real inference deployment have NOT been completed.
- No paid inference requests are issued merely by deploying the Vercel UI. Salad GPU container instances bill while running even without requests; stop the container group when idle.

## Vercel deployment
Because this is a folder in a public GitHub repository, review deployment permissions and protect the preview URL. Do not put character reference photos or API keys in Git.
Set the following **server-side** environment variables in the intended Vercel project before generating:
- STUDIO_ACCESS_TOKEN: secret of at least 16 characters; entered into the UI for each session. API refuses requests unless configured.
- FAL_KEY: API key from the same fal account intended to pay for requests. A ChatGPT connector's OAuth session is NOT a Vercel key.
- PRIVATE_GPU_ENDPOINT: optional approved HTTPS worker base URL (do not include /jobs).
- PRIVATE_GPU_TOKEN: optional secret for that worker.
Do not give any of these the NEXT_PUBLIC_ prefix. Do not deploy with default/example passwords.

The image-to-image reference field is not yet wired to fal: existing image models are text-to-image only. Video requires an HTTPS start-frame image; use consented, authorized reference media. This preview stores no persistent gallery, reference library, or job ledger. Do not use for production, automated recurring generations, or private unencrypted identity uploads yet.

## Independent GPU worker HTTP contract
POST /jobs
Authorization: Bearer PRIVATE_GPU_TOKEN
JSON: { character: "Aria"|"Nicole", mode: "image"|"video", prompt, reference_image_url, aspect_ratio: "9:16"|"1:1"|"16:9", duration?: 5|10|15 }
Response: { id: "opaque_job_id" }

GET /jobs/:id
Authorization: Bearer PRIVATE_GPU_TOKEN
Response: { status: "IN_QUEUE"|"IN_PROGRESS"|"COMPLETED"|"ERROR", assets: ["https://..."], error?: "..." }

A worker must secure all asset URLs, validate rights and age, enforce local law and the host's policy, and use private storage with expiring URLs. GPU costs are separate from Vercel and fal inference.

## Next milestones
1. Vercel preview build, auth and API smoke tests (no paid inference).
2. Save approved Aria/Nicole reference images in private object storage with scoped access.
3. Test a single low-cost, permitted generation on fal after explicit spending approval.
4. Obtain written confirmation from a GPU host permitting the intended lawful workload, then deploy a compatible model/worker.
5. Persist jobs and asset history; add a hard monthly spend cap and one active job per character.

## Private reference-photo pilot (feature branch, not yet GPU verified)
Reference-guided FLUX.2 Klein 4B editing supports up to **two** browser-provided references: `identity` and `scenery`. Images are resized client-side to no more than 1024px on their longer edge and compressed to JPEG; the API accepts only base64 inline JPEGs with size limits. The server forwards them over HTTPS inside the authenticated GPU request; **no public storage URL is created** and no references are written to the job record. For processing, the worker decodes them in memory and supplies them as image-conditioning inputs to `Flux2KleinPipeline`. Neither identical facial appearance nor scene exactness is guaranteed; those require separate visual QA. This does not add video support or Telegram integration. Browser-session references are not persistent. Vercel's overall request body limits still apply. All existing owner and worker tokens remain untouched.

## 2026-11-20 GPU readiness and explicit provider errors
The Studio checks the worker's unauthenticated `/readyz` over HTTPS before submitting a generation job, refuses submissions when the model is still loading or initialization has failed, and displays a live status indicator refreshed every 15 seconds in the private preview UI. GPU API 401/422/503 errors now surface concise actionable text rather than hiding behind a generic message. Token values and worker requests are never logged. This protects against repeated unsuccessful GPU submissions but is not a substitute for Salad container log inspection. The independent reference worker image was published at `sha256:1ea091cf60892164e54a9cdba6cfddb37bcffaa6fbb9f5d54b5d59648b00f31a` and must be installed in Salad for reference-photo functionality; the earlier image accepts only text-to-image.

## 2026-11-20 composition and proportion correction
A generated Nicole-and-room test placed her shoes in/over the coffee table and scaled her body too small relative to the furniture. The worker's old prompts listed the two input-image roles but did not constrain a floor plane, furniture occlusion, adult-body anatomy or human scale. The updated worker now builds a role-specific composition prompt preserving image 2's original room as master scenery, image 1's character appearance, realistic body/furniture scale, and floor contact shadows. The Studio offers **foreground floor** (default), **behind table**, or **seated on sofa**, but these are text guidance, not certified geometric constraints. Use a clear floor foreground setting for full-body imagery. The photos stay private/in-memory and no extra GPU job is triggered automatically. Regression tests cover the rendered guidance and rejection of unknown placements. An upgraded Docker image is necessary to activate this worker behavior on Salad; do not hot-swap a working instance without deciding to interrupt the model.\n