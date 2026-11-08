# Aria and Nicole: WaveSpeed creator integration (staging)

This feature provides a separate /creator preview page and authenticated /api/creator/jobs endpoint. It does not modify the current MuAPI studios, Telegram calls and Stars flows, Fanvue automations, or any live publishing.

## Models routed through WaveSpeed API v3

- seedream-image: bytedance/seedream-v5.0-pro
- seedream-edit: bytedance/seedream-v5.0-pro/edit
- wan-text-video: alibaba/wan-3.0/text-to-video
- wan-image-video: alibaba/wan-3.0/image-to-video
- wan-reference-video: alibaba/wan-3.0/reference-to-video

All generations use WaveSpeed's paid hosted inference. Provider/platform policies and commercial terms still apply. This is not a moderation bypass.

## Environment for preview deployment

Set server-side secrets on the Vercel deployment for this repository:

- WAVESPEED_API_KEY: the existing WaveSpeed API key.
- CREATOR_API_TOKEN: a different high-entropy token for the creator app and automation clients.
- CREATOR_MAX_VIDEO_SECONDS: optional, defaults to 10 seconds; absolute maximum is 30.

If the WaveSpeed key is currently saved only on Render or n8n, it is not automatically accessible by Vercel. Copy it securely to Vercel environment variables, not to source code, localStorage, a query string, or a Telegram message.

Visit /creator on a preview deployment and enter the creator access token. The client holds it only in memory.

## API usage

POST /api/creator/jobs
Authorization: Bearer [creator access token]
Content-Type: application/json

Nicole reference image edit:

    {
      "creator": "nicole",
      "model": "seedream-edit",
      "prompt": "Editorial portrait, preserve facial likeness and hairstyle, elegant evening outfit, warm window light, vertical photograph",
      "images": ["https://example.com/temporary-signed-nicole-reference.jpg"],
      "aspect_ratio": "9:16",
      "resolution": "1k"
    }

Aria image-to-video:

    {
      "creator": "aria",
      "model": "wan-image-video",
      "prompt": "Natural smile and gentle hair movement, slow cinematic camera push, warm studio ambience",
      "image": "https://example.com/temporary-signed-aria-reference.jpg",
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "duration": 5,
      "generate_audio": false
    }

The POST result returns a prediction id, creator, model, status, and not_published status. Poll with GET /api/creator/jobs?id=[prediction-id] and the same authorization header. Completed outputs are in the outputs array. Stop on failed, cancelled, timeout, deleted.

Wan text-to-video requires only prompt and options. Wan reference-to-video accepts reference_images (up to 10), reference_videos (up to 5), reference_audios (up to 5); at least one reference required. WaveSpeed enforces further constraints on reference duration and format.

## Integrating the existing Telegram and Fanvue workflows

Keep an independent authorized workflow for each persona. In the existing n8n / agent workflow:

1. Resolve character identity using the authenticated Telegram/Fanvue channel, not a fan-provided prompt.
2. Fetch the correct persona's canonical image references and temporary signed URLs from their separate assets.
3. Submit a generation with a fixed creator value.
4. Record the prediction id alongside creator, fan/conversation id, intended platform, job id, payment state, and content-review state in the existing durable database.
5. Poll until terminal; verify completed outputs and image/video properties.
6. Run character-likeness QA, platform policy checks, and owner approval for premium content.
7. Deliver via the existing Telegram or Fanvue publisher only after authorized payment and approval; deduplicate deliveries to prevent double sends or double charges.
8. Store a media manifest and reconcile actual WaveSpeed charges.

This PR does not publish, invoice, change bot credentials, deduct Stars, or modify live workflows. Enable those actions only after test generations and idempotency checks pass.

## Validation plan

- Configure secrets on preview deployment.
- Seedream text-to-image with a benign prompt for Nicole.
- Seedream edit using Aria's approved canonical reference.
- Wan image-to-video at 480p/2 seconds with a Nicole reference.
- Confirm completed predictions, output URLs, billing, and no cross-character references.
- Test invalid token, missing image, expired asset link, rejected model and failed prediction.
- Connect staging n8n webhooks only after outputs are verified; simulate publishing before enabling live delivery.

## Limitations

The backend intentionally has no fan identity database, concurrent job quota, rate limits, permanent media storage, platform uploader, or automatic paid-delivery logic. These must be completed in the existing agent infrastructure before production. Publicly exposed creator tokens should be rotated if leaked.

Upstream references:
https://wavespeed.ai/models/bytedance/seedream-v5.0-pro
https://wavespeed.ai/models/bytedance/seedream-v5.0-pro/edit
https://wavespeed.ai/models/alibaba/wan-3.0/image-to-video
https://wavespeed.ai/models/alibaba/wan-3.0/reference-to-video

## Canonical character references (preview)

For the creator branch, configure:

- `ARIA_REFERENCE_IMAGE_URL`: Aria's direct public object URL from Supabase Storage `character-assets/IMG_5317.PNG`.
- `NICOLE_REFERENCE_IMAGE_URL`: Nicole's established direct public object URL from Supabase Storage `characters/059b820d86494f16829a1650a1f23b14.jpeg`.

These are deployment environment variables, not code constants. Both belong to Preview and can be updated independently without changing the repository. Do not use Supabase dashboard preview URLs or expiring signed URLs here.

The Creator Studio reads the selected creator's reference from an authenticated metadata endpoint and shows its thumbnail. The server automatically injects that reference into Seedream Edit, Wan image-to-video, and Wan reference-to-video requests when no custom reference image is supplied. Text-to-image and text-to-video modes remain text-only and do not claim to preserve a character's exact likeness.

When switching from Aria to Nicole or back, the page clears custom reference fields immediately. Models accept the current character's reference only, unless the creator deliberately provides an override. For paid or unattended fan workflows, implement a server-side override authorization rule before connecting fans, since a free-form override supplied by an authorized client should not be treated as the creator's canonical identity.

Reference isolation regression tests: `node --test tests/wavespeed-creator.test.mjs tests/creator-references.test.mjs`.
