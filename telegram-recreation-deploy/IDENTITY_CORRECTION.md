# Nicole identity correction (staging only)

The first Wan 3.0 canary proved provider connectivity, **not** character replacement. That request passed Video 1 alone and incorrectly preserved the source performer.

New staging application behavior:
- Upload Nicole primary face reference via Telegram private DM image with caption /identity, or reply /identity to her image
- Save the reference privately in Supabase Storage and record a job-specific identity checkpoint
- /status reports missing/pending/ready identity
- The provider rejects paid reference-to-video requests without at least one HTTPS reference_images value, even if paid generation is enabled
- Use Image 1 for Nicole identity and Video 1 for choreography, camera and scene, never source person identity
- New one-shot key wan_nicole_identity_canary_480p_20261115_002 to avoid repetition of the first charge
- Signed image URL is generated just in time; no public reference URL is required
- All paid generation switches remain OFF; no second WaveSpeed POST approved or sent

Tests: 69 passed, 1 skipped locally before publishing. The original archived clip and first canary output are preserved. This branch is isolated from Nicole production and n8n.
