// Canonical reference media is configured server-side, scoped to each creator.
// Do not store private signed URLs here: they expire and may disclose access tokens.
const REFERENCE_KEYS = Object.freeze({
  aria: "ARIA_REFERENCE_IMAGE_URL",
  nicole: "NICOLE_REFERENCE_IMAGE_URL",
});

export function referenceForCreator(creator, env = process.env) {
  const key = REFERENCE_KEYS[creator];
  if (!key) return null;
  const raw = env[key];
  if (!raw || typeof raw !== "string") return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function applyReferenceDefaults(body, env = process.env) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const url = referenceForCreator(body.creator, env);
  if (!url) return body;

  // Only reference-aware models can preserve the canonical character.
  // Text-only models do not accept reference media.
  switch (body.model) {
    case "seedream-edit":
      return Array.isArray(body.images) && body.images.length
        ? body
        : { ...body, images: [url] };
    case "wan-image-video":
      return body.image ? body : { ...body, image: url };
    case "wan-reference-video":
      return Array.isArray(body.reference_images) && body.reference_images.length
        ? body
        : { ...body, reference_images: [url] };
    default:
      return body;
  }
}
