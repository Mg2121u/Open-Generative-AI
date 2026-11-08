import { NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { validateCreatorRequest, wavespeedRequest } from "@/lib/creator/wavespeed";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const reply = (body, status = 200) => NextResponse.json(body, {
  status, headers: { "Cache-Control": "no-store" }
});
const configured = () => Boolean(process.env.WAVESPEED_API_KEY && process.env.CREATOR_API_TOKEN);

function hasAccess(request) {
  const expected = process.env.CREATOR_API_TOKEN || "";
  const header = request.headers.get("authorization") || "";
  const submitted = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!expected || !submitted) return false;
  const a = Buffer.from(submitted);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

const errorText = error => typeof error?.message === "string"
  ? error.message.slice(0, 350) : "Request failed.";
const errorStatus = error => error instanceof SyntaxError ? 400
  : error?.name === "TimeoutError" ? 504
  : String(error?.message).startsWith("WaveSpeed") ? 502 : 400;

export async function POST(request) {
  if (!configured()) return reply({ error: "Creator service is not configured." }, 503);
  if (!hasAccess(request)) return reply({ error: "Unauthorized." }, 401);
  try {
    const maxSeconds = Number(process.env.CREATOR_MAX_VIDEO_SECONDS || 10);
    const job = validateCreatorRequest(await request.json(), maxSeconds);
    const result = await wavespeedRequest(
      process.env.WAVESPEED_API_KEY, job.endpoint, job.payload
    );
    const id = result?.id || result?.request_id;
    if (!id || typeof id !== "string") {
      return reply({ error: "WaveSpeed did not return a prediction ID." }, 502);
    }
    return reply({
      id, creator: job.creator, model: job.model, kind: job.kind,
      status: result?.status || "submitted", publish_status: "not_published"
    }, 202);
  } catch (error) {
    return reply({ error: errorText(error) }, errorStatus(error));
  }
}

export async function GET(request) {
  if (!configured()) return reply({ error: "Creator service is not configured." }, 503);
  if (!hasAccess(request)) return reply({ error: "Unauthorized." }, 401);
  const id = new URL(request.url).searchParams.get("id") || "";
  if (!/^[a-zA-Z0-9_-]{8,128}$/.test(id)) return reply({ error: "Invalid prediction ID." }, 400);
  try {
    const result = await wavespeedRequest(
      process.env.WAVESPEED_API_KEY,
      "predictions/" + encodeURIComponent(id) + "/result"
    );
    return reply({
      id, status: result?.status || "unknown",
      outputs: Array.isArray(result?.outputs) ? result.outputs : [],
      error: result?.error || null, publish_status: "not_published"
    });
  } catch (error) {
    return reply({ error: errorText(error) }, errorStatus(error));
  }
}
