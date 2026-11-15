# Nicole full-video staging v3.11

Builds on the successful 3-second Wan 3.0 reference-image + reference-video identity canary.

**Non-paid by default**: worker continues in preflight_only; user has NOT approved a full-length generation budget yet. Full-video paid mode requires a separate explicit approval ID, source job ID, cap, and enable flags.

13.9s source plan: five ordered clips, 3s, 3s, 3s, 2.9s, 2s. Reuse already successful 3-second Nicole identity canary only if source MP4 and Nicole photo match its original inputs, and the completed clip exists in private storage. Four additional paid predictions would cost ~USD 1.10 at current published Wan 3.0 480p rates; actual billing may differ.

Changes: new restart-safe full_video_once worker mode with one immutable provider step per paid segment; source-window FFmpeg trim; WaveSpeed 480p submission using Nicole image for identity; durable poll and private archive; FFmpeg concatenate and original-source audio restoration; at-most-once Telegram private video delivery; /fullplan no-cost owner command. Existing 67-node pipeline unchanged.

Locally verified the 13.9s FFmpeg trim/concat/audio merge: 13.9167s final 480x854 MP4 with audio. Building this version on Render runs its full automated test suite. See bundled FULL_VIDEO_STAGING.md.
