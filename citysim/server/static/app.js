/* CitySim SPA shell — state, the three-step workflow, and the pipeline.
 *
 * V2 makes the workflow explicit:
 *
 *   1 Setup      build or reconfigure the twin, and see every model assumption
 *   2 Simulate   run the ensemble, then watch a storm replay
 *   3 Results    the distributions, and a record of what was assumed
 *
 * The screens themselves live in setup.js / replay.js / results.js; this file
 * owns the shared state object, step navigation, and the build → simulate →
 * results pipeline.
 */
"use strict";

const $ = id => document.getElementById(id);

const state = {
  hazard: "flood",
  place: "Hamilton (lower city)",
  dataMode: "hamilton",
  seed: 1234,
  twin: null,               // twin summary from the build job
  builtTwinParams: null,    // the params the current twin was actually built with
  jobId: null,
  results: null,
  scene: null,
  replayable: null,
  preset: null,
  viewer: null,
  step: 1,
  maxStep: 1,
};

let setup, replay, results;

/* ============================== navigation ============================== */
const SCREENS = { 1: "screen-setup", 2: "screen-sim", 3: "screen-results" };

function goStep(n) {
  if (n > state.maxStep) return;
  state.step = n;
  for (const [k, id] of Object.entries(SCREENS))
    $(id).classList.toggle("active", String(n) === k);
  for (const b of document.querySelectorAll("#stepper .step")) {
    const s = parseInt(b.dataset.step);
    b.classList.toggle("active", s === n);
    b.classList.toggle("done", s < state.maxStep);
    b.disabled = s > state.maxStep;
  }
  if (n === 2 && state.viewer) state.viewer.requestRender();
  if (n !== 2 && replay) replay.pause();
}

function unlock(n) {
  state.maxStep = Math.max(state.maxStep, n);
  goStep(state.step);
}

/* =============================== pipeline =============================== */
function setProgress(frac, msg, detail) {
  $("sim-progress").classList.add("visible");
  $("progress-fill").style.width = `${Math.round(frac * 100)}%`;
  $("progress-msg").textContent = msg || "";
  if (detail !== undefined) $("progress-detail").textContent = detail;
}

async function run() {
  const btn = $("btn-go");
  btn.disabled = true;
  $("error-msg").classList.remove("visible");
  goStep(2);
  state.maxStep = Math.max(state.maxStep, 2);
  goStep(2);
  $("sim-stage").classList.add("running");
  $("viewer-stage").classList.remove("ready");

  try {
    // ---- 1. the twin. Only rebuild when a twin-affecting parameter moved:
    // the ensemble is the expensive part, and re-running it against the same
    // twin is a legitimate and much faster thing to want.
    if (!state.twin || setup.twinIsDirty() || state.dataMode !== state.builtMode) {
      setProgress(0.02, "building the digital twin…",
                  `${state.place} · ${state.dataMode}`);
      state.twin = await API.buildTwin(
        { place: state.place, mode: state.dataMode, params: setup.twinParamValues() },
        (p, msg) => setProgress(0.02 + p * 0.18, `twin: ${msg}`));
      state.builtTwinParams = setup.twinParamValues();
      state.builtMode = state.dataMode;
      state.scene = await API.scene(state.twin.id);
      state.sceneDirty = true;
    }
    renderTwinSummary();

    // ---- 2. the ensemble
    const nRuns = parseInt(($("nruns") || {}).value || 96);
    setProgress(0.22, `running ${nRuns} storms…`, "");
    const t0 = performance.now();
    state.jobId = await API.simulate({
      twinId: state.twin.id, hazard: state.hazard, nRuns,
      seed: state.seed, options: setup.hazardOptions(), presetId: state.preset,
    }, (p, msg) => setProgress(0.22 + p * 0.7, `ensemble: ${msg}`,
                               `${((performance.now() - t0) / 1000).toFixed(0)}s elapsed`));

    // ---- 3. results, then hand straight into the replay
    setProgress(0.95, "reducing distributions…", "");
    state.results = await API.results(state.jobId);
    state.replayable = API.replayableRuns(state.jobId);
    setProgress(1, "done", "");

    unlock(3);
    results.render();
    initViewer();
    $("sim-stage").classList.remove("running");
    $("viewer-stage").classList.add("ready");
    await replay.open(defaultRun());
  } catch (e) {
    $("sim-stage").classList.remove("running");
    $("error-msg").classList.add("visible");
    $("error-msg").textContent = String((e && e.message) || e);
    goStep(1);
  } finally {
    btn.disabled = false;
  }
}

