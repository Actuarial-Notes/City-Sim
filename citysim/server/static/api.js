/* CitySim front-end backend adapter.
 *
 * The SPA talks to one interface; two implementations sit behind it:
 *
 *   LiveBackend    the FastAPI app — builds twins and runs ensembles on demand.
 *   StaticBackend  a prebaked export (scripts/export_static.py) served as flat
 *                  files, e.g. GitHub Pages. Same payloads, same binary replay
 *                  format; the difference is that scenarios are limited to the
 *                  presets baked at build time.
 *
 * Static mode is selected by `window.CITYSIM_STATIC` (a manifest URL) which the
 * exporter injects into its copy of index.html. Absent that, the app is live.
 */
"use strict";

/* Prebaked binaries ship gzipped to keep the deployed artifact small. Sniff the
 * magic bytes rather than trusting the extension: a host that decompresses for
 * us (Content-Encoding: gzip) would otherwise break the decode. */
async function maybeGunzip(buf) {
  const u8 = new Uint8Array(buf);
  if (u8.length < 2 || u8[0] !== 0x1f || u8[1] !== 0x8b) return buf;
  if (typeof DecompressionStream === "undefined")
    throw new Error("this browser cannot read the prebaked data (needs DecompressionStream)");
  const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
  return await new Response(stream).arrayBuffer();
}

async function getBuffer(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return maybeGunzip(await res.arrayBuffer());
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return res.json();
}

function quantile(sorted, q) {
  if (!sorted.length) return 0;
  const p = (sorted.length - 1) * q, lo = Math.floor(p), hi = Math.ceil(p);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo);
}

/* ===================================================================== live */
class LiveBackend {
  constructor() {
    this.mode = "live";
    this.manifest = null;
  }

  async init() { return null; }

  hazards() { return getJSON("/api/hazards"); }

  /* Progress for both job types comes over a WebSocket, with polling as the
   * safety net (proxies that drop WS, sleeping tabs). */
  _watchJob(jobId, onProgress) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = job => {
        if (settled) return;
        settled = true;
        job.status === "done" ? resolve(job) : reject(new Error(job.error || "job failed"));
      };
      const poll = async () => {
        try {
          const job = await getJSON(`/api/jobs/${jobId}`);
          onProgress(job);
          if (job.status === "done" || job.status === "error") return finish(job);
        } catch (e) { /* transient; keep polling */ }
        if (!settled) setTimeout(poll, 700);
      };
      try {
        const ws = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/jobs/${jobId}`);
        ws.onmessage = ev => {
          const job = JSON.parse(ev.data);
          if (job.error && !job.status) return;
          onProgress(job);
          if (job.status === "done" || job.status === "error") { finish(job); ws.close(); }
        };
        ws.onerror = () => ws.close();
      } catch (e) { /* fall through to polling */ }
      poll();
    });
  }

  async _post(url, body) {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  }

  async buildTwin({ place, mode }, onProgress) {
    const { job_id } = await this._post("/api/twin", { place, mode });
    const job = await this._watchJob(job_id, j => onProgress(j.progress, j.message));
    return job.result;
  }

  async simulate({ twinId, hazard, nRuns, options }, onProgress) {
    const { job_id } = await this._post("/api/simulate", {
      twin_id: twinId, hazard, n_runs: nRuns, options,
    });
    await this._watchJob(job_id, j => onProgress(j.progress, j.message));
    return job_id;
  }

  results(jobId) { return getJSON(`/api/results/${jobId}`); }
  scene(twinId) { return getJSON(`/api/twin/${twinId}/scene`); }
  frames(jobId, run) { return getBuffer(`/api/results/${jobId}/runs/${run}/frames`); }
  building(jobId, buildingId) { return getJSON(`/api/results/${jobId}/buildings/${buildingId}`); }

  /* every run is re-simulable on demand, so all of them are replayable */
  replayableRuns() { return null; }
}

/* =================================================================== static */
class StaticBackend {
  constructor(manifestUrl) {
    this.mode = "static";
    this.manifestUrl = manifestUrl;
    this.root = manifestUrl.replace(/[^/]*$/, "");   // strip "manifest.json"
    this.manifest = null;
    this._results = new Map();     // preset id → results payload
    this._losses = new Map();      // preset id → Float32Array (n_runs × n_buildings)
  }

  async init() {
    this.manifest = await getJSON(this.manifestUrl);
    return this.manifest;
  }

  async hazards() { return this.manifest.hazards; }

  /* The twin was built at export time; replay the staged messages so the
   * pipeline reads the same way it does live. */
  async buildTwin(_req, onProgress) {
    onProgress(0.4, "loading prebaked digital twin");
    await new Promise(r => setTimeout(r, 120));
    onProgress(1, "twin ready");
    return this.manifest.twin;
  }

  /* `options` is ignored — the setup screen offers baked presets in static
   * mode, and passes the chosen preset id through. */
  async simulate({ presetId }, onProgress) {
    const preset = this.manifest.presets.find(p => p.id === presetId)
      || this.manifest.presets[0];
    onProgress(0.15, `loading ${preset.n_runs}-run ensemble — ${preset.label}`);
    await this._loadResults(preset.id);
    onProgress(1, `${preset.n_runs} runs loaded`);
    return preset.id;
  }

  async _loadResults(presetId) {
    if (!this._results.has(presetId))
      this._results.set(presetId, await getJSON(`${this.root}${presetId}/results.json`));
    return this._results.get(presetId);
  }

  async results(presetId) { return this._loadResults(presetId); }

  scene() { return getJSON(`${this.root}scene.json`); }

  frames(presetId, run) {
    return getBuffer(`${this.root}${presetId}/runs/${run}.bin.gz`);
  }

  /* Per-building distributions come from one packed loss matrix instead of a
   * file per building — 311 buildings × 96 runs is ~120 kB of float32. */
  async building(presetId, buildingId) {
    const res = await this._loadResults(presetId);
    const k = res.building_ids.indexOf(buildingId);
    if (k < 0) throw new Error("building not found");
    if (!this._losses.has(presetId)) {
      const buf = await getBuffer(`${this.root}${presetId}/building_losses.bin.gz`);
      this._losses.set(presetId, new Float32Array(buf));
    }
    const matrix = this._losses.get(presetId);
    const nb = res.building_ids.length, nRuns = res.stats.n_runs;
    const losses = new Array(nRuns);
    for (let i = 0; i < nRuns; i++) losses[i] = matrix[i * nb + k];
    const sorted = [...losses].sort((a, b) => a - b);
    return {
      building_id: buildingId,
      losses,
      mean: losses.reduce((a, b) => a + b, 0) / nRuns,
      p95: quantile(sorted, 0.95),
      max: sorted[nRuns - 1],
      prob_any_damage: losses.filter(v => v > 0).length / nRuns,
    };
  }

  /* Frames are baked for a subset of runs (the ones the UI surfaces), since
   * each is a full depth-grid time-series. */
  replayableRuns(presetId) {
    const preset = this.manifest.presets.find(p => p.id === presetId);
    return preset ? preset.frames_runs : null;
  }
}

const API = window.CITYSIM_STATIC
  ? new StaticBackend(window.CITYSIM_STATIC)
  : new LiveBackend();
