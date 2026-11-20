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
  const [reachable, ready] = await Promise.all([probe("/healthz"), probe("/readyz")]);
  return NextResponse.json({ reachable, ready, setup: "configured" },
    { headers: { "Cache-Control": "no-store" } });
}
