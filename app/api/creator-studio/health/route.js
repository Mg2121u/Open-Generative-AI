import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 20;

// Temporary read-only pilot check. Enabled only on Vercel previews.
// No credentials, private addresses, or service error bodies are returned.
export async function GET() {
  if (process.env.VERCEL_ENV !== "preview") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const base = process.env.PRIVATE_GPU_ENDPOINT;
  if (!base || !base.startsWith("https://")) {
    return NextResponse.json({ reachable: false, ready: false, setup: "missing_endpoint" });
  }
  const probe = async (path) => {
    try {
      const response = await fetch(base.replace(/\/$/, "") + path, {
        cache: "no-store",
        signal: AbortSignal.timeout(7000),
        headers: { "User-Agent": "AriaNicoleStudioHealth/1.0" }
      });
      return response.status === 200;
    } catch {
      return false;
    }
  };
  const [reachable, modelReady, engineMatch] = await Promise.all([
    probe("/healthz"), probe("/readyz"),
    (async () => {
      try {
        const response = await fetch(base.replace(/\/$/, "") + "/model-info", {
          cache: "no-store", signal: AbortSignal.timeout(6000)
        });
        const data = response.ok ? await response.json() : {};
        return data?.engine === "qwen-image-edit-2511" && data?.mode === "image-edit";
      } catch { return false; }
    })()
  ]);
  return NextResponse.json({ reachable, ready: modelReady && engineMatch,
    engine: engineMatch ? "qwen-image-edit-2511" : "not_qwen_worker", setup: "configured" },
    { headers: { "Cache-Control": "no-store" } });
}
