import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// The Next.js project is not a "type": "module" package; import a data URL
// so this test can exercise the pure ESM validation module without Next.
const source = await readFile(new URL("../lib/creator/wavespeed.js", import.meta.url), "utf8");
const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const { validateCreatorRequest, wavespeedRequest } = await import(moduleUrl);

test("Nicole Seedream edit creates the documented WaveSpeed payload", () => {
  const job = validateCreatorRequest({
    creator: "nicole", model: "seedream-edit", prompt: "Photographic evening portrait",
    images: ["https://example.org/nicole.jpg"], resolution: "2k"
  });
  assert.equal(job.endpoint, "bytedance/seedream-v5.0-pro/edit");
  assert.equal(job.payload.resolution, "2k");
  assert.deepEqual(job.payload.images, ["https://example.org/nicole.jpg"]);
});

test("Aria Wan video routes to image-to-video and defaults to 720p", () => {
  const job = validateCreatorRequest({
    creator: "aria", model: "wan-image-video",
    prompt: "Slow camera pan and relaxed expression",
    image: "https://example.org/aria.png", duration: 5, generate_audio: false
  });
  assert.equal(job.endpoint, "alibaba/wan-3.0/image-to-video");
  assert.equal(job.payload.image, "https://example.org/aria.png");
  assert.equal(job.payload.generate_audio, false);
  assert.equal(job.payload.resolution, "720p");
});

test("Reject unknown creator or arbitrary API model", () => {
  assert.throws(() => validateCreatorRequest({
    creator: "other", model: "seedream-image", prompt: "A landscape"
  }));
  assert.throws(() => validateCreatorRequest({
    creator: "nicole", model: "arbitrary/endpoint", prompt: "A landscape"
  }));
});

test("Reject expensive durations exceeding configured cap and missing references", () => {
  assert.throws(() => validateCreatorRequest({
    creator: "aria", model: "wan-text-video", prompt: "Walking through Beirut",
    duration: 30
  }));
  assert.throws(() => validateCreatorRequest({
    creator: "nicole", model: "wan-reference-video",
    prompt: "Natural camera motion", duration: 5
  }));
  assert.throws(() => validateCreatorRequest({
    creator: "nicole", model: "seedream-edit",
    prompt: "Change the outfit", images: ["http://localhost/internal"]
  }));
});

test("WaveSpeed submission unwraps data without leaking credentials", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, "https://api.wavespeed.ai/api/v3/bytedance/seedream-v5.0-pro");
      assert.equal(options.headers.Authorization, "Bearer secret-test-key");
      return { ok: true, json: async () => ({ data: { id: "abc-123", status: "processing" } }) };
    };
    const result = await wavespeedRequest("secret-test-key", "bytedance/seedream-v5.0-pro", {
      prompt: "A still life"
    });
    assert.equal(result.id, "abc-123");
  } finally {
    globalThis.fetch = previous;
  }
});
