/* CitySim SPA — Tier 1 front end.
 * Screens: setup → results dashboard → 3D replay viewer.
 */
"use strict";

const $ = id => document.getElementById(id);
const fmt$ = v => v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M`
  : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : `$${Math.round(v)}`;
const INK2 = "#c3c2b7", MUTED = "#898781", GRID = "#2c2c2a", BASE = "#383835",
  S1 = "#3987e5";

const state = {
  hazard: "flood", twin: null, jobId: null, results: null,
  scene: null, viewer: null, preset: null, replayable: null,
  replay: { frames: null, header: null, idx: 0, playing: false, timer: null, run: null },
};

/* ================= navigation ================= */
const screens = { setup: $("screen-setup"), dash: $("screen-dash"), viewer: $("screen-viewer") };
function show(name) {
  for (const [k, el] of Object.entries(screens)) el.classList.toggle("active", k === name);
  for (const k of ["setup", "dash", "viewer"])
    $(`nav-${k}`).classList.toggle("active", k === name);
  if (name === "viewer" && state.viewer) state.viewer.requestRender();
}
$("nav-setup").onclick = () => show("setup");
$("nav-dash").onclick = () => show("dash");
/* entering the viewer from the nav has no run in hand — open the worst storm
 * rather than presenting an empty scrubber */
$("nav-viewer").onclick = () => state.replay.frames ? show("viewer") : openReplay(defaultRun());

function defaultRun() {
  const runs = [...state.results.run_index]
    .filter(r => !state.replayable || state.replayable.includes(r.run))
    .sort((a, b) => b.total_loss - a.total_loss);
  return runs.length ? runs[0].run : 0;
}

/* ================= setup screen ================= */
async function loadHazards() {
  const hazards = await API.hazards();
  const wrap = $("hazard-list");
  wrap.innerHTML = "";
  for (const h of hazards) {
    const b = document.createElement("button");
    b.className = "hazard-chip" + (h.name === state.hazard ? " selected" : "");
    b.textContent = h.display_name + (h.available ? "" : " (roadmap)");
    b.disabled = !h.available;
    b.onclick = () => {
      state.hazard = h.name;
      wrap.querySelectorAll(".hazard-chip").forEach(x => x.classList.remove("selected"));
      b.classList.add("selected");
    };
    wrap.appendChild(b);
  }
}

function setProgress(frac, msg) {
  $("progress-wrap").classList.add("visible");
  $("progress-fill").style.width = `${Math.round(frac * 100)}%`;
  $("progress-msg").textContent = msg || "";
}

$("btn-go").onclick = async () => {
  const btn = $("btn-go");
  btn.disabled = true;
  $("error-msg").style.display = "none";
  try {
    // 1. build the twin (Tier 2)
    setProgress(0.02, "building digital twin…");
    state.twin = await API.buildTwin(
      { place: $("place").value, mode: $("mode").value },
      (p, msg) => setProgress(0.02 + p * 0.18, `twin: ${msg}`));
    $("twin-summary").style.display = "block";
    $("twin-summary").innerHTML =
      `<b style="color:var(--ink)">${state.twin.name}</b> — ` +
      `${state.twin.buildings} buildings · ${state.twin.households} households · ` +
      `${state.twin.sewer_nodes} sewer nodes (${state.twin.combined_sewer_nodes} combined) · ` +
      `${state.twin.grid.extent_m[0]}×${state.twin.grid.extent_m[1]} m @ ${state.twin.grid.cell_size} m` +
      `<div style="color:var(--muted);margin-top:3px">${state.twin.sources.join(" · ")}</div>`;

    // 2. run the Monte-Carlo batch (Tier 3 via the module contract)
    const options = {};
    if ($("opt-valves").checked) options.backwater_valves = true;
    const cf = parseFloat($("opt-climate").value);
    if (cf !== 1.0) options.climate_factor = cf;
    state.jobId = await API.simulate({
      twinId: state.twin.id, hazard: state.hazard,
      nRuns: parseInt($("nruns").value), options, presetId: state.preset,
    }, (p, msg) => setProgress(0.2 + p * 0.75, `ensemble: ${msg}`));

    // 3. fetch results + scene, hand off to dashboard & viewer
    setProgress(0.96, "loading results…");
    state.results = await API.results(state.jobId);
    state.scene = await API.scene(state.twin.id);
    state.replayable = API.replayableRuns(state.jobId);
    resetReplay();
    setProgress(1, "done");
    $("nav-dash").disabled = false;
    $("nav-viewer").disabled = false;
    renderDashboard();
    initViewer();
    show("dash");
  } catch (e) {
    $("error-msg").style.display = "block";
    $("error-msg").textContent = String(e.message || e);
  } finally {
    btn.disabled = false;
  }
};

/* ================= dashboard ================= */
function renderDashboard() {
  const st = state.results.stats, hh = st.per_household;
  $("tiles").innerHTML = [
    ["Expected annual damage / household", fmt$(hh.mean), `median ${fmt$(hh.median)}`],
    ["P95 / household", fmt$(hh.p95), `P99 ${fmt$(hh.p99)}`],
    ["CVaR₉₅ / household", fmt$(hh.cvar95), "mean of worst 5% of years"],
    ["Expected annual damage — region", fmt$(st.total.mean), `worst run ${fmt$(st.total.max)}`],
    ["Ensemble", `${st.n_runs} runs`, `${st.elapsed_s}s · seed ${st.seed}`],
  ].map(([k, v, d]) =>
    `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`
  ).join("");

  histogram($("chart-hist"), state.results.runs.map(r => r.per_household_mean));
  exceedanceChart($("chart-exceed"), state.results.exceedance);
  mechanismChart($("chart-mech"), st.mechanism_counts);
  runPicker();
}

/* ---- tooltip helpers ---- */
const tip = $("viz-tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html; tip.style.display = "block";
  tip.style.left = `${Math.min(x + 14, innerWidth - 180)}px`;
  tip.style.top = `${y + 12}px`;
}
function hideTip() { tip.style.display = "none"; }

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function chartFrame(W, H, pad) {
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  return { svg, x0: pad.l, x1: W - pad.r, y0: H - pad.b, y1: pad.t };
}

function histogram(mount, values) {
  mount.innerHTML = "";
  const W = 460, H = 240, f = chartFrame(W, H, { l: 46, r: 12, t: 12, b: 34 });
  const max = Math.max(...values, 1);
  const nb = 18, edges = [], counts = new Array(nb).fill(0);
  for (let i = 0; i <= nb; i++) edges.push(max * i / nb);
  for (const v of values) counts[Math.min(Math.floor(v / max * nb), nb - 1)]++;
  const cmax = Math.max(...counts, 1);
  const sx = v => f.x0 + (f.x1 - f.x0) * v / max;
  const sy = c => f.y0 - (f.y0 - f.y1) * c / cmax;
  // hairline grid + y labels
  for (let g = 0; g <= 4; g++) {
    const c = Math.round(cmax * g / 4), y = sy(c);
    f.svg.appendChild(svgEl("line", { x1: f.x0, x2: f.x1, y1: y, y2: y, stroke: GRID, "stroke-width": 1 }));
    const t = svgEl("text", { x: f.x0 - 6, y: y + 4, "text-anchor": "end", fill: MUTED, "font-size": 10 });
    t.textContent = c; f.svg.appendChild(t);
  }
  counts.forEach((c, i) => {
    if (!c) return;
    const x = sx(edges[i]) + 1, w = Math.max(sx(edges[i + 1]) - sx(edges[i]) - 2, 2);
    const y = sy(c), h = f.y0 - y;
    const r = svgEl("path", {
      d: `M${x},${f.y0} L${x},${y + 4} Q${x},${y} ${x + 4},${y} L${x + w - 4},${y} Q${x + w},${y} ${x + w},${y + 4} L${x + w},${f.y0} Z`,
      fill: S1,
    });
    r.addEventListener("mousemove", e => showTip(
      `<div class="t">${fmt$(edges[i])} – ${fmt$(edges[i + 1])}</div><b>${c} run${c > 1 ? "s" : ""}</b>`,
      e.clientX, e.clientY));
    r.addEventListener("mouseleave", hideTip);
    f.svg.appendChild(r);
  });
  f.svg.appendChild(svgEl("line", { x1: f.x0, x2: f.x1, y1: f.y0, y2: f.y0, stroke: BASE, "stroke-width": 1 }));
  for (let g = 0; g <= 4; g++) {
    const v = max * g / 4;
    const t = svgEl("text", { x: sx(v), y: f.y0 + 16, "text-anchor": "middle", fill: MUTED, "font-size": 10 });
    t.textContent = fmt$(v); f.svg.appendChild(t);
  }
  mount.appendChild(f.svg);
}

function exceedanceChart(mount, exceedance) {
  mount.innerHTML = "";
  const W = 460, H = 240, f = chartFrame(W, H, { l: 46, r: 12, t: 12, b: 34 });
  const pts = exceedance.filter(p => p.loss > 0);
  if (!pts.length) { mount.innerHTML = `<div style="color:${MUTED};font-size:12px">no non-zero losses</div>`; return; }
  const lmax = pts[0].loss;
  const sx = l => f.x0 + (f.x1 - f.x0) * l / lmax;
  const sy = p => f.y0 - (f.y0 - f.y1) * p;
  for (let g = 0; g <= 4; g++) {
    const p = g / 4, y = sy(p);
    f.svg.appendChild(svgEl("line", { x1: f.x0, x2: f.x1, y1: y, y2: y, stroke: GRID, "stroke-width": 1 }));
    const t = svgEl("text", { x: f.x0 - 6, y: y + 4, "text-anchor": "end", fill: MUTED, "font-size": 10 });
    t.textContent = `${Math.round(p * 100)}%`; f.svg.appendChild(t);
  }
  const d = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.loss).toFixed(1)},${sy(p.prob).toFixed(1)}`).join(" ");
  f.svg.appendChild(svgEl("path", { d, fill: "none", stroke: S1, "stroke-width": 2, "stroke-linejoin": "round" }));
  f.svg.appendChild(svgEl("line", { x1: f.x0, x2: f.x1, y1: f.y0, y2: f.y0, stroke: BASE, "stroke-width": 1 }));
  for (let g = 0; g <= 4; g++) {
    const v = lmax * g / 4;
    const t = svgEl("text", { x: sx(v), y: f.y0 + 16, "text-anchor": "middle", fill: MUTED, "font-size": 10 });
    t.textContent = fmt$(v); f.svg.appendChild(t);
  }
  // crosshair + tooltip
  const cross = svgEl("line", { y1: f.y1, y2: f.y0, stroke: MUTED, "stroke-width": 1, "stroke-dasharray": "3,3", visibility: "hidden" });
  const dotEl = svgEl("circle", { r: 4, fill: S1, stroke: "#1a1a19", "stroke-width": 2, visibility: "hidden" });
  f.svg.appendChild(cross); f.svg.appendChild(dotEl);
  const hover = svgEl("rect", { x: f.x0, y: f.y1, width: f.x1 - f.x0, height: f.y0 - f.y1, fill: "transparent" });
  hover.addEventListener("mousemove", e => {
    const r = f.svg.getBoundingClientRect();
    const lx = (e.clientX - r.left) / r.width * W;
    const loss = (lx - f.x0) / (f.x1 - f.x0) * lmax;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.loss - loss) < Math.abs(best.loss - loss)) best = p;
    cross.setAttribute("x1", sx(best.loss)); cross.setAttribute("x2", sx(best.loss));
    cross.setAttribute("visibility", "visible");
    dotEl.setAttribute("cx", sx(best.loss)); dotEl.setAttribute("cy", sy(best.prob));
    dotEl.setAttribute("visibility", "visible");
    showTip(`<div class="t">annual damage / household</div><b>${(best.prob * 100).toFixed(1)}%</b> chance of exceeding <b>${fmt$(best.loss)}</b>`,
      e.clientX, e.clientY);
  });
  hover.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden"); dotEl.setAttribute("visibility", "hidden"); hideTip();
  });
  f.svg.appendChild(hover);
  mount.appendChild(f.svg);
}

