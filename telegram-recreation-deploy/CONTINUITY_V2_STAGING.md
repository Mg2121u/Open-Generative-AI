# Full-video v2: generated-last-frame chaining (staging only)

2026-11-15: User reported visible cuts between independently generated Nicole segments.

New engine retains the successful Nicole Image 1 identity + source Video 1 motion prompts and adds Image 2 = last decoded frame of the PREVIOUS generated Nicole segment. It extracts and saves that frame as a private PNG before the next provider submission and verifies the saved prediction references the correct previous frame. Strong mismatches trigger review before paying for later segments. Final FFmpeg stitching uses a 167-ms leading-frame blend with original soundtrack and unchanged timeline.

Important limitation: Wan 3.0 reference-to-video does NOT treat Image 2 as a strict first_frame. The model's separate strict first_frame mode cannot simultaneously accept source reference video. Smoothness is an improvement target, not an unqualified guarantee.

New run_key = nicole_full_480p_v2_chained_20261115. v1 outputs and billable records are untouched. No paid WaveSpeed calls performed. v2 requires NEW owner budget approval and exact v2 approval ID. Staging defaults are WORKER_MODE=preflight_only, FULL_VIDEO_ONE_SHOT=false, ENABLE_PAID_GENERATION=false, ENABLE_PAID_VOICE_GENERATION=false.

Local full suite: 103 passed (tests with a stub for missing local psycopg; the Render Docker build runs the real dependencies). Source details in CONTINUITY_V2_STAGING.md inside the archive.
