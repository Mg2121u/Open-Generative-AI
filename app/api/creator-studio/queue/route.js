import { NextResponse } from "next/server";
import { authenticate, falRequest, publicError, validExternalImage } from "../../../../lib/creator-studio/server";
export const runtime = "nodejs";
export const maxDuration = 30;

const IMAGE_MODELS = new Set(["fal-ai/flux-2", "fal-ai/flux-2/klein/9b"]);
const VIDEO_MODEL = "alibaba/wan-3.0/image-to-video";

export async function POST(request) {
  const auth = authenticate(request);
  if (!auth.ok) return NextResponse.json({ error: auth.error }, { status: auth.status });
  try {
    const body = await request.json();
    const { provider, character, mode, model, prompt, referenceUrl, ratio, duration } = body;
    if (!["Aria", "Nicole"].includes(character) || !["image", "video"].includes(mode) || typeof prompt !== "string" || prompt.length > 4000) {
      return NextResponse.json({ error: "Invalid character, mode or prompt." }, { status: 400 });
    }
    if (!["fal", "gpu"].includes(provider)) return NextResponse.json({ error: "Unknown provider." }, { status: 400 });
    if (mode === "image" && provider === "fal" && !IMAGE_MODELS.has(model)) return NextResponse.json({ error: "Unsupported fal image model." }, { status: 400 });
    if (mode === "image" && provider === "gpu" && model !== "selfhost/flux2-klein-4b") return NextResponse.json({ error: "Unsupported GPU image model." }, { status: 400 });
    if (mode === "video" && provider === "gpu") return NextResponse.json({ error: "GPU video generation is not enabled in pilot." }, { status: 422 });
    if (mode === "video" && model !== VIDEO_MODEL) return NextResponse.json({ error: "Unsupported video model." }, { status: 400 });
    if (mode === "video" && !validExternalImage(referenceUrl)) return NextResponse.json({ error: "An HTTPS start-frame URL is required." }, { status: 400 });
    if (referenceUrl && !validExternalImage(referenceUrl)) return NextResponse.json({ error: "Reference image URL must be a public HTTPS URL without credentials." }, { status: 400 });
    const ratios = { portrait_16_9: "9:16", square_hd: "1:1", landscape_16_9: "16:9" };
    if (!(ratio in ratios)) return NextResponse.json({ error: "Invalid aspect ratio." }, { status: 400 });
    if (mode === "video" && ![5, 10, 15].includes(duration)) return NextResponse.json({ error: "Unsupported duration." }, { status: 400 });
    if (provider === "fal") {
      const input = mode === "image"
        ? { prompt, image_size: ratio, num_images: 1, enable_safety_checker: true }
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
    const result = await fetch(endpoint.replace(/\/$/, "") + "/jobs", {
      method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: JSON.stringify({ character, mode, prompt, reference_image_url: referenceUrl || null, aspect_ratio: ratios[ratio], duration: mode === "video" ? duration : undefined }),
      cache: "no-store", signal: AbortSignal.timeout(22000)
    });
    const data = await result.json().catch(() => ({}));
    if (!result.ok) return NextResponse.json({ error: String(data.error || "Private GPU rejected the request").slice(0, 300) }, { status: result.status });
    if (typeof data.id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(data.id)) return NextResponse.json({ error: "Invalid private GPU job identifier." }, { status: 502 });
    return NextResponse.json({ id: data.id, provider });
  } catch (error) {
    return NextResponse.json({ error: error.name === "TimeoutError" ? "Provider request timed out." : "Unable to queue generation." }, { status: 500 });
  }
}