function mechanismChart(mount, counts) {
  mount.innerHTML = "";
  const labels = {
    sewer_backup: "Sewer backup (combined system)",
    basement_inundation: "Basement inundation (surface water)",
    overland_flooding: "Main-floor overland flooding",
  };
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { mount.innerHTML = `<div style="color:${MUTED};font-size:12px">no damage in ensemble</div>`; return; }
  const W = 460, H = 40 + entries.length * 42;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  const max = Math.max(...entries.map(e => e[1]));
  const x0 = 10, x1 = W - 80;
  entries.forEach(([k, v], i) => {
    const y = 26 + i * 42;
    const t = svgEl("text", { x: x0, y: y - 7, fill: INK2, "font-size": 11.5 });
    t.textContent = labels[k] || k; svg.appendChild(t);
    const w = Math.max((x1 - x0) * v / max, 3);
    svg.appendChild(svgEl("path", {
      d: `M${x0},${y} L${x0 + w - 4},${y} Q${x0 + w},${y} ${x0 + w},${y + 4} L${x0 + w},${y + 8} Q${x0 + w},${y + 12} ${x0 + w - 4},${y + 12} L${x0},${y + 12} Z`,
      fill: S1,
    }));
    const val = svgEl("text", { x: x0 + w + 8, y: y + 10, fill: INK2, "font-size": 11.5, "font-weight": 600 });
    val.textContent = v; svg.appendChild(val);
  });
  mount.appendChild(svg);
}

