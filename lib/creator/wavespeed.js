// WaveSpeed creator job contract. No API credentials are accepted from the browser or caller.
export const CREATOR_MODELS = Object.freeze({
  "seedream-image": { endpoint: "bytedance/seedream-v5.0-pro", kind: "image" },
  "seedream-edit": { endpoint: "bytedance/seedream-v5.0-pro/edit", kind: "image" },
  "wan-text-video": { endpoint: "alibaba/wan-3.0/text-to-video", kind: "video" },
  "wan-image-video": { endpoint: "alibaba/wan-3.0/image-to-video", kind: "video" },
  "wan-reference-video": { endpoint: "alibaba/wan-3.0/reference-to-video", kind: "video" },
});

const IMAGE_RATIOS = new Set([
  "1:1", "1:2", "2:1", "1:3", "3:1", "2:3", "3:2", "3:4", "4:3",
  "4:5", "5:4", "9:16", "16:9", "9:21", "21:9",
]);
const VIDEO_RATIOS = new Set(["1:1", "3:4", "4:3", "9:16", "16:9"]);
const IMAGE_RESOLUTIONS = new Set(["1k", "1.5k", "2k"]);
const VIDEO_RESOLUTIONS = new Set(["480p", "720p", "1080p"]);

function fromSet(value, allowed, fallback, label) {
  if (value == null || value === "") return fallback;
  if (!allowed.has(value)) throw new Error("Invalid " + label + ".");
  return value;
}

function mediaUrl(value, label) {
  if (typeof value !== "string" || value.length > 4096) {
    throw new Error(label + " must be an HTTPS URL.");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(label + " must be an HTTPS URL.");
  }
  if (url.protocol !== "https:" || url.username || url.password ||
      ["localhost", "127.0.0.1", "::1"].includes(url.hostname) ||
      /(?:^|\.)internal$/.test(url.hostname)) {
    throw new Error(label + " must be a publicly accessible HTTPS URL.");
  }
  return value;
}

function urlArray(value, label, max) {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length > max) {
    throw new Error(label + " must be an array containing at most " + max + " URLs.");
  }
  return value.map((item, index) => mediaUrl(item, label + "[" + index + "]"));
}

function optionalSeed(body, payload) {
  if (body.seed == null || body.seed === "") return;
  const seed = Number(body.seed);
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 2147483647) {
    throw new Error("seed must be an integer from 0 to 2147483647.");
  }
  payload.seed = seed;
}

export function validateCreatorRequest(body, maxVideoSeconds = 10) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new Error("Expected a JSON object.");
  }
  const creator = body.creator;
  if (creator !== "aria" && creator !== "nicole") {
    throw new Error("creator must be aria or nicole.");
  }
  const model = String(body.model || "");
  const config = CREATOR_MODELS[model];
  if (!config) throw new Error("Unsupported creator model.");
  const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
  if (prompt.length < 3 || prompt.length > 6000) {
    throw new Error("Prompt must be between 3 and 6000 characters.");
  }

  const payload = { prompt };
  if (config.kind === "image") {
    payload.aspect_ratio = fromSet(body.aspect_ratio, IMAGE_RATIOS, "9:16", "aspect_ratio");
    payload.resolution = fromSet(body.resolution, IMAGE_RESOLUTIONS, "1k", "resolution");
    payload.output_format = fromSet(body.output_format, new Set(["jpeg", "png"]), "jpeg", "output_format");
    payload.prompt_optimization_mode = fromSet(
      body.prompt_optimization_mode, new Set(["fast", "standard"]), "standard",
      "prompt_optimization_mode"
    );
    if (model === "seedream-edit") {
      payload.images = urlArray(body.images, "images", 10);
      if (payload.images.length < 1) throw new Error("seedream-edit requires images.");
    }
  } else {
    const limit = Math.min(30, Math.max(2, Number(maxVideoSeconds) || 10));
    const duration = body.duration == null ? 5 : Number(body.duration);
    if (!Number.isInteger(duration) || duration < 2 || duration > limit) {
      throw new Error("Video duration must be between 2 and " + limit + " seconds.");
    }
    payload.duration = duration;
    payload.resolution = fromSet(body.resolution, VIDEO_RESOLUTIONS, "720p", "resolution");
    if (model !== "wan-image-video" || body.aspect_ratio) {
      payload.aspect_ratio = fromSet(body.aspect_ratio, VIDEO_RATIOS, "9:16", "aspect_ratio");
    }
    payload.enable_prompt_expansion = body.enable_prompt_expansion === true;
    payload.generate_audio = body.generate_audio !== false;
    optionalSeed(body, payload);
    if (model === "wan-image-video") {
      payload.image = mediaUrl(body.image, "image");
      if (body.last_image) payload.last_image = mediaUrl(body.last_image, "last_image");
    }
    if (model === "wan-reference-video") {
      const images = urlArray(body.reference_images, "reference_images", 10);
      const videos = urlArray(body.reference_videos, "reference_videos", 5);
      const audios = urlArray(body.reference_audios, "reference_audios", 5);
      if (!images.length && !videos.length && !audios.length) {
        throw new Error("wan-reference-video requires reference media.");
      }
      if (images.length) payload.reference_images = images;
      if (videos.length) payload.reference_videos = videos;
      if (audios.length) payload.reference_audios = audios;
    }
  }
  return { creator, model, kind: config.kind, endpoint: config.endpoint, payload };
}

export async function wavespeedRequest(apiKey, endpoint, payload) {
  const url = "https://api.wavespeed.ai/api/v3/" + endpoint;
  const options = {
    method: payload ? "POST" : "GET",
    headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(20000),
  };
  if (payload) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(url, options);
  let responseBody = null;
  try {
    responseBody = await response.json();
  } catch {
    throw new Error("WaveSpeed returned an invalid response (HTTP " + response.status + ").");
  }
  if (!response.ok) {
    const message = String(responseBody?.message || responseBody?.error || "Request failed").slice(0, 300);
    throw new Error("WaveSpeed error (" + response.status + "): " + message);
  }
  return responseBody?.data ?? responseBody;
}
