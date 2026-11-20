import { createHash, timingSafeEqual } from "node:crypto";

export function authenticate(request) {
  const expected = process.env.STUDIO_ACCESS_TOKEN;
  if (!expected || expected.length < 16) return { ok: false, status: 503, error: "STUDIO_ACCESS_TOKEN is not configured (minimum 16 characters)." };
  const header = request.headers.get("authorization") || "";
  const actual = header.startsWith("Bearer ") ? header.slice(7) : "";
  const a = createHash("sha256").update(actual).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b) ? { ok: true } : { ok: false, status: 401, error: "Invalid private studio access token." };
}

export function allowedQueueUrl(value, kind) {
  try {
    const u = new URL(value);
    if (u.origin !== "https://queue.fal.run" || u.search || u.hash || u.username || u.password) return null;
    if (!/^\/[A-Za-z0-9_-]+\/[A-Za-z0-9_/-]+\/requests\/[0-9a-fA-F-]{8,64}(?:\/status)?$/.test(u.pathname)) return null;
    if (kind === "status" && !u.pathname.endsWith("/status")) return null;
    if (kind === "result" && u.pathname.endsWith("/status")) return null;
    return u.toString();
  } catch { return null; }
}

export function validExternalImage(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return false;
    const host = url.hostname.toLowerCase();
    if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) return false;
    if (/^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2[0-9]|3[01])\.)/.test(host)) return false;
    return value.length <= 2048;
  } catch { return false; }
}

export async function falRequest(url, options = {}) {
  if (!process.env.FAL_KEY) return { ok: false, status: 503, data: { detail: "FAL_KEY is missing from Vercel environment variables." } };
  const response = await fetch(url, {
    ...options,
    headers: { "Authorization": "Key " + process.env.FAL_KEY, "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store",
    signal: AbortSignal.timeout(22000)
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

export function publicError(status, data) {
  const message = typeof data?.detail === "string" ? data.detail : typeof data?.error === "string" ? data.error : "Provider request failed";
  return { error: message.slice(0, 500), status };
}