/* a run is replayable when its frames can be produced: always, live; only for
 * the runs baked at export time in static mode */
const canReplay = run => !state.replayable || state.replayable.includes(run);

function runPicker() {
  const reps = state.results.stats.representative_runs;
  const chips = $("rep-chips");
  chips.innerHTML = "";
  for (const [name, run] of Object.entries(reps)) {
    if (!canReplay(run)) continue;
    const b = document.createElement("button");
    b.className = "rep-chip";
    b.textContent = `${name} run (#${run})`;
    b.onclick = () => openReplay(run);
    chips.appendChild(b);
  }
  const idx = [...state.results.run_index].sort((a, b) => b.total_loss - a.total_loss).slice(0, 8);
  $("runs-table").innerHTML =
    `<tr><th>storm</th><th>return period</th><th>rain</th><th>duration</th><th>total damage</th><th>$/household</th></tr>` +
    idx.map(r =>
      `<tr class="${canReplay(r.run) ? "clickable" : ""}" data-run="${r.run}">` +
      `<td>run #${r.run}</td><td>${r.return_period.toFixed(0)} yr</td>` +
      `<td>${r.total_mm.toFixed(0)} mm</td><td>${r.duration_h.toFixed(1)} h</td>` +
      `<td>${fmt$(r.total_loss)}</td><td>${fmt$(r.per_household_mean)}</td></tr>`).join("");
  $("runs-table").querySelectorAll("tr.clickable").forEach(tr =>
    tr.onclick = () => openReplay(parseInt(tr.dataset.run)));
}

