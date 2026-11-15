Wan 3.0 Staging Guard (2026-11-15)

No paid model calls have been made.

Safety: one-shot PostgreSQL ON CONFLICT DO NOTHING reservations before each external POST; paywall in provider.submit_once, chat_once, upload_media_once; allowed Wan 3.0 reference-to-video and 480p only; no auto-resubmission after ambiguous/terminal failures; poll saved request IDs; protect Telegram delivery reservations.

Tests: 58 passed, 1 optional skipped locally. Staging worker remains WORKER_MODE=preflight_only, ENABLE_PAID_GENERATION=false, ENABLE_PAID_VOICE_GENERATION=false.

Not yet tested: live WaveSpeed model generation, end-to-end results and output quality. Separate authorization is required for paid canary.
