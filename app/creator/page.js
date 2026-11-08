"use client";

import { useEffect, useState } from "react";

const MODELS = [
  ["seedream-image", "Seedream 5.0 Pro — Image"],
  ["seedream-edit", "Seedream 5.0 Pro — Reference Edit"],
  ["wan-text-video", "Wan 3.0 — Text to Video"],
  ["wan-image-video", "Wan 3.0 — Image to Video"],
  ["wan-reference-video", "Wan 3.0 — Reference to Video"],
];
const terminal = new Set(["completed", "failed", "cancelled", "timeout", "deleted"]);
const split = value => value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);

async function readJson(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed (" + response.status + ").");
  return data;
}

export default function CreatorStudio() {
  const [token, setToken] = useState("");
  const [creator, setCreator] = useState("nicole");
  const [model, setModel] = useState("seedream-edit");
  const [prompt, setPrompt] = useState("");
  const [reference, setReference] = useState("");
  const [lastImage, setLastImage] = useState("");
  const [videos, setVideos] = useState("");
  const [audios, setAudios] = useState("");
  const [aspect, setAspect] = useState("9:16");
  const [resolution, setResolution] = useState("1k");
  const [duration, setDuration] = useState("5");
  const [audio, setAudio] = useState(true);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [storedReference, setStoredReference] = useState(null);
  const [referenceStatus, setReferenceStatus] = useState("");

  useEffect(() => {
    const secret = token.trim();
    setStoredReference(null);
    if (secret.length < 20) {
      setReferenceStatus("");
      return;
    }
    let inactive = false;
    const controller = new AbortController();
    setReferenceStatus("Checking saved reference…");
    const timeout = setTimeout(async () => {
      try {
        const response = await fetch("/api/creator/jobs?reference=1&creator=" + creator, {
          headers: { Authorization: "Bearer " + secret },
          cache: "no-store",
          signal: controller.signal
        });
        const data = await readJson(response);
        if (!inactive) {
          setStoredReference(data.reference_url || null);
          setReferenceStatus(data.configured ? "Saved reference ready" : "No saved reference configured");
        }
      } catch (problem) {
        if (!inactive) {
          setReferenceStatus("Reference unavailable: " + (problem.message || "request failed"));
        }
      }
    }, 450);
    return () => {
      inactive = true;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [creator, token]);

  const isVideo = model.startsWith("wan-");
  const needsImages = model === "seedream-edit" || model === "wan-image-video" || model === "wan-reference-video";

  async function generate(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setJob(null);
    try {
      const body = {
        creator, model, prompt, aspect_ratio: aspect,
        resolution: isVideo ? (resolution.endsWith("p") ? resolution : "720p")
          : (resolution.endsWith("k") ? resolution : "1k"),
      };
      if (isVideo) {
        body.duration = Number(duration);
        body.generate_audio = audio;
      }
      if (model === "seedream-edit") body.images = split(reference);
      if (model === "wan-image-video") {
        body.image = reference.trim();
        if (lastImage.trim()) body.last_image = lastImage.trim();
      }
      if (model === "wan-reference-video") {
        body.reference_images = split(reference);
        body.reference_videos = split(videos);
        body.reference_audios = split(audios);
      }
      const headers = {
        "Content-Type": "application/json",
        Authorization: "Bearer " + token.trim()
      };
      const submission = await readJson(await fetch("/api/creator/jobs", {
        method: "POST", headers, body: JSON.stringify(body)
      }));
      setJob(submission);
      for (let attempt = 0; attempt < 180; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 4000));
        const status = await readJson(await fetch(
          "/api/creator/jobs?id=" + encodeURIComponent(submission.id),
          { headers: { Authorization: headers.Authorization }, cache: "no-store" }
        ));
        setJob({ ...submission, ...status });
        if (terminal.has(status.status)) {
          if (status.status !== "completed") {
            setError("Generation stopped: " + (status.error || status.status));
          }
          return;
        }
      }
      setError("Polling timed out; the job may still be running. Save the prediction ID.");
    } catch (problem) {
      setError(problem.message || "Generation failed.");
    } finally {
      setBusy(false);
    }
  }

  const field = {
    width: "100%", padding: "10px 12px", borderRadius: 9,
    background: "#172033", color: "#f8fafc", border: "1px solid #46526b",
    fontSize: 14
  };
  const label = { display: "grid", gap: 6, fontSize: 13, color: "#cbd5e1" };
  const results = job?.outputs || [];

  return (
    <main style={{ minHeight: "100vh", background: "#0b1020", color: "#f8fafc", padding: "28px 18px" }}>
      <div style={{ maxWidth: 820, margin: "0 auto" }}>
        <a href="/studio" style={{ color: "#a5b4fc" }}>← Main studio</a>
        <h1 style={{ fontSize: 30, fontWeight: 700, margin: "20px 0 6px" }}>Aria + Nicole Creator Studio</h1>
        <p style={{ color: "#94a3b8", marginBottom: 22 }}>
          WaveSpeed-powered Seedream 5.0 Pro images and Wan 3.0 videos.
          Jobs stay in preview mode; publishing is not enabled.
        </p>
        <form onSubmit={generate} style={{ display: "grid", gap: 18 }}>
          <label style={label}>
            Creator access token (kept only in this page's memory)
            <input style={field} type="password" autoComplete="off" required value={token}
              onChange={event => setToken(event.target.value)} />
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 14 }}>
            <label style={label}>Character
              <select style={field} value={creator} onChange={event => { setCreator(event.target.value); setReference(""); setLastImage(""); setVideos(""); setAudios(""); setJob(null); setStoredReference(null); }}>
                <option value="aria">Aria</option>
                <option value="nicole">Nicole</option>
              </select>
            </label>
            <label style={label}>Model
              <select style={field} value={model} onChange={event => {
                const next = event.target.value;
                setModel(next);
                setResolution(next.startsWith("wan-") ? "720p" : "1k");
              }}>
                {MODELS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </label>
          </div>
          <section aria-label="Saved character reference" style={{
            padding: 14, borderRadius: 12, border: "1px solid #46526b",
            background: "#111a2b"
          }}>
            <div style={{ color: "#cbd5e1", fontWeight: 700, marginBottom: 10 }}>
              {creator === "aria" ? "Aria" : "Nicole"} — automatic reference
            </div>
            {storedReference ? (
              <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
                <img src={storedReference} alt={creator + " canonical reference"} style={{
                  width: 94, height: 120, borderRadius: 10, objectFit: "cover",
                  border: "1px solid #64748b"
                }} />
                <div style={{ flex: 1, minWidth: 160 }}>
                  <p style={{ marginBottom: 8, color: "#5eead4" }}>Saved reference ready</p>
                  <p style={{ fontSize: 13, color: "#cbd5e1" }}>
                    Automatically added to Seedream Edit and Wan image/reference-to-video.
                    No need to paste the URL.
                  </p>
                  <a href={storedReference} target="_blank" rel="noreferrer" style={{
                    color: "#a5b4fc", fontSize: 12
                  }}>Open reference photo</a>
                </div>
              </div>
            ) : (
              <p style={{ color: "#94a3b8", fontSize: 13 }}>{referenceStatus ||
                "Enter your creator access token to display the saved reference."}</p>
            )}
            {(model === "seedream-image" || model === "wan-text-video") && (
              <p style={{ color: "#fcd34d", fontSize: 12, marginTop: 10 }}>
                This text-only model cannot use the identity reference.
                Select Seedream Reference Edit or Wan Image to Video for likeness consistency.
              </p>
            )}
          </section>
          <label style={label}>Prompt
            <textarea style={{ ...field, minHeight: 126 }} required value={prompt}
              placeholder="Describe the scene, wardrobe, lighting, motion and composition."
              onChange={event => setPrompt(event.target.value)} />
          </label>
          {needsImages && (
            <label style={label}>
              {model === "wan-image-video" ? "Optional start-frame URL override" : "Optional reference image URL override(s) — one per line"}
              <textarea style={{ ...field, minHeight: 72 }} value={reference}
                placeholder="Leave blank to use the saved character image automatically"
                onChange={event => setReference(event.target.value)} />
            </label>
          )}
          {model === "wan-image-video" && (
            <label style={label}>Optional final-frame HTTPS image URL
              <input style={field} value={lastImage} onChange={event => setLastImage(event.target.value)} />
            </label>
          )}
          {model === "wan-reference-video" && (
            <>
              <label style={label}>Reference video HTTPS URLs (one per line)
                <textarea style={field} value={videos} onChange={event => setVideos(event.target.value)} />
              </label>
              <label style={label}>Reference audio HTTPS URLs (one per line)
                <textarea style={field} value={audios} onChange={event => setAudios(event.target.value)} />
              </label>
            </>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 12 }}>
            <label style={label}>Aspect ratio
              <select style={field} value={aspect} onChange={event => setAspect(event.target.value)}>
                {["9:16", "16:9", "1:1", "3:4", "4:3"].map(v => <option key={v}>{v}</option>)}
              </select>
            </label>
            <label style={label}>Resolution
              <select style={field} value={resolution} onChange={event => setResolution(event.target.value)}>
                {(isVideo ? ["480p", "720p", "1080p"] : ["1k", "1.5k", "2k"])
                  .map(v => <option key={v}>{v}</option>)}
              </select>
            </label>
            {isVideo && (
              <label style={label}>Duration (max 10s by default)
                <input style={field} type="number" min="2" max="30" value={duration}
                  onChange={event => setDuration(event.target.value)} />
              </label>
            )}
          </div>
          {isVideo && (
            <label style={{ ...label, display: "flex", alignItems: "center", gap: 10 }}>
              <input type="checkbox" checked={audio} onChange={event => setAudio(event.target.checked)} />
              Generate audio
            </label>
          )}
          <button disabled={busy} type="submit" style={{
            background: busy ? "#525d76" : "#4f46e5", border: 0, borderRadius: 10,
            padding: "13px 18px", fontWeight: 700, color: "white", cursor: busy ? "wait" : "pointer"
          }}>{busy ? "Generating…" : "Submit generation"}</button>
        </form>

        {error && <p role="alert" style={{ color: "#fca5a5", marginTop: 18 }}>{error}</p>}
        {job && (
          <section style={{ marginTop: 24, border: "1px solid #334155", padding: 18, borderRadius: 14 }}>
            <h2 style={{ fontSize: 18, fontWeight: 700 }}>Generation preview</h2>
            <p style={{ margin: "12px 0", color: "#94a3b8", overflowWrap: "anywhere" }}>
              {job.creator} · {job.model} · {job.status} · {job.id}
            </p>
            {results.map((url, index) => (
              <div key={index} style={{ marginTop: 14 }}>
                {job.kind === "image" ? (
                  <img src={url} alt="Generated preview" style={{ maxWidth: "100%", maxHeight: 620, borderRadius: 10 }} />
                ) : (
                  <video src={url} controls playsInline style={{ maxWidth: "100%", maxHeight: 620, borderRadius: 10 }} />
                )}
                <p><a href={url} target="_blank" rel="noreferrer" style={{ color: "#a5b4fc" }}>
                  Open original media
                </a></p>
              </div>
            ))}
            <small style={{ color: "#94a3b8" }}>No Telegram or Fanvue post is triggered by this page.</small>
          </section>
        )}
      </div>
    </main>
  );
}
