# Telegram Recreation Studio — isolated staging build

This branch contains the **Version 3 source snapshot**, published from the tested local package. It is an n8n-independent Telegram recreation application. The original 67-node n8n workflow and Nicole production services are untouched.

- Source archive: `telegram-recreation-deploy/source.tar.xz`
- Staging Dockerfile: `telegram-recreation-deploy/Dockerfile`
- Build context: repository root `.`
- Runtime app roles: `APP_ROLE=web` (HTTP webhook) and `APP_ROLE=worker` (persistent background worker)
- Health: `GET /health`
- Safeguards: `ENABLE_PAID_GENERATION=false`, `ENABLE_PAID_VOICE_GENERATION=false` until separately verified.

The source snapshot preserves the original Gemini/Grok/Wan transformation scripts and includes the Nicole/Aria ElevenLabs v4 configuration. No personal API tokens or production database secrets are included.

**This staging service must not be connected to Nicole's existing bot token, Stars flows, or Supabase project.**

Before enabling uploads or generation, provision a dedicated PostgreSQL database, S3-compatible bucket, Telegram test bot, webhook secret, allowed owner ID, WaveSpeed key, and ElevenLabs key. Initialize tables using `schema.sql` inside the archive. The worker runs separately from the HTTP process.

The staging health endpoint can be deployed without secrets, but reports `configured: false`. A passing health check alone is not a successful Telegram generation test.
