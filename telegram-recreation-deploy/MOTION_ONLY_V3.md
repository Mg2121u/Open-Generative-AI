# Motion-aware Nicole v3, no WaveSpeed regeneration

The preceding FFmpeg v2 seam held a frozen ending JPEG over 200ms and then resumed the new clip; frame-difference metrics passed but temporal velocity could visibly jerk.

v3 replaces the static hold with bidirectional dense optical flow from the final generated frame of the preceding segment to the **actual next generated clip frame at 250ms**. The bridge occupies the same 250ms of the next segment, retaining full source duration. The source audio is remuxed unchanged, and temporal-motion metrics are logged at all four boundaries.

This is a **local postprocess-only** run: worker mode `motion_remux_once`, `MOTION_REMUX_ONE_SHOT=true`, `MOTION_REMUX_JOB_ID=<previous job>`, both paid provider flags `false`. It reuses exactly the already completed v2 provider MP4s and successful first canary, writes an independently named output `nicole_full_480p_motion_v3_20261115`, and delivers it once to the staging owner DM. No WaveSpeed API request, no new paid authorization, no change to Nicole production.

Tests: 108 passed locally, including a 13.9-second FFmpeg optical-flow assembly with original soundtrack, 4 new safety/motion tests, and exact existing-output reuse checks.

Optical flow is an editorial improvement; it cannot correct a new segment whose character pose or action is fundamentally inconsistent for its entire duration. User visual review is still required.
