"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./studio.module.css";

const characters = {
  Nicole: { headline: "Beirut after dark", hint: "Photorealistic, expressive, consistent appearance" },
  Aria: { headline: "Mediterranean muse", hint: "Editorial, cinematic, natural skin texture" }
};
const imageModels = [
  { id: "fal-ai/flux-2/klein/9b", name: "FLUX.2 Klein 9B", rate: "$0.006/MP" },
  { id: "fal-ai/flux-2", name: "FLUX.2 Dev", rate: "$0.012/MP" }
];

export default function CreatorStudioPage() {
  const [character, setCharacter] = useState("Nicole");
  const [mode, setMode] = useState("image");
  const [provider, setProvider] = useState("fal");
  const [model, setModel] = useState(imageModels[0].id);
  const [password, setPassword] = useState("");
  const [prompt, setPrompt] = useState("");
  const [referenceUrl, setReferenceUrl] = useState("");
  const [ratio, setRatio] = useState("portrait_16_9");
  const [duration, setDuration] = useState(5);
  const [job, setJob] = useState(null);
  const [gallery, setGallery] = useState([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = submitting || (job && !["COMPLETED", "ERROR"].includes(job.status));

  useEffect(() => {
    if (mode === "video") setModel(provider === "gpu" ? "disabled" : "alibaba/wan-3.0/image-to-video");
    else setModel(provider === "gpu" ? "selfhost/flux2-klein-4b" : imageModels[0].id);
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
      if (data.status === "ERROR") throw new Error(data.error || "Generation failed");
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
    const timer = setInterval(() => poll(job), 4000);
    return () => clearInterval(timer);
  }, [job, poll]);

  async function generate(event) {
    event.preventDefault();
    setError("");
    if (!password) { setError("Enter the private studio access token."); return; }
    if (mode === "video" && !referenceUrl.trim()) { setError("Video generation needs an HTTPS start-frame URL."); return; }
    setSubmitting(true);
    try {
      const response = await fetch("/api/creator-studio/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + password },
        body: JSON.stringify({ provider, character, mode, model, prompt, referenceUrl: referenceUrl.trim(), ratio, duration })
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
            <div className={styles.row2}><div className={styles.field}><label>Compute provider</label><select value={provider} onChange={(e) => setProvider(e.target.value)}><option value="fal">fal.ai · policy-compliant</option><option value="gpu">Independent GPU · requires setup</option></select></div><div className={styles.field}><label>Model</label>{mode === "image" ? provider === "gpu" ? <select value={model} onChange={(e) => setModel(e.target.value)}><option value="selfhost/flux2-klein-4b">FLUX.2 Klein 4B (self-hosted)</option></select> : <select value={model} onChange={(e) => setModel(e.target.value)}>{imageModels.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select> : <select value={model} onChange={(e) => setModel(e.target.value)}>{provider === "gpu" ? <option value="disabled">Video not enabled on Salad pilot</option> : <option value="alibaba/wan-3.0/image-to-video">Wan 3.0 I2V</option>}</select>}</div></div>
            <div className={styles.field}><label>Creative direction</label><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} maxLength={4000} placeholder={mode === "image" ? "Describe the scene, composition, lighting, styling, and mood..." : "Describe the subject's motion and camera movement..."} /><div className={styles.helper}>{prompt.length}/4000 characters</div></div>
            <div className={styles.field}><label>Start-frame or reference image URL {mode === "video" ? "(required)" : "(phase-one GPU: leave empty)"}</label><input value={referenceUrl} onChange={(e) => setReferenceUrl(e.target.value)} type="url" placeholder="https://... image URL" /><div className={styles.helper}>{provider === "gpu" && mode === "image" ? "Reference identity support is not enabled yet; leave this blank." : provider === "fal" && mode === "image" ? "FLUX text-to-image ignores this field. Image-editing support is a later step." : "Use a URL from storage you control. Avoid exposing private reference photos publicly."}</div></div>
            <div className={styles.row2}><div className={styles.field}><label>{mode === "image" ? "Frame size" : "Aspect ratio"}</label><select value={ratio} onChange={(e) => setRatio(e.target.value)}>{mode === "image" ? <><option value="portrait_16_9">Portrait 9:16</option><option value="square_hd">Square HD</option><option value="landscape_16_9">Landscape 16:9</option></> : <><option value="portrait_16_9">Portrait 9:16</option><option value="square_hd">Square 1:1</option><option value="landscape_16_9">Landscape 16:9</option></>}</select></div><div className={styles.field}><label>{mode === "image" ? "Batch size" : "Clip duration"}</label>{mode === "image" ? <div className={styles.readonly}>1 image / request</div> : <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}><option value={5}>5 seconds</option><option value={10}>10 seconds</option><option value={15}>15 seconds</option></select>}</div></div>
            {provider === "fal" ? <p className={styles.notice}>fal.ai content policies and model safeguards apply.</p> : <p className={styles.notice}>Salad pilot supports text-to-image only. Character names are labels; identity locking and video are not yet configured. Comply with model, host and local policies.</p>}
            {error && <p role="alert" className={styles.error}>{error}</p>}
            <button disabled={!!busy || (provider === "gpu" && mode === "video")} className={styles.generate} type="submit">{busy ? "Generation in progress…" : "Generate " + (mode === "image" ? "image" : "video") + "  ✦"}</button>
            {job && <p aria-live="polite" className={styles.jobStatus}>Latest job: {job.status === "COMPLETED" ? "Complete" : job.status === "IN_PROGRESS" ? "Rendering…" : job.status === "IN_QUEUE" ? "Queued…" : job.status === "ERROR" ? "Failed" : job.status}</p>}
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