function renderTwinSummary() {
  const t = state.twin;
  if (!t) return;
  $("twin-summary").classList.add("visible");
  $("twin-summary").innerHTML =
    `<b>${t.name}</b> — ${t.buildings.toLocaleString()} buildings · ` +
    `${t.households.toLocaleString()} households · ` +
    `${t.sewer_nodes} sewer nodes (${t.combined_sewer_nodes} combined) · ` +
    `${t.grid.extent_m[0]}×${t.grid.extent_m[1]} m @ ${t.grid.cell_size} m` +
    `<div class="muted">${(t.sources || []).join(" · ")}</div>`;
}

/* Step 2 opens on the median storm rather than the worst: a typical year is a
 * more honest first impression than the tail, and the worst run is one click
 * away in the picker. */
function defaultRun() {
  const reps = state.results.stats.representative_runs;
  const canReplay = r => !state.replayable || state.replayable.includes(r);
  for (const key of ["median", "p90", "p95", "max"])
    if (reps[key] !== undefined && canReplay(reps[key])) return reps[key];
  const runs = [...state.results.run_index].filter(r => canReplay(r.run))
    .sort((a, b) => b.total_loss - a.total_loss);
  return runs.length ? runs[0].run : 0;
}

/* ================================ viewer ================================ */
function initViewer() {
  if (!state.viewer) {
    state.viewer = new CitySimViewer($("gl"));
    state.viewer.setLabelHost($("map-labels"));
    replay.attach(state.viewer);

    $("btn-play").onclick = () => replay.toggle();
    $("scrubber").oninput = () => { replay.pause(); replay.setFrame(parseInt($("scrubber").value)); };
    $("speed").onchange = e => replay.setSpeed(e.target.value);
    $("color-mode").onchange = applyColorMode;
    $("toggle-pipes").onclick = e => {
      state.viewer.showPipes = !state.viewer.showPipes;
      e.target.classList.toggle("on", state.viewer.showPipes);
      state.viewer.requestRender();
    };
    $("toggle-trees").onclick = e => {
      state.viewer.showTrees = !state.viewer.showTrees;
      e.target.classList.toggle("on", state.viewer.showTrees);
      state.viewer.requestRender();
    };
    $("btn-reset-view").onclick = () => state.viewer.resetView();
    for (const b of document.querySelectorAll("#view-mode button"))
      b.onclick = () => {
        state.viewer.setMode(b.dataset.mode);
        for (const o of document.querySelectorAll("#view-mode button"))
          o.classList.toggle("active", o === b);
      };
    $("btn-to-results").onclick = () => goStep(3);
  }

  if (state.sceneDirty || !state.viewer.scene) {
    state.viewer.setScene(state.scene);
    state.sceneDirty = false;
  }

  const sel = $("run-select");
  sel.innerHTML = "";
  const sorted = [...state.results.run_index]
    .filter(r => !state.replayable || state.replayable.includes(r.run))
    .sort((a, b) => b.total_loss - a.total_loss);
  for (const r of sorted) {
    const o = document.createElement("option");
    o.value = r.run;
    o.textContent = `#${r.run} — ${r.return_period.toFixed(0)}yr, ` +
                    `${r.total_mm.toFixed(0)}mm, ${CitySimCharts.fmt$(r.total_loss)}`;
    sel.appendChild(o);
  }
  sel.onchange = () => replay.open(parseInt(sel.value));
  applyColorMode();
}

const LEGENDS = {
  class: [["#8d4b39", "masonry"], ["#b9b3a4", "wood frame"], ["#9a9790", "concrete"],
          ["#7d848a", "steel"]],
  loss: [["#2f4858", "no risk"], ["#a08b4a", "moderate"], ["#8f1d10", "highest $ risk"]],
  age: [["#7a3b2e", "oldest"], ["#c8a86a", "mid-century"], ["#5d8ba0", "newest"]],
  value: [["#37474f", "lowest value"], ["#d98f4a", "highest value"]],
  storeys: [["#37474f", "1 storey"], ["#d98f4a", "6+ storeys"]],
  material: [["#8d4b39", "masonry"], ["#b9b3a4", "wood frame"], ["#9a9790", "concrete"],
             ["#7d848a", "steel"]],
  basement: [["#c9401f", "has a basement"], ["#4a6b7a", "slab / no basement"]],
};

