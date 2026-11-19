# Aria × Nicole Creator Studio — isolated preview

This is a prototype at /creator-studio inside the existing Next.js application. It does not modify Telegram, Render, Supabase, or the root studio. Keep on a draft feature branch until reviewed.

## Working pieces
- Browser-session-only gallery; independent Aria and Nicole selection; private access token in component memory only.
- fal.ai submit + async status/result polling for FLUX.2 Dev, FLUX.2 Klein 9B and Wan 3.0 image-to-video. Uses current published model schemas.
- Model safeguards remain **enabled**. fal explicitly prohibits sexually explicit content and bypassing protective controls. This app does not disable them.
- Separate HTTPS-only GPU adapter. Its contract is below. GPU provider, legal approval, and model deployment are NOT included.
- No paid requests are issued merely by deploying. Requests cost money only when the owner explicitly clicks Generate.

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