/* ================= 3D viewer & replay ================= */
function initViewer() {
  if (!state.viewer) {
    state.viewer = new CitySimViewer($("gl"));
    state.viewer.onPick = onBuildingPick;
    $("color-mode").onchange = applyColorMode;
    $("toggle-pipes").onclick = () => {
      state.viewer.showPipes = !state.viewer.showPipes;
      state.viewer.requestRender();
    };
    $("btn-play").onclick = togglePlay;
    $("scrubber").oninput = () => { pause(); setFrame(parseInt($("scrubber").value)); };
    $("speed").onchange = () => { if (state.replay.playing) { pause(); play(); } };
  }
  state.viewer.setScene(state.scene);
  $("viewer-legend").innerHTML =
    `<span><span class="sw" style="background:#86b6ef"></span>shallow</span>` +
    `<span><span class="sw" style="background:#0d366b"></span>deep water</span>` +
    `<span><span class="sw" style="background:#e34948"></span>surcharging manhole</span>`;
  // run selector
  const sel = $("run-select");
  sel.innerHTML = "";
  const sorted = [...state.results.run_index]
    .filter(r => canReplay(r.run))
    .sort((a, b) => b.total_loss - a.total_loss);
  for (const r of sorted) {
    const o = document.createElement("option");
    o.value = r.run;
    o.textContent = `#${r.run} — ${r.return_period.toFixed(0)}yr, ${r.total_mm.toFixed(0)}mm, ${fmt$(r.total_loss)}`;
    sel.appendChild(o);
  }
  sel.onchange = () => openReplay(parseInt(sel.value));
  applyColorMode();
}

