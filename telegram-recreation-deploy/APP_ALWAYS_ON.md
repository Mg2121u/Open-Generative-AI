# Recreation Studio normal always-on mode

Deploy staging only; do not modify Mg2121u/Nicole-Telegram-Agent or Nicole production Render services.

Owner can upload a source MP4 directly to the isolated Recreation Studio Telegram DM. If the previous job is running or completed, a new job draft is created automatically, carrying forward only the owner's saved Nicole photo. A successful /identity upload overrides it. /generate explicitly authorizes a new paid Wan generation, without any Render configuration change or per-run approval ID. /status tracks progress.

Always-on worker `WORKER_MODE=app_always_on`, `ENABLE_PAID_GENERATION=true`, voice generation disabled; isolated web webhook `ENABLE_PAID_GENERATION=true`. No startup charges: only owner-initiated /generate queues generation.

Continuity: image 1 Nicole; image 2 previous GENERATED segment final frame; video 1 source-motion slice. Motion-aware 250ms seam interpolation, original-audio merge, final Telegram delivery. Durable provider step IDs prevent automatic repeated charges. Only owner allowlisted IDs are permitted.

Default estimated per-video spending guard: `APP_MAX_ESTIMATED_USD=10.00`. It is not a daily usage quota; higher estimates pause before any paid provider submission. For long sources the estimate is based on actual source duration, model rates, and segment count. No other automatic daily/monthly limits.

Safety: completed source media and old outputs remain untouched. Production Nicole Telegram, paid calls, Stars, and Supabase memory are untouched. Built-in unit and Render Docker tests verify queue lifecycle; a real paid generation needs the owner's own MP4 and /generate.
