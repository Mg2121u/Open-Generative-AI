import { NextResponse } from "next/server";
import { authenticate, falRequest, publicError, validExternalImage } from "../../../../lib/creator-studio/server";
export const runtime = "nodejs";
export const maxDuration = 60;

const IMAGE_MODELS = new Set(["fal-ai/qwen-image-2512", "fal-ai/qwen-image-edit-2511"]);
const VIDEO_MODEL = "alibaba/wan-3.0/image-to-video";
const WAN_EDIT_MODEL = "alibaba/wan-2.5/image-edit";
const WAVESPEED_BASE = "https://api.wavespeed.ai/api/v3";
const WAVESPEED_ID = /^[A-Za-z0-9_-]{1,128}$/;

async function uploadWanReference(dataUri, role, apiKey) {
  const bytes = Buffer.from(dataUri.split(",")[1], "base64");
  if (!bytes.length || bytes.length > 1_350_000) throw new Error("Reference exceeds 1.35 MB.");
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: "image/jpeg" }), role + ".jpg");
  const response = await fetch(WAVESPEED_BASE + "/media/upload/binary", {
    method: "POST", headers: { Authorization: "Bearer " + apiKey },
    body: form, signal: AbortSignal.timeout(15000)
  });
  const result = await response.json().catch(() => ({}));
  const url = result?.data?.download_url || result?.data?.url;
  if (!response.ok || result.code !== 200 || typeof url !== "string" || !url.startsWith("https://")) {
    throw new Error("WaveSpeed reference upload failed (HTTP " + response.status + ").");
  }
  return url;
}