function applyColorMode() {
  const mode = $("color-mode").value;
  state.viewer.recolorBuildings(mode, mode === "loss" ? state.results.building_mean_loss : null);
}

/* frames belong to one ensemble — drop them when a new one is loaded */
function resetReplay() {
  pause();
  state.replay = { ...state.replay, frames: null, header: null, run: null, idx: 0 };
  $("run-info").textContent = "No run loaded";
}

async function openReplay(run) {
  show("viewer");
  $("run-select").value = String(run);
  if (state.replay.run === run && state.replay.frames) return;
  pause();
  $("viewer-loading").classList.add("visible");
  try {
    const buf = await API.frames(state.jobId, run);
    const dv = new DataView(buf);
    const hlen = dv.getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
    const depths = new Uint16Array(buf.slice(4 + hlen));
    state.replay = { ...state.replay, frames: depths, header, run, idx: 0 };
    const s = header.scenario, r = header.run_summary;
    $("run-info").innerHTML =
      `<b>Run #${run}</b> — ${s.return_period.toFixed(0)}-yr storm, ${s.total_mm.toFixed(0)} mm over ${s.duration_h.toFixed(1)} h<br>` +
      `total damage <b>${fmt$(r.total_loss)}</b> · ${fmt$(r.per_household_mean)}/household · ` +
      `${r.surcharged_nodes} manholes surcharged`;
    $("scrubber").max = header.n_frames - 1;
    setFrame(0);
    play();
  } catch (e) {
    $("run-info").innerHTML = `<b style="color:var(--critical)">replay failed:</b> ${e.message || e}`;
  } finally {
    $("viewer-loading").classList.remove("visible");
  }
}

function setFrame(k) {
  const { frames, header } = state.replay;
  if (!frames) return;
  const n = header.ny * header.nx;
  k = Math.min(Math.max(k, 0), header.n_frames - 1);
  state.replay.idx = k;
  const depth = new Float32Array(n);
  const off = k * n;
  for (let i = 0; i < n; i++) depth[i] = frames[off + i] / 1000;
  state.viewer.setWaterFrame(depth, header.surcharging[k]);
  $("scrubber").value = k;
  const tmin = header.t[k] / 60;
  $("t-label").textContent = `t = ${tmin.toFixed(0)} min / ${(header.t[header.n_frames - 1] / 60).toFixed(0)} min`;
  const rain = header.rain_mmh[k];
  $("rain-val").textContent = `${rain.toFixed(1)} mm/h`;
  $("rain-bar").firstElementChild.style.width = `${Math.min(rain / 120 * 100, 100)}%`;
}

function play() {
  const rp = state.replay;
  if (!rp.frames) return;
  rp.playing = true;
  $("btn-play").textContent = "⏸";
  const fps = 6 * parseFloat($("speed").value);
  rp.timer = setInterval(() => {
    if (rp.idx >= rp.header.n_frames - 1) { setFrame(0); return; }
    setFrame(rp.idx + 1);
  }, 1000 / fps);
}
function pause() {
  const rp = state.replay;
  rp.playing = false;
  $("btn-play").textContent = "▶";
  if (rp.timer) { clearInterval(rp.timer); rp.timer = null; }
}
function togglePlay() { state.replay.playing ? pause() : play(); }

