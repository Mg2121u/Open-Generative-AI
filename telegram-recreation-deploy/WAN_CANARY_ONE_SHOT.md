# Wan 3.0 One-shot 480p staging canary

Status: PREPARED, DISABLED. Never commit API keys or paid credentials.

The canary worker mode runs ONLY one WaveSpeed model reference-to-video request: 3 seconds of saved source reference footage, 3-second output at 480p, vertical framing, model alibaba/wan-3.0/reference-to-video. It is a transport/connectivity canary, **not a Nicole identity fidelity test**, and does not substitute the original 67-node pipeline. No paid LLM or voiceover.

Published estimate: USD 0.05 x (ceil(reference video seconds) + 3 output seconds). Enforced estimate budget USD 0.40. The source is clipped to 3 seconds locally and re-probed before submission. Provider pricing may ultimately vary: this calculation is not an externally guaranteed spending cap.

Protection: WORKER_MODE=canary_once, WAN_CANARY_ONE_SHOT=true, WAN_CANARY_MAX_USD=0.40, WAN_CANARY_SOURCE_JOB_ID=<archived owner draft UUID>, ENABLE_PAID_GENERATION=true, ENABLE_PAID_VOICE_GENERATION=false, WAVESPEED_API_KEY supplied only via Render secret. All switches off by default.

Uses an immutable PostgreSQL step key wan_canary_480p_20261115_001, with INSERT ON CONFLICT DO NOTHING, so after any crash and/or re-run the provider POST cannot be repeated. Polling uses stored provider ID. Output is copied to private storage. Restore worker to preflight_only and generation to false after canary check.

The web staging service never enables paid generation; Nicole production remains untouched. Source archive tests: 63 passed, 1 skipped locally.
