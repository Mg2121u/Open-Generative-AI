import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// Import pure ESM source without changing the enclosing Next.js package mode.
const source = await readFile(new URL("../lib/creator/references.js", import.meta.url), "utf8");
const { applyReferenceDefaults, referenceForCreator } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const testEnv = {
  ARIA_REFERENCE_IMAGE_URL: "https://example.com/aria.png",
  NICOLE_REFERENCE_IMAGE_URL: "https://example.com/nicole.jpg",
};

test("resolves separate creator-specific HTTPS references", () => {
  assert.equal(referenceForCreator("aria", testEnv), testEnv.ARIA_REFERENCE_IMAGE_URL);
  assert.equal(referenceForCreator("nicole", testEnv), testEnv.NICOLE_REFERENCE_IMAGE_URL);
  assert.equal(referenceForCreator("unknown", testEnv), null);
});

test("rejects non-HTTPS and credential-bearing reference values", () => {
  assert.equal(referenceForCreator("aria", { ARIA_REFERENCE_IMAGE_URL: "http://example.com/a.png" }), null);
  assert.equal(referenceForCreator("nicole", { NICOLE_REFERENCE_IMAGE_URL: "https://user:pass@example.com/a.png" }), null);
});

test("auto-injects correct creator URL for Seedream Edit", () => {
  const nicole = applyReferenceDefaults({
    creator: "nicole", model: "seedream-edit", prompt: "Portrait", images: []
  }, testEnv);
  assert.deepEqual(nicole.images, [testEnv.NICOLE_REFERENCE_IMAGE_URL]);
  const aria = applyReferenceDefaults({
    creator: "aria", model: "seedream-edit", prompt: "Portrait"
  }, testEnv);
  assert.deepEqual(aria.images, [testEnv.ARIA_REFERENCE_IMAGE_URL]);
});

test("automatically supplies Wan image-to-video and reference-to-video inputs", () => {
  const i2v = applyReferenceDefaults({
    creator: "aria", model: "wan-image-video", prompt: "A smile"
  }, testEnv);
  assert.equal(i2v.image, testEnv.ARIA_REFERENCE_IMAGE_URL);
  const r2v = applyReferenceDefaults({
    creator: "nicole", model: "wan-reference-video",
    prompt: "Walking", reference_videos: ["https://example.com/clip.mp4"]
  }, testEnv);
  assert.deepEqual(r2v.reference_images, [testEnv.NICOLE_REFERENCE_IMAGE_URL]);
  assert.deepEqual(r2v.reference_videos, ["https://example.com/clip.mp4"]);
});

test("never overwrites an explicitly chosen reference image", () => {
  const custom = applyReferenceDefaults({
    creator: "nicole", model: "wan-image-video",
    image: "https://example.com/custom.png", prompt: "A smile"
  }, testEnv);
  assert.equal(custom.image, "https://example.com/custom.png");
});

test("does not insert references into text-only models", () => {
  for (const model of ["seedream-image", "wan-text-video"]) {
    const job = { creator: "aria", model, prompt: "An editorial scene" };
    assert.deepEqual(applyReferenceDefaults(job, testEnv), job);
  }
});
