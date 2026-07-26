/* CitySim replay — step 2 of the workflow.
 *
 * V2 promotes the replay from "a thing you can click into from the dashboard"
 * to the second step of the workflow: you run the ensemble, then you watch a
 * storm happen. Everything here serves that — the storm clock, the rain, the
 * building inspector that updates as water rises against it.
 *
 * Playback runs on requestAnimationFrame with a time accumulator rather than
 * setInterval. setInterval drifts under load, and because the renderer is
 * demand-driven it also left the water shimmer and the surcharge pulse frozen
 * whenever playback was paused.
 */
"use strict";

(function () {
const C = window.CitySimCharts;
const $ = id => document.getElementById(id);

/* frames arrive as [uint32 headerLen][header JSON][uint16 depth mm] — the one
 * encoder in citysim/results/pack.py serves both the live API and the bake */
function decodeFrames(buf) {
  const dv = new DataView(buf);
  const hlen = dv.getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
  return { header, depths: new Uint16Array(buf.slice(4 + hlen)) };
}

class Replay {
  constructor(state) {
    this.state = state;
    this.frames = null;
    this.header = null;
    this.run = null;
    this.idx = 0;
    this.playing = false;
    this.speed = 2;
    this._acc = 0;
    this._last = 0;
    this._raf = null;
    this.baseFps = 6;
    this.onFrame = null;
    this.onFinish = null;
  }

  attach(viewer) {
    this.viewer = viewer;
    viewer.onPick = b => this._select(b);
    viewer.onHover = (b, x, y) => this._hover(b, x, y);
    viewer.onModeChange = m => this._syncModeButtons(m);
    this.rain = new RainLayer($("rain-canvas"));
  }

  /* ------------------------------------------------------------- load -- */
  async open(run) {
    if (this.run === run && this.frames) { this.play(); return; }
    this.pause();
    $("viewer-loading").classList.add("visible");
    try {
      const buf = await API.frames(this.state.jobId, run);
      const { header, depths } = decodeFrames(buf);
      this.header = header;
      this.frames = depths;
      this.run = run;
      this.idx = 0;
      const sel = $("run-select");
      if (sel) sel.value = String(run);
      $("scrubber").max = header.n_frames - 1;
      this._renderRunInfo(run);
      this.setFrame(0);
      this.play();
    } catch (e) {
      $("run-info").innerHTML =
        `<b class="bad">replay failed:</b> ${e.message || e}`;
    } finally {
      $("viewer-loading").classList.remove("visible");
    }
  }

  _renderRunInfo(run) {
    const s = this.header.scenario, r = this.header.run_summary;
    $("run-info").innerHTML =
      `<b>Run #${run}</b> — a ${s.return_period.toFixed(0)}-year storm: ` +
      `${s.total_mm.toFixed(0)} mm over ${s.duration_h.toFixed(1)} h` +
      `<div class="sub">${C.fmt$(r.total_loss)} total damage · ` +
      `${C.fmt$(r.per_household_mean)} per household · ` +
      `${r.surcharged_nodes} manholes surcharged</div>`;
  }

  reset() {
    this.pause();
    this.frames = null;
    this.header = null;
    this.run = null;
    this.idx = 0;
    if (this.viewer) this.viewer.clearWater();
    if (this.rain) this.rain.setIntensity(0);
    $("run-info").textContent = "No run loaded";
  }

  /* ------------------------------------------------------------ frames -- */
  setFrame(k) {
    if (!this.frames) return;
    const h = this.header;
    const n = h.ny * h.nx;
    k = Math.min(Math.max(k, 0), h.n_frames - 1);
    this.idx = k;

    const depth = new Float32Array(n);
    const off = k * n;
    for (let i = 0; i < n; i++) depth[i] = this.frames[off + i] / 1000;
    this.depth = depth;

    const rain = h.rain_mmh[k];
    this.viewer.setWaterFrame(depth, h.surcharging[k], rain);
    this.rain.setIntensity(rain);

    $("scrubber").value = k;
    const tmin = h.t[k] / 60;
    const tend = h.t[h.n_frames - 1] / 60;
    $("t-label").textContent = `t = ${tmin.toFixed(0)} / ${tend.toFixed(0)} min`;
    $("rain-val").textContent = `${rain.toFixed(1)} mm/h`;
    $("rain-bar").firstElementChild.style.width =
      `${Math.min(rain / 120 * 100, 100)}%`;

    const flooded = countFlooded(depth, this.state.scene.cell_size);
    $("flood-stat").textContent =
      `${(flooded.area / 1e4).toFixed(1)} ha wet · peak ${flooded.peak.toFixed(2)} m`;

    if (this.selected) this._renderInspector(this.selected);
    if (this.onFrame) this.onFrame(k, h);
  }

  /* -------------------------------------------------------- transport -- */
  play() {
    if (!this.frames || this.playing) return;
    this.playing = true;
    $("btn-play").textContent = "⏸";
    $("btn-play").title = "pause";
    this._last = performance.now();
    this._acc = 0;
    const tick = now => {
      if (!this.playing) return;
      const dt = Math.min((now - this._last) / 1000, 0.25);
      this._last = now;
      this._acc += dt * this.baseFps * this.speed;
      if (this._acc >= 1) {
        const advance = Math.floor(this._acc);
        this._acc -= advance;
        let next = this.idx + advance;
        if (next >= this.header.n_frames - 1) {
          this.setFrame(this.header.n_frames - 1);
          this.pause();
          if (this.onFinish) this.onFinish();
          return;
        }
        this.setFrame(next);
      }
      this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  }

  pause() {
    this.playing = false;
    const b = $("btn-play");
    if (b) { b.textContent = "▶"; b.title = "play"; }
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
  }

  toggle() {
    if (this.playing) return this.pause();
    // replaying from the end should start over rather than sit there
    if (this.header && this.idx >= this.header.n_frames - 1) this.setFrame(0);
    this.play();
  }

  setSpeed(v) { this.speed = parseFloat(v) || 1; }

  /* ------------------------------------------------------- inspection -- */
  _hover(b, x, y) {
    this.viewer.setHover(b ? b.id : null);
    if (!b) { C.hideTip(); return; }
    C.showTip(this._tooltipHtml(b), x, y);
  }

  _tooltipHtml(b) {
    const depth = this._depthAt(b);
    const rows = [
      [`${cap(b.use)}${b.dwelling_units > 1 ? ` · ${b.dwelling_units} units` : ""}`, ""],
      ["Built", b.year_built],
      ["Material", b.material.replace("_", " ")],
      ["Storeys", b.storeys],
      ["Foundation", b.has_basement
        ? `basement ${b.basement_depth.toFixed(1)} m deep` : b.foundation],
      ["First floor", `+${b.first_floor_height.toFixed(2)} m`],
      ["Value", `${C.fmt$(b.structure_value)} + ${C.fmt$(b.contents_value)} contents`],
    ];
    let live = "";
    if (depth !== null) {
      const cls = depth > 0.25 ? "bad" : depth > 0.02 ? "warn" : "";
      live = `<div class="tip-live ${cls}">water beside it now: ` +
             `<b>${(depth * 100).toFixed(0)} cm</b></div>`;
    }
    return `<div class="tip-title">${rows[0][0]}</div>` +
      rows.slice(1).map(([k, v]) => `<div class="tip-row"><span>${k}</span><b>${v}</b></div>`).join("") +
      live + `<div class="t">click for its loss distribution</div>`;
  }

  /* peak depth over the cells the footprint's bounding box covers — the same
   * neighbourhood the impact engine samples */
  _depthAt(b) {
    if (!this.depth || !this.header || !b.centroid) return null;
    const scene = this.state.scene;
    const cs = scene.cell_size;
    const nx = this.header.nx, ny = this.header.ny;
    let max = 0;
    const j0 = Math.max(Math.floor((b.centroid[0] - 8) / cs), 0);
    const j1 = Math.min(Math.ceil((b.centroid[0] + 8) / cs), nx - 1);
    const i0 = Math.max(Math.floor((b.centroid[1] - 8) / cs), 0);
    const i1 = Math.min(Math.ceil((b.centroid[1] + 8) / cs), ny - 1);
    for (let i = i0; i <= i1; i++)
      for (let j = j0; j <= j1; j++) {
        const d = this.depth[i * nx + j];
        if (d > max) max = d;
      }
    return max;
  }

  async _select(b) {
    this.selected = b;
    this.viewer.setSelected(b ? b.id : null);
    const panel = $("bldg-panel");
    if (!b) { panel.classList.remove("visible"); return; }
    panel.classList.add("visible");
    this._renderInspector(b, true);
    try {
      const d = await API.building(this.state.jobId, b.id);
      this.selectedDist = d;
      this._renderInspector(b);
    } catch (e) {
      this.selectedDist = null;
      this._renderInspector(b);
    }
  }

  _renderInspector(b, loading) {
    const panel = $("bldg-panel");
    const d = loading ? null : this.selectedDist;
    const depth = this._depthAt(b);
    const meta = `${b.material.replace("_", " ")} · ${b.storeys} storey · ` +
      `built ${b.year_built} · ` +
      (b.has_basement ? `${b.basement_depth.toFixed(1)} m basement` : "no basement");
    const dist = loading
      ? `<div class="muted">loading distribution…</div>`
      : d
        ? `<div class="dist-row">
             <span>mean <b>${C.fmt$(d.mean)}</b></span>
             <span>P95 <b>${C.fmt$(d.p95)}</b></span>
             <span>any damage <b>${(d.prob_any_damage * 100).toFixed(0)}%</b></span>
           </div>
           <div class="muted">across ${d.losses.length} runs</div>
           ${C.sparkHist(d.losses)}`
        : `<div class="muted">no per-building results for this ensemble</div>`;
    panel.innerHTML = `<div class="panel">
      <button class="close" title="close">×</button>
      <h4>${cap(b.use)} — ${C.fmt$(b.structure_value + b.contents_value)}</h4>
      <div class="muted">${meta}</div>
      ${depth !== null
        ? `<div class="live-depth ${depth > 0.25 ? "bad" : depth > 0.02 ? "warn" : ""}">
             ${depth > 0.02
               ? `<b>${(depth * 100).toFixed(0)} cm</b> of water against it right now`
               : "dry at this moment in the storm"}
           </div>` : ""}
      ${dist}</div>`;
    panel.querySelector(".close").onclick = () => this._select(null);
  }

  _syncModeButtons(mode) {
    for (const b of document.querySelectorAll("#view-mode button"))
      b.classList.toggle("active", b.dataset.mode === mode);
  }
}

/* wet area uses the same 5 cm threshold the ensemble reducer does, so the live
 * readout and the results table agree on what counts as flooded */
function countFlooded(depth, cellSize) {
  let n = 0, peak = 0;
  for (let i = 0; i < depth.length; i++) {
    const d = depth[i];
    if (d > 0.05) n++;
    if (d > peak) peak = d;
  }
  return { area: n * cellSize * cellSize, peak };
}

function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

/* ---------------------------------------------------------- rain layer --
 * A 2D canvas of falling streaks over the GL canvas, with density and speed
 * driven by the frame's rainfall rate. It costs almost nothing and does more
 * for the sense of a storm happening than anything in the 3D scene. */
class RainLayer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas ? canvas.getContext("2d") : null;
    this.intensity = 0;
    this.drops = [];
    this._raf = null;
  }

  setIntensity(mmh) {
    const target = Math.min((mmh || 0) / 90, 1);
    this.intensity = target;
    if (target <= 0.01) {
      this.stop();
      return;
    }
    this._ensure(Math.round(target * 420));
    if (!this._raf) this._loop();
  }

  stop() {
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
    if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.drops.length = 0;
  }

  _ensure(n) {
    const w = this.canvas.clientWidth || 800;
    const h = this.canvas.clientHeight || 600;
    while (this.drops.length < n)
      this.drops.push({
        x: Math.random() * (w + 200) - 100, y: Math.random() * h,
        len: 8 + Math.random() * 18, v: 700 + Math.random() * 900,
        a: 0.15 + Math.random() * 0.35,
      });
    if (this.drops.length > n) this.drops.length = n;
  }

  _loop() {
    const c = this.canvas, ctx = this.ctx;
    if (!ctx) return;
    let last = performance.now();
    const step = now => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const w = Math.round((c.clientWidth || 1) * dpr);
      const h = Math.round((c.clientHeight || 1) * dpr);
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, c.clientWidth, c.clientHeight);
      ctx.lineCap = "round";
      const slant = 0.22;
      for (const d of this.drops) {
        d.y += d.v * dt;
        d.x += d.v * dt * slant;
        if (d.y > c.clientHeight) {
          d.y = -20;
          d.x = Math.random() * (c.clientWidth + 200) - 100;
        }
        ctx.strokeStyle = `rgba(200,222,245,${(d.a * this.intensity).toFixed(3)})`;
        ctx.lineWidth = 1.1;
        ctx.beginPath();
        ctx.moveTo(d.x, d.y);
        ctx.lineTo(d.x - d.len * slant, d.y - d.len);
        ctx.stroke();
      }
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }
}

window.CitySimReplay = Replay;
window.CitySimCountFlooded = countFlooded;
})();
