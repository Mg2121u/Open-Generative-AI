import { NextResponse } from "next/server";
import { authenticate, falRequest, allowedQueueUrl, publicError } from "../../../../lib/creator-studio/server";
export const runtime = "nodejs";
export const maxDuration = 30;

function assetsFromResult(data) {
  const urls = [];
  for (const image of Array.isArray(data?.images) ? data.images : []) if (typeof image?.url === "string") urls.push(image.url);
  if (typeof data?.video?.url === "string") urls.push(data.video.url);
  return urls;
}

export async function POST(request) {
  const auth = authenticate(request);
  if (!auth.ok) return NextResponse.json({ error: auth.error }, { status: auth.status });
  try {
    const body = await request.json();
    if (body.provider === "fal") {
      const statusUrl = allowedQueueUrl(body.statusUrl, "status");
      if (!statusUrl) return NextResponse.json({ error: "Invalid queue status URL." }, { status: 400 });
      const observed = await falRequest(statusUrl);
      if (!observed.ok) return NextResponse.json(publicError(observed.status, observed.data), { status: observed.status });
      if (observed.data.status !== "COMPLETED") return NextResponse.json({ status: observed.data.status || "IN_QUEUE" });
      if (observed.data.error) return NextResponse.json({ status: "ERROR", error: observed.data.error });
      const resultUrl = allowedQueueUrl(observed.data.response_url, "result");
      if (!resultUrl) return NextResponse.json({ status: "ERROR", error: "Invalid provider result URL." });
      const result = await falRequest(resultUrl);
      if (!result.ok) return NextResponse.json(publicError(result.status, result.data), { status: result.status });
      return NextResponse.json({ status: "COMPLETED", assets: assetsFromResult(result.data) });
    }
    if (body.provider === "gpu") {
      const endpoint = process.env.PRIVATE_GPU_ENDPOINT;
      const token = process.env.PRIVATE_GPU_TOKEN;
      if (!endpoint || !token || !endpoint.startsWith("https://")) return NextResponse.json({ error: "GPU worker not configured." }, { status: 503 });
      if (typeof body.id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.id)) return NextResponse.json({ error: "Invalid job ID." }, { status: 400 });
      const response = await fetch(endpoint.replace(/\/$/, "") + "/jobs/" + encodeURIComponent(body.id), {
        headers: { Authorization: "Bearer " + token }, cache: "no-store", signal: AbortSignal.timeout(22000)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : "";
        return NextResponse.json({ error: detail.slice(0, 240) || "GPU job status unavailable (HTTP " + response.status + ")." }, { status: response.status });
      }
      const state = String(data.status || "IN_QUEUE").toUpperCase();
      return NextResponse.json({ status: state, assets: Array.isArray(data.assets) ? data.assets : [], error: data.error || null });
    }
    return NextResponse.json({ error: "Unknown provider." }, { status: 400 });
  } catch { return NextResponse.json({ error: "Unable to check job status." }, { status: 500 }); }
}