function applyColorMode() {
  const mode = $("color-mode").value;
  state.viewer.recolorBuildings(
    mode, mode === "loss" ? state.results.building_mean_loss : null);
  $("viewer-legend").innerHTML =
    (LEGENDS[mode] || []).map(([c, t]) =>
      `<span><i class="sw" style="background:${c}"></i>${t}</span>`).join("") +
    `<span><i class="sw" style="background:#86b6ef"></i>shallow water</span>` +
    `<span><i class="sw" style="background:#0d366b"></i>deep</span>` +
    `<span><i class="sw" style="background:#f2522e"></i>surcharging manhole</span>`;
}

/* ============================== static mode ============================= */
/* A prebaked deployment has no solver behind it: the twin and a fixed set of
 * ensembles were computed at build time. Every tab still renders in full —
 * the assumptions are not adjustable because there is nothing to re-run, not
 * because they are hidden. */
function applyStaticMode(manifest) {
  document.body.classList.add("static-mode");
  state.place = manifest.twin.name;
  state.twin = manifest.twin;
  $("btn-go").textContent = "Load ensemble →";
  $("static-note").classList.add("visible");
  $("static-note").innerHTML =
    `<b>Prebaked demo.</b> These ensembles were run with the full Python solver at ` +
    `build time (${manifest.generated_at.slice(0, 10)}) and shipped as static data, so ` +
    `there is no server to wait on — and no way to re-run with different assumptions. ` +
    `Every assumption below is still shown in full. ` +
    `<a href="${manifest.repo_url}#running-it-yourself" target="_blank" rel="noopener">` +
    `Run it locally</a> to build twins for other places and launch your own ensembles.`;

  const wrap = $("preset-list");
  $("field-presets").classList.add("visible");
  wrap.innerHTML = "";
  manifest.presets.forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "preset-chip" + (i === 0 ? " selected" : "");
    b.innerHTML = `<b>${p.label}</b><span>${p.description}</span>`;
    b.onclick = () => {
      state.preset = p.id;
      for (const x of wrap.children) x.classList.remove("selected");
      b.classList.add("selected");
    };
    wrap.appendChild(b);
  });
  state.preset = manifest.presets[0].id;
}

/* ================================= boot ================================= */
async function loadHazards() {
  const list = $("hazard-list");
  if (!list) return;
  const hazards = await API.hazards();
  list.innerHTML = "";
  for (const h of hazards) {
    const b = document.createElement("button");
    b.className = "hazard-chip" + (h.name === state.hazard ? " selected" : "");
    b.textContent = h.display_name + (h.available ? "" : " (roadmap)");
    b.disabled = !h.available;
    b.onclick = () => {
      state.hazard = h.name;
      for (const x of list.children) x.classList.remove("selected");
      b.classList.add("selected");
    };
    list.appendChild(b);
  }
}

(async () => {
  try {
    const manifest = await API.init();
    const readonly = !!manifest;

    replay = new CitySimReplay(state);
    results = new CitySimResults(state, run => { goStep(2); replay.open(run); });
    setup = new CitySimSetup(state, spec => {
      // twin-shaped changes invalidate the built twin's summary charts
      setup.refresh();
      if (spec && spec.id === "mode") state.dataMode = $("data-mode").value;
    });

    if (manifest) applyStaticMode(manifest);

    const [assumptions, twinParams] = await Promise.all([
      API.assumptions(state.hazard), API.twinParams(),
    ]);
    setup.init(assumptions, twinParams, readonly);
    await loadHazards();

    for (const b of document.querySelectorAll("#stepper .step"))
      b.onclick = () => goStep(parseInt(b.dataset.step));
    $("btn-go").onclick = run;
    goStep(1);

    // a prebaked deployment already has its twin, so the Region tab can show
    // the real thing immediately instead of an empty placeholder
    if (manifest) {
      state.scene = await API.scene();
      state.sceneDirty = true;
      setup.show(setup.tab);
    }
  } catch (e) {
    $("error-msg").classList.add("visible");
    $("error-msg").textContent =
      `could not reach the CitySim backend: ${(e && e.message) || e}`;
  }
})();