export async function POST(request) {
  const auth = authenticate(request);
  if (!auth.ok) return NextResponse.json({ error: auth.error }, { status: auth.status });
  try {
    const body = await request.json();
    const { provider, character, mode, model, prompt, referenceUrl, referenceImages, placement, ratio, duration } = body;
    if (!["Aria", "Nicole"].includes(character) || !["image", "video"].includes(mode) || typeof prompt !== "string" || prompt.length > 4000) {
      return NextResponse.json({ error: "Invalid character, mode or prompt." }, { status: 400 });
    }
    if (!["fal", "gpu", "wavespeed"].includes(provider)) return NextResponse.json({ error: "Unknown provider." }, { status: 400 });
    if (mode === "image" && provider === "fal" && !IMAGE_MODELS.has(model)) return NextResponse.json({ error: "Unsupported Qwen image model." }, { status: 400 });
    if (mode === "image" && provider === "gpu" && model !== "selfhost/qwen-image-edit-2511-q4") return NextResponse.json({ error: "Unsupported GPU image model." }, { status: 400 });
    if (provider === "wavespeed" && (mode !== "image" || model !== WAN_EDIT_MODEL)) return NextResponse.json({ error: "WaveSpeed Wan 2.5 Edit supports image editing only." }, { status: 422 });
    if (mode === "video" && provider === "gpu") return NextResponse.json({ error: "GPU video generation is not enabled in pilot." }, { status: 422 });
    if (mode === "video" && model !== VIDEO_MODEL) return NextResponse.json({ error: "Unsupported video model." }, { status: 400 });
    if (mode === "video" && !validExternalImage(referenceUrl)) return NextResponse.json({ error: "An HTTPS start-frame URL is required." }, { status: 400 });
    if (referenceUrl && provider === "fal" && !validExternalImage(referenceUrl)) return NextResponse.json({ error: "Reference image URL must be a public HTTPS URL without credentials." }, { status: 400 });
    const privateRefs = referenceImages || {};
    if (typeof privateRefs !== "object" || Array.isArray(privateRefs) || !privateRefs ||
        Object.keys(privateRefs).length > 2 || Object.keys(privateRefs).some((key) => !["identity", "scenery"].includes(key)) ||
        Object.values(privateRefs).some((value) => typeof value !== "string" ||
            !/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value) || value.length > 1_850_000)) {
      return NextResponse.json({ error: "Invalid reference upload. Use up to two compact JPEG images." }, { status: 422 });
    }
    if ((provider === "gpu" || provider === "wavespeed") && placement !== undefined &&
        !["foreground_floor", "rear_floor", "sofa_seated"].includes(placement)) {
      return NextResponse.json({ error: "Unknown subject placement." }, { status: 422 });
    }
    if (mode === "image" && (provider === "gpu" || provider === "wavespeed") && !Object.keys(privateRefs).length) {
      return NextResponse.json({ error: "Image editing needs at least one identity or scenery photo." }, { status: 422 });
    }
    if (mode === "image" && provider === "fal" && model === "fal-ai/qwen-image-edit-2511" && !validExternalImage(referenceUrl)) {
      return NextResponse.json({ error: "Hosted Qwen Edit requires an HTTPS reference image URL." }, { status: 422 });
    }
    if (provider === "fal" && Object.keys(privateRefs).length) {
      return NextResponse.json({ error: "Private reference uploads are available on the independent GPU only." }, { status: 422 });
    }
    const ratios = { portrait_16_9: "9:16", square_hd: "1:1", landscape_16_9: "16:9" };
    if (!(ratio in ratios)) return NextResponse.json({ error: "Invalid aspect ratio." }, { status: 400 });
    if (mode === "video" && ![5, 10, 15].includes(duration)) return NextResponse.json({ error: "Unsupported duration." }, { status: 400 });
    if (provider === "wavespeed") {
      const apiKey = process.env.WAVESPEED_API_KEY;
      if (!apiKey) return NextResponse.json({ error: "WaveSpeed API key is not configured on the Studio preview branch." }, { status: 503 });
      const roles = ["identity", "scenery"].filter((role) => !!privateRefs[role]);
      const inputImages = [];
      try {
        for (const role of roles) inputImages.push(await uploadWanReference(privateRefs[role], role, apiKey));
      } catch (error) {
        return NextResponse.json({ error: error.message || "Could not upload reference to WaveSpeedAI." }, { status: 502 });
      }
      const roleGuidance = roles.length === 2
        ? "Image 1 shows the character to preserve. Image 2 is the scenery and camera perspective to preserve. Place the character on the actual floor, at natural human scale, with realistic leg proportions, floor contact and correct furniture occlusion. "
        : roles[0] === "identity" ? "Image 1 is the character identity reference. Preserve realistic facial identity, natural body proportions and anatomy. "
          : "Image 1 is the scenery reference. Preserve the camera angle and architecture. ";
      const place = placement === "sofa_seated" ? "Seat the character naturally on the sofa. " :
        placement === "rear_floor" ? "Place the character on the rear floor behind the furniture. " :
        "Place the character on the visible foreground floor, not on the coffee table. ";
      const sizes = { portrait_16_9: "720*1280", square_hd: "1024*1024", landscape_16_9: "1280*720" };
      let response;
      try {
        response = await fetch(WAVESPEED_BASE + "/alibaba/wan-2.5/image-edit", {
          method: "POST",
          headers: { Authorization: "Bearer " + apiKey, "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: roleGuidance + (roles.length === 2 ? place : "") + prompt,
            images: inputImages, size: sizes[ratio], seed: -1 }),
          signal: AbortSignal.timeout(22000)
        });
      } catch {
        return NextResponse.json({ error: "Wan submission outcome is uncertain; check WaveSpeed History before retrying to avoid duplicate charges." }, { status: 502 });
      }
      const result = await response.json().catch(() => ({}));
      const task = result?.data || result;
      if (!response.ok || !WAVESPEED_ID.test(String(task.id || ""))) {
        const detail = response.ok ? "Provider did not return a valid task ID." :
          "Wan submission returned HTTP " + response.status + ".";
        return NextResponse.json({ error: detail + (response.status >= 500 ? " Check WaveSpeed History before trying again." : "") }, { status: response.status || 502 });
      }
      return NextResponse.json({ id: task.id, provider: "wavespeed" });
    }
    if (provider === "fal") {
      const input = mode === "image"
        ? model === "fal-ai/qwen-image-edit-2511"
          ? { prompt, image_urls: [referenceUrl], image_size: ratio, num_images: 1, enable_safety_checker: true }
          : { prompt, image_size: ratio, num_images: 1, enable_safety_checker: true }
        : { prompt, start_image_url: referenceUrl, resolution: "480p", aspect_ratio: ratios[ratio], duration, audio: false, enable_safety_checker: true };
      const response = await falRequest("https://queue.fal.run/" + model, { method: "POST", body: JSON.stringify(input) });
      if (!response.ok) return NextResponse.json(publicError(response.status, response.data), { status: response.status });
      if (!response.data.request_id || !response.data.status_url) return NextResponse.json({ error: "Provider did not return queue identifiers." }, { status: 502 });
      return NextResponse.json({ id: response.data.request_id, provider, statusUrl: response.data.status_url });
    }
    const endpoint = process.env.PRIVATE_GPU_ENDPOINT;
    const token = process.env.PRIVATE_GPU_TOKEN;
    if (!endpoint || !token || !endpoint.startsWith("https://")) {
      return NextResponse.json({ error: "Independent GPU worker is not configured. Set PRIVATE_GPU_ENDPOINT and PRIVATE_GPU_TOKEN only after provider approval." }, { status: 503 });
    }
    // Never submit a billable job to a machine whose model is still initializing
    // or whose initialization failed.
    let health;
    try {
      health = await fetch(endpoint.replace(/\/$/, "") + "/readyz", {
        method: "GET", cache: "no-store", signal: AbortSignal.timeout(6000)
      });
    } catch {
      return NextResponse.json({ error: "Salad worker is not reachable. Check whether its instance is running." }, { status: 503 });
    }
    if (health.status !== 200) {
      return NextResponse.json({ error: health.status === 503
        ? "Salad server is online, but Qwen model initialization is not complete or failed. Check the latest Salad Container Logs before generating."
        : "Salad GPU is not ready (health status " + health.status + ")." }, { status: 503 });
    }
    let modelInfo;
    try {
      const response = await fetch(endpoint.replace(/\/$/, "") + "/model-info", {
        method: "GET", cache: "no-store", signal: AbortSignal.timeout(6000)
      });
      modelInfo = response.ok ? await response.json() : {};
    } catch {
      modelInfo = {};
    }
    if (modelInfo?.engine !== "qwen-image-edit-2511" || modelInfo?.mode !== "image-edit") {
      return NextResponse.json({ error: "Salad still runs the previous Docker image. Install the verified Qwen-Image-Edit-2511 image before submitting jobs." }, { status: 503 });
    }
    const result = await fetch(endpoint.replace(/\/$/, "") + "/jobs", {
      method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify({ character, mode, prompt, reference_image_url: null, reference_images: privateRefs, placement: placement || "foreground_floor", aspect_ratio: ratios[ratio], duration: mode === "video" ? duration : undefined }),
      cache: "no-store", signal: AbortSignal.timeout(22000)
    });
    const data = await result.json().catch(() => ({}));
    if (!result.ok) {
      const detail = typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : "";
      const safe = detail.slice(0, 240);
      const message = result.status === 422 && /Phase one supports text-to-image only|reference and video coming later/i.test(safe)
        ? "Salad is running the older text-to-image Docker version. Update the Salad container image to the reference-enabled version before generating."
        : result.status === 401
        ? "Salad rejected the worker credential (401). Verify its configured token matches PRIVATE_GPU_TOKEN."
        : result.status === 503
        ? "Salad GPU model or storage is not ready (503). Check the latest Container Logs."
        : safe || "Private GPU rejected the request (HTTP " + result.status + ").";
      return NextResponse.json({ error: message }, { status: result.status });
    }
    if (typeof data.id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(data.id)) return NextResponse.json({ error: "Invalid private GPU job identifier." }, { status: 502 });
    return NextResponse.json({ id: data.id, provider });
  } catch (error) {
    return NextResponse.json({ error: error.name === "TimeoutError" ? "Provider request timed out." : "Unable to queue generation." }, { status: 500 });
  }
}
