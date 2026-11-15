Dedicated staging FFmpeg preflight worker.

This branch contains the non-billable diagnostic worker logic, with 45 local tests passing. The new worker only claims LOCAL_PREFLIGHT jobs for the staging owner's allowlisted Telegram ID. It never claims WAN_SUBMIT, WAN_POLL, or other provider stages. An already archived source is probed, source clips are durably saved and checkpointed one at a time, then a 480p silent diagnostic preview is merged and saved. This does not alter the final WAN/Grok recreation pipeline or original soundtrack.

Render Blueprint file: telegram-recreation-deploy/worker.render.yaml
Creates only one dedicated worker, on plan 0.5c-512mb, which is currently $7/month. It references secrets in the existing staging web service with fromService, without writing those secrets to GitHub. First Blueprint sync requires review/approval, and autoSync is disabled. Never adopt or change Nicole production resources.
