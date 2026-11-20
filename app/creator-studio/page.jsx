"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./studio.module.css";

const characters = {
  Nicole: { headline: "Beirut after dark", hint: "Photorealistic, expressive, consistent appearance" },
  Aria: { headline: "Mediterranean muse", hint: "Editorial, cinematic, natural skin texture" }
};
const imageModels = [
  { id: "fal-ai/qwen-image-2512", name: "Qwen-Image 2512 · Text to image" },
  { id: "fal-ai/qwen-image-edit-2511", name: "Qwen-Image-Edit 2511 · Image edit" }
];

export default function CreatorStudioPage() {
  const [character, setCharacter] = useState("Nicole");
  const [mode, setMode] = useState("image");
  const [provider, setProvider] = useState("gpu");
  const [model, setModel] = useState("selfhost/qwen-image-edit-2511-q4");
  const [password, setPassword] = useState("");
  const [prompt, setPrompt] = useState("");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [identityData, setIdentityData] = useState("");
  const [sceneryData, setSceneryData] = useState("");
  const [placement, setPlacement] = useState("foreground_floor");
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [gpuStatus, setGpuStatus] = useState(null);
  const [ratio, setRatio] = useState("portrait_16_9");
  const [duration, setDuration] = useState(5);
  const [job, setJob] = useState(null);
  const gatewayDownSince = useRef(null);
  const [gallery, setGallery] = useState([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = submitting || referenceBusy || (job && !["COMPLETED", "ERROR"].includes(job.status));
  const gpuUnavailable = provider === "gpu" && gpuStatus?.ready === false;

  useEffect(() => {
    if (mode === "video" && provider === "wavespeed") { setProvider("fal"); return; }
    if (mode === "video") setModel(provider === "gpu" ? "disabled" : "alibaba/wan-3.0/image-to-video");
    else setModel(provider === "gpu" ? "selfhost/qwen-image-edit-2511-q4" :
      provider === "wavespeed" ? "alibaba/wan-2.5/image-edit" : imageModels[0].id);
  }, [mode, provider]);

  const poll = useCallback(async (activeJob) => {
    try {
      const response = await fetch("/api/creator-studio/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + password },
        body: JSON.stringify(activeJob)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to check job");
      if (data.status === "RECOVERING") {
        if (gatewayDownSince.current === null) gatewayDownSince.current = Date.now();
        if (Date.now() - gatewayDownSince.current >= 120000) {
          throw new Error(activeJob.provider === "wavespeed" ? "WaveSpeed status has remained unavailable for two minutes. Check WaveSpeed History for this job before resubmitting." : "Salad has remained unavailable for two minutes. The job may have been interrupted. Check the container logs before any new generation.");
        }
        setError(data.error || "Reconnecting to Salad to check this job.");
        setJob({ ...activeJob, status: "RECOVERING" });
        return;
      }
      gatewayDownSince.current = null;
      if (data.status === "ERROR") throw new Error(data.error || "Generation failed");
      setError("");
      setJob({ ...activeJob, status: data.status });
      if (data.status === "COMPLETED") {
        const urls = (data.assets || []).filter((url) => typeof url === "string" && url.startsWith("https://"));
        setGallery((previous) => [{ id: activeJob.id, character: activeJob.character, mode: activeJob.mode, prompt: activeJob.prompt, urls }, ...previous].slice(0, 18));
      }
    } catch (err) {
      setError(err.message);
      setJob({ ...activeJob, status: "ERROR" });
    }
  }, [password]);

  useEffect(() => {
    if (!job || job.status === "COMPLETED" || job.status === "ERROR") return;
    // Serial polling prevents overlapping long waits when Salad's gateway
    // is degraded, and retries only the existing job status endpoint.
    const timer = setTimeout(() => poll(job), 5000);
    return () => clearTimeout(timer);
  }, [job, poll]);

  useEffect(() => {
    if (provider !== "gpu") { setGpuStatus(null); return; }
    let active = true;
    async function checkGpu() {
      try {
        const response = await fetch("/api/creator-studio/health", { cache: "no-store" });
        const value = await response.json();
        if (active) setGpuStatus(response.ok ? value : { reachable: false, ready: false });
      } catch {
        if (active) setGpuStatus({ reachable: false, ready: false });
      }
    }
    checkGpu();
    const timer = setInterval(checkGpu, 15000);
    return () => { active = false; clearInterval(timer); };
  }, [provider]);

  async function readPrivateReference(file, setter) {
    if (!file) { setter(""); return; }
    setError("");
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 12_000_000) {
      setError("Use a JPEG, PNG or WebP reference under 12 MB.");
      return;
    }
    setReferenceBusy(true);
    try {
      const bitmap = await createImageBitmap(file);
      const factor = Math.min(1, 1024 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * factor));
      canvas.height = Math.max(1, Math.round(bitmap.height * factor));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Your browser could not process the reference.");
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      let output = canvas.toDataURL("image/jpeg", 0.78);
      if (output.length > 1_850_000) output = canvas.toDataURL("image/jpeg", 0.53);
      if (output.length > 1_850_000) throw new Error("Reference is too large after compression; use a smaller photo.");
      setter(output);
    } catch (err) {
      setError(err.message || "Could not read reference image.");
    } finally {
      setReferenceBusy(false);
    }
  }

  async function generate(event) {
    event.preventDefault();
    setError("");
    gatewayDownSince.current = null;
    if (!password) { setError("Enter the private studio access token."); return; }
    if (gpuUnavailable) { setError("Salad Qwen worker is not ready. Check its Container Logs before retrying."); return; }
    if (mode === "image" && (provider === "gpu" || provider === "wavespeed") && !identityData && !sceneryData) {
      setError(provider === "gpu" ? "Qwen needs an identity or scenery reference image." : "Wan 2.5 Edit needs at least one identity or scenery reference image.");
      return;
    }
    if (mode === "video" && !referenceUrl.trim()) { setError("Video generation needs an HTTPS start-frame URL."); return; }
    setSubmitting(true);
    try {
      const response = await fetch("/api/creator-studio/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + password },
        body: JSON.stringify({ provider, character, mode, model, prompt, placement, referenceUrl: referenceUrl.trim(), referenceImages: (provider === "gpu" || provider === "wavespeed") && mode === "image" ? { ...(identityData ? { identity: identityData } : {}), ...(sceneryData ? { scenery: sceneryData } : {}) } : {}, ratio, duration })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Submission failed");
      setJob({ ...data, character, mode, prompt, status: "IN_QUEUE" });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return <div className={styles.shell}>
    <aside className={styles.sidebar}>
      <div className={styles.brand}><span className={styles.brandmark}>✦</span><div><b>Private Studio</b><small>Aria × Nicole</small></div></div>
      <p className={styles.eyebrow}>CHARACTER VAULT</p>
      {Object.keys(characters).map((name) => <button key={name} className={character === name ? styles.personActive : styles.person} type="button" onClick={() => setCharacter(name)}>
        <span className={styles.avatar}>{name[0]}</span><span><b>{name}</b><small>{characters[name].headline}</small></span>
      </button>)}
      <div className={styles.sidefoot}><span className={styles.online} /> Private workspace prototype<br /><small>Images remain under your control. Generation uses your configured provider.</small></div>
    </aside>
    <main className={styles.main}>
      <div className={styles.topline}><span>WORKSPACE / {character.toUpperCase()}</span><span className={styles.tag}>Owner access</span></div>
      <header className={styles.header}><div><p className={styles.eyebrow}>CONTENT PRODUCTION</p><h1>Create with {character}<span className={styles.sparkle}>✳</span></h1><p>{characters[character].hint}</p></div></header>
      <div className={styles.layout}>
        <section className={styles.panel}>
          <div className={styles.tabs}><button type="button" className={mode === "image" ? styles.tabActive : styles.tab} onClick={() => setMode("image")}>✧ Image Studio</button><button type="button" className={mode === "video" ? styles.tabActive : styles.tab} onClick={() => setMode("video")}>▷ Video Studio</button></div>
          <form onSubmit={generate} className={styles.form}>
            <div className={styles.field}><label>Private access token</label><input autoComplete="off" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="STUDIO_ACCESS_TOKEN from Vercel" /></div>
            <div className={styles.row2}><div className={styles.field}><label>Compute provider</label><select value={provider} onChange={(e) => setProvider(e.target.value)}><option value="fal">fal.ai · hosted Qwen</option><option value="gpu">Independent GPU · Qwen Edit</option>{mode === "image" && <option value="wavespeed">WaveSpeedAI · Wan 2.5 Edit</option>}</select></div><div className={styles.field}><label>Model</label>{mode === "image" ? provider === "gpu" ? <select value={model} onChange={(e) => setModel(e.target.value)}><option value="selfhost/qwen-image-edit-2511-q4">Qwen-Image-Edit 2511 Q4 (self-hosted)</option></select> : provider === "wavespeed" ? <select value={model} onChange={(e) => setModel(e.target.value)}><option value="alibaba/wan-2.5/image-edit">Wan 2.5 · Image Edit (WaveSpeedAI)</option></select> : <select value={model} onChange={(e) => setModel(e.target.value)}>{imageModels.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select> : <select value={model} onChange={(e) => setModel(e.target.value)}>{provider === "gpu" ? <option value="disabled">Video not enabled on Salad pilot</option> : <option value="alibaba/wan-3.0/image-to-video">Wan 3.0 I2V</option>}</select>}</div></div>
            <div className={styles.field}><label>Creative direction</label><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} maxLength={4000} placeholder={mode === "image" ? "Describe the scene, composition, lighting, styling, and mood..." : "Describe the subject's motion and camera movement..."} /><div className={styles.helper}>{prompt.length}/4000 characters</div></div>
            {(provider === "gpu" || provider === "wavespeed") && mode === "image" ? <>
              <div className={styles.field}><label>{character} identity reference (optional)</label><input aria-label={character + " identity reference"} accept="image/jpeg,image/png,image/webp" type="file" onChange={(e) => readPrivateReference(e.target.files?.[0], setIdentityData)} /><div className={styles.helper}>{identityData ? provider === "wavespeed" ? "Identity photo ready; uploaded to WaveSpeedAI only when you generate." : "Identity reference ready; transferred only with your next GPU request." : "Optional face/character photo; not uploaded until you generate."}</div></div>
              <div className={styles.field}><label>Scenery reference (optional)</label><input aria-label="Scenery reference" accept="image/jpeg,image/png,image/webp" type="file" onChange={(e) => readPrivateReference(e.target.files?.[0], setSceneryData)} /><div className={styles.helper}>{sceneryData ? provider === "wavespeed" ? "Scenery photo ready; uploaded to WaveSpeedAI only when you generate." : "Scenery reference ready; transferred only with your next GPU request." : "Optional location/background photo; the model may reinterpret details."}</div></div>
              {identityData && sceneryData && <div className={styles.field}><label>Subject placement in scenery</label><select aria-label="Subject placement in scenery" value={placement} onChange={(e) => setPlacement(e.target.value)}><option value="foreground_floor">Standing on clear foreground floor — full body</option><option value="rear_floor">Standing near windows — behind furniture</option><option value="sofa_seated">Seated naturally on sofa</option></select><div className={styles.helper}>Spatial guidance: shoes must connect to the floor, furniture must not intersect the body, and the person's scale must match the room. Results still require visual review.</div></div>}
            </> : <div className={styles.field}><label>{mode === "video" ? "Start-frame HTTPS image URL (required)" : model === "fal-ai/qwen-image-edit-2511" ? "Public HTTPS Qwen editing reference (required)" : "Reference image URL (optional)"}</label><input value={referenceUrl} onChange={(e) => setReferenceUrl(e.target.value)} type="url" placeholder="https://... image URL" /><div className={styles.helper}>{mode === "image" && model === "fal-ai/qwen-image-edit-2511" ? "Qwen hosted editing requires a public HTTPS image URL. Use the independent GPU for private identity uploads." : mode === "image" ? "Qwen 2512 uses your text prompt; this URL is ignored." : "Use a URL from storage you control. Avoid exposing private reference photos publicly."}</div></div>}
            <div className={styles.row2}><div className={styles.field}><label>{mode === "image" ? "Frame size" : "Aspect ratio"}</label><select value={ratio} onChange={(e) => setRatio(e.target.value)}>{mode === "image" ? <><option value="portrait_16_9">Portrait 9:16</option><option value="square_hd">Square HD</option><option value="landscape_16_9">Landscape 16:9</option></> : <><option value="portrait_16_9">Portrait 9:16</option><option value="square_hd">Square 1:1</option><option value="landscape_16_9">Landscape 16:9</option></>}</select></div><div className={styles.field}><label>{mode === "image" ? "Batch size" : "Clip duration"}</label>{mode === "image" ? <div className={styles.readonly}>1 image / request</div> : <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}><option value={5}>5 seconds</option><option value={10}>10 seconds</option><option value={15}>15 seconds</option></select>}</div></div>
            {provider === "fal" ? <p className={styles.notice}>fal.ai content policies and model safeguards apply.</p> : provider === "wavespeed" ? <p className={styles.notice}>Wan 2.5 Image Edit is hosted by WaveSpeedAI, not your Salad GPU. Uploaded references are sent to WaveSpeedAI only when you generate; uploaded files are retained by the provider for approximately 7 days. Model/content policies and per-job charges apply. This is IMAGE editing, not video editing.</p> : <p className={styles.notice}>Salad image generation uses Apache-2.0 Qwen-Image-Edit-2511 with Q4 transformer quantization; a private identity or scenery reference is required. Perspective guidance is not automatic geometry validation. GPU video remains disabled.</p>}
            {provider === "gpu" && <p className={styles.notice} aria-live="polite">GPU status: {gpuStatus === null ? "Checking connection…" : gpuStatus.ready ? "Qwen Image Edit 2511 is ready" : gpuStatus.reachable && gpuStatus.engine === "not_qwen_worker" ? "Salad still runs the old Docker image; Qwen not installed yet" : gpuStatus.reachable ? "Qwen model is loading or failed; check Salad Container Logs" : "Offline or unreachable"}. Status refreshes automatically.</p>}
            {error && <p role="alert" className={styles.error}>{error}</p>}
            <button disabled={!!busy || gpuUnavailable || (provider === "gpu" && mode === "video")} className={styles.generate} type="submit">{busy ? "Generation in progress…" : "Generate " + (mode === "image" ? "image" : "video") + "  ✦"}</button>
            {job && <p aria-live="polite" className={styles.jobStatus}>Latest job: {job.status === "COMPLETED" ? "Complete" : job.status === "IN_PROGRESS" ? "Rendering…" : job.status === "IN_QUEUE" ? "Queued…" : job.status === "RECOVERING" ? "Reconnecting to Salad — same job, no resubmission…" : job.status === "ERROR" ? "Failed or interrupted" : job.status}</p>}
            {job?.status === "ERROR" && ["gpu", "wavespeed"].includes(job?.provider) && <button type="button" className={styles.readonly} onClick={() => { gatewayDownSince.current = null; setError(""); setJob({ ...job, status: "RECOVERING" }); }}>Recheck existing job (no new generation)</button>}
          </form>
        </section>
        <section className={styles.output}>
          <div className={styles.outputhead}><div><h2>Generation gallery</h2><p>Recent results from this browser session</p></div><span className={styles.counter}>{gallery.length}</span></div>
          {gallery.length === 0 ? <div className={styles.empty}><div className={styles.orbit}>✧</div><h3>Your next concept starts here</h3><p>Choose a character, set your creative direction and generate your first shot.</p></div> : <div className={styles.gallery}>{gallery.map((item) => <article key={item.id} className={styles.result}><div className={styles.resultmeta}><b>{item.character}</b><span>{item.mode}</span></div>{item.urls.map((url, index) => <div key={url + index}>{item.mode === "video" ? <video className={styles.media} controls playsInline src={url} /> : <img className={styles.media} src={url} alt={item.prompt || "Generated image"} />}<a className={styles.save} href={url} target="_blank" rel="noopener noreferrer">Open original ↗</a></div>)}<p className={styles.prompt}>{item.prompt || "Untitled generation"}</p></article>)}</div>}
        </section>
      </div>
    </main>
  </div>;
}
