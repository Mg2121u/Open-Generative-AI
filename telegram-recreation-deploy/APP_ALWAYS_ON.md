# Recreation Studio v3.14 — Always-on owner app

Enable the isolated Telegram Recreation Studio so the authorized owner can start repeated full-video Nicole jobs directly in Telegram without changing Render environment variables for each job.

Usage: /new -> upload MP4 -> optionally /identity, /scenery, /clothes, /instructions -> /generate -> /status. Saved owner-specific Nicole identity carries forward to new drafts, source media never carries forward. No daily or job-count quotas; the old single-job 60s staging cap is lifted. Provider billing and Telegram/media technical limits still apply. Only an explicit /generate request queues paid work.

Staging Render worker mode: app_always_on; ENABLE_PAID_GENERATION true; ENABLE_PAID_VOICE_GENERATION false. Web service: ENABLE_PAID_GENERATION true; existing private Telegram webhook and whitelist unchanged. Bot and WaveSpeed credentials remain secrets in Render. Old full-video one-shot flags stay false and approval ID unset.

Every new job's first 480p Wan 3.0 request uses Nicole reference image and first source-motion slice. Every subsequent request uses Nicole image + actual final frame from preceding generated clip + the next source-motion slice. Durable per-job/per-segment provider IDs stop duplicate billing after restarts. Last-frame checkpoints persist. Local 250ms optical-flow seam smoothing and original-source soundtrack restoration are applied. The /preflight command remains available in always-on worker.

No automatic public access; only approved Telegram user IDs may submit chargeable jobs. A previously unverified character path or voiceover is not silently activated. See APP_ALWAYS_ON.md bundled in the source archive. Local owner/security tests were run with provider calls mocked; full automated suite runs during Render Docker build.