async function onBuildingPick(b) {
  const panel = $("bldg-panel");
  if (!b) { panel.style.display = "none"; return; }
  panel.style.display = "block";
  panel.innerHTML = `<div class="panel"><h4>${b.use} · ${b.storeys} storey</h4>loading…</div>`;
  try {
    const d = await API.building(state.jobId, b.id);
    const spark = sparkHist(d.losses);
    panel.innerHTML = `<div class="panel">
      <h4>${cap(b.use)} — built ${b.year_built}</h4>
      ${b.material.replace("_", " ")} · ${b.storeys} storey · ${b.has_basement ? "basement" : "no basement"} ·
      value ${fmt$(b.structure_value)}<br>
      <div style="margin-top:6px">damage across ${d.losses.length} runs:</div>
      <div>mean <b style="color:var(--ink)">${fmt$(d.mean)}</b> · P95 <b style="color:var(--ink)">${fmt$(d.p95)}</b>
      · any-damage prob <b style="color:var(--ink)">${(d.prob_any_damage * 100).toFixed(0)}%</b></div>
      ${spark}</div>`;
  } catch (e) {
    panel.innerHTML = `<div class="panel">no results for this building</div>`;
  }
}
function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

function sparkHist(values) {
  const max = Math.max(...values, 1);
  const nb = 16, counts = new Array(nb).fill(0);
  for (const v of values) counts[Math.min(Math.floor(v / max * nb), nb - 1)]++;
  const cmax = Math.max(...counts, 1);
  const W = 240, H = 54;
  let bars = "";
  counts.forEach((c, i) => {
    const h = c / cmax * (H - 12);
    if (!h) return;
    bars += `<rect x="${i * W / nb + 1}" y="${H - h}" width="${W / nb - 2}" height="${h}" rx="2" fill="${S1}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}">${bars}
    <line x1="0" x2="${W}" y1="${H}" y2="${H}" stroke="${BASE}" stroke-width="1"/></svg>`;
}

/* ================= static (prebaked) mode ================= */
/* A static deployment has no solver behind it: the twin and a fixed set of
 * scenario ensembles were computed at build time. The setup screen becomes a
 * picker over those presets; everything downstream is unchanged. */
function applyStaticMode(manifest) {
  document.body.classList.add("static-mode");
  $("btn-go").textContent = "Load scenario ensemble";
  $("place").value = manifest.twin.name;
  for (const el of [$("place"), $("mode"), $("nruns")]) el.disabled = true;
  const opt = document.createElement("option");
  opt.textContent = `${manifest.n_runs} (prebaked)`;
  opt.selected = true;
  $("nruns").appendChild(opt);
  $("static-note").style.display = "block";
  $("static-note").innerHTML =
    `Prebaked demo — the ensembles below were simulated with the full Python ` +
    `solver at build time (${manifest.generated_at.slice(0, 10)}) and shipped as ` +
    `static data, so there is no server to wait on. ` +
    `<a href="${manifest.repo_url}#running-it-yourself" target="_blank" rel="noopener">` +
    `Run it locally</a> to build twins for other places and launch your own ensembles.`;

  const wrap = $("preset-list");
  wrap.innerHTML = "";
  manifest.presets.forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "preset-chip" + (i === 0 ? " selected" : "");
    b.innerHTML = `<b>${p.label}</b><span>${p.description}</span>`;
    b.onclick = () => {
      state.preset = p.id;
      wrap.querySelectorAll(".preset-chip").forEach(x => x.classList.remove("selected"));
      b.classList.add("selected");
    };
    wrap.appendChild(b);
  });
  state.preset = manifest.presets[0].id;
}

/* ================= boot ================= */
(async () => {
  try {
    const manifest = await API.init();
    if (manifest) applyStaticMode(manifest);
    await loadHazards();
  } catch (e) {
    $("error-msg").style.display = "block";
    $("error-msg").textContent = `could not reach the CitySim backend: ${e.message || e}`;
  }
})();
