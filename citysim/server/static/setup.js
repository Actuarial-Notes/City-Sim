/* CitySim Setup — step 1 of the workflow.
 *
 * The whole point of this screen in V2 is that nothing about the model is
 * hidden. Every constant that moves a dollar figure is on a tab here, with its
 * range, its units, what it affects and where it came from — and in live mode
 * you can move it before you run.
 *
 * Controls are not hand-written. The backend publishes a declarative spec
 * (`/api/hazards/flood/assumptions` and `/api/twin/params`) and this file
 * renders whatever is in it, so declaring a new assumption in Python makes it
 * visible and adjustable with no change here.
 *
 * In prebaked (static) mode every input is disabled but every curve, table and
 * citation still renders: the numbers are not adjustable because the ensembles
 * were computed at build time, not because they are secret.
 */
"use strict";

(function () {
const C = window.CitySimCharts;
const $ = id => document.getElementById(id);

const TABS = [
  { id: "region", label: "Region", sub: "the ground being modelled" },
  { id: "building_stock", label: "Building stock", sub: "what the city is made of" },
  { id: "sewer", label: "Sewer network", sub: "the buried half" },
  { id: "storm", label: "Storm & IDF", sub: "how much rain, how fast" },
  { id: "vulnerability", label: "Vulnerability", sub: "depth to dollars" },
  { id: "economics", label: "Economics", sub: "what it costs to rebuild" },
  { id: "sampling", label: "Monte-Carlo", sub: "the uncertainty sampled" },
  { id: "sources", label: "Sources", sub: "provenance & limits" },
];

class Setup {
  constructor(state, onChange) {
    this.state = state;              // shared app state
    this.onChange = onChange;        // called when any knob moves
    this.tab = "region";
    this.readonly = false;
    this.hazardValues = {};          // knob id → value
    this.twinValues = {};            // param id → value
  }

  /* spec payloads from the backend */
  init(assumptions, twinParams, readonly) {
    this.assumptions = assumptions;
    this.twinParams = twinParams;
    this.readonly = !!readonly;
    this.hazardValues = { ...assumptions.defaults };
    this.twinValues = { ...twinParams.defaults };
    this.knobById = {};
    for (const k of assumptions.knobs) this.knobById[k.id] = k;
    this.paramById = {};
    for (const p of twinParams.params) this.paramById[p.id] = p;
    this.sourceById = {};
    for (const s of [...assumptions.sources, ...twinParams.sources])
      this.sourceById[s.id] = s;
    this._renderTabs();
    this.show(this.tab);
  }

  /* what gets POSTed */
  hazardOptions() { return { ...this.hazardValues }; }
  twinParamValues() { return { ...this.twinValues }; }

  /* Twin-affecting knobs need the twin rebuilt; hazard knobs only need a new
   * ensemble against the existing one. */
  twinIsDirty() {
    const d = this.state.builtTwinParams;
    if (!d) return true;
    return Object.keys(this.twinValues).some(k => this.twinValues[k] !== d[k]);
  }

  /* ----------------------------------------------------------- chrome -- */
  _renderTabs() {
    const rail = $("setup-tabs");
    rail.innerHTML = "";
    for (const t of TABS) {
      const b = document.createElement("button");
      b.className = "tab" + (t.id === this.tab ? " active" : "");
      b.dataset.tab = t.id;
      b.innerHTML = `<b>${t.label}</b><span>${t.sub}</span>`;
      b.onclick = () => this.show(t.id);
      rail.appendChild(b);
    }
  }

  show(id) {
    this.tab = id;
    for (const b of $("setup-tabs").children)
      b.classList.toggle("active", b.dataset.tab === id);
    const panel = $("setup-panel");
    panel.innerHTML = "";
    panel.scrollTop = 0;
    ({
      region: () => this._region(panel),
      building_stock: () => this._group(panel, "building_stock"),
      sewer: () => this._group(panel, "sewer"),
      storm: () => this._storm(panel),
      vulnerability: () => this._vulnerability(panel),
      economics: () => this._economics(panel),
      sampling: () => this._sampling(panel),
      sources: () => this._sources(panel),
    })[id]();
  }

  /* ------------------------------------------------------- control kit -- */
  _sourceTag(sourceId) {
    const s = this.sourceById[sourceId];
    if (!s) return "";
    const link = s.url
      ? `<a href="${s.url}" target="_blank" rel="noopener">${s.title}</a>` : s.title;
    return `<div class="src">${link}<span class="fid">${s.fidelity || ""}</span></div>`;
  }

  /* One control, rendered from a spec entry. Handles float/int/bool, shows the
   * default, offers a reset, and carries its own provenance. */
  _knob(spec, get, set) {
    const row = document.createElement("div");
    row.className = "knob" + (spec.readonly ? " readonly" : "");
    const value = get(spec.id);
    const disabled = this.readonly || spec.readonly;

    const head = document.createElement("div");
    head.className = "knob-head";
    head.innerHTML =
      `<label for="k-${spec.id}">${spec.label}</label>` +
      `<span class="val" id="v-${spec.id}"></span>`;
    row.appendChild(head);

    if (spec.type === "bool") {
      const wrap = document.createElement("label");
      wrap.className = "switch";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = `k-${spec.id}`;
      cb.checked = !!value;
      cb.disabled = disabled;
      cb.onchange = () => { set(spec.id, cb.checked); this._sync(spec, get); this.onChange(spec); };
      wrap.appendChild(cb);
      wrap.appendChild(document.createElement("i"));
      row.appendChild(wrap);
    } else {
      const bar = document.createElement("div");
      bar.className = "knob-input";
      const slider = document.createElement("input");
      slider.type = "range";
      slider.id = `k-${spec.id}`;
      slider.min = spec.min;
      slider.max = spec.max;
      slider.step = spec.step || 0.01;
      slider.value = value;
      slider.disabled = disabled;
      const num = document.createElement("input");
      num.type = "number";
      num.className = "num";
      num.min = spec.min;
      num.max = spec.max;
      num.step = spec.step || 0.01;
      num.value = value;
      num.disabled = disabled;

      const apply = v => {
        const clamped = Math.min(Math.max(Number(v), spec.min), spec.max);
        set(spec.id, spec.type === "int" ? Math.round(clamped) : clamped);
        slider.value = get(spec.id);
        num.value = get(spec.id);
        this._sync(spec, get);
        this.onChange(spec);
      };
      slider.oninput = () => apply(slider.value);
      num.onchange = () => apply(num.value);
      bar.appendChild(slider);
      bar.appendChild(num);
      if (spec.unit) {
        const u = document.createElement("span");
        u.className = "unit";
        u.textContent = spec.unit;
        bar.appendChild(u);
      }
      row.appendChild(bar);

      if (spec.presets && !disabled) {
        const chips = document.createElement("div");
        chips.className = "preset-row";
        for (const p of spec.presets) {
          const c = document.createElement("button");
          c.className = "mini";
          c.textContent = p.label;
          c.onclick = () => apply(p.value);
          chips.appendChild(c);
        }
        row.appendChild(chips);
      }
    }

    const meta = document.createElement("div");
    meta.className = "knob-meta";
    meta.innerHTML =
      `<p>${spec.description || ""}</p>` +
      (spec.affects ? `<div class="affects">moves: ${spec.affects}</div>` : "") +
      this._sourceTag(spec.source);
    row.appendChild(meta);

    if (!disabled) {
      const reset = document.createElement("button");
      reset.className = "reset";
      reset.title = `reset to default (${spec.default})`;
      reset.textContent = "↺";
      reset.onclick = () => {
        set(spec.id, spec.default);
        const s = $(`k-${spec.id}`);
        if (spec.type === "bool") s.checked = !!spec.default;
        else { s.value = spec.default; s.parentElement.querySelector(".num").value = spec.default; }
        this._sync(spec, get);
        this.onChange(spec);
      };
      head.appendChild(reset);
    }

    this._syncLater(spec, get);
    return row;
  }

  _syncLater(spec, get) {
    // the value badge needs the node in the document first
    setTimeout(() => this._sync(spec, get), 0);
  }

  _sync(spec, get) {
    const badge = $(`v-${spec.id}`);
    if (!badge) return;
    const v = get(spec.id);
    badge.textContent = spec.type === "bool" ? (v ? "on" : "off") : C.fmtNum(v);
    badge.classList.toggle("changed", v !== spec.default);
  }

  _hazardGet = id => this.hazardValues[id];
  _hazardSet = (id, v) => { this.hazardValues[id] = v; };
  _twinGet = id => this.twinValues[id];
  _twinSet = (id, v) => { this.twinValues[id] = v; };

  _knobsFor(group) {
    const out = [];
    for (const k of this.assumptions.knobs)
      if (k.group === group) out.push(this._knob(k, this._hazardGet, this._hazardSet));
    return out;
  }

  _paramsFor(group) {
    const out = [];
    for (const p of this.twinParams.params)
      if (p.group === group) out.push(this._knob(p, this._twinGet, this._twinSet));
    return out;
  }

  _section(panel, title, blurb) {
    const s = document.createElement("section");
    s.className = "setup-section";
    s.innerHTML = `<h3>${title}</h3>` + (blurb ? `<p class="blurb">${blurb}</p>` : "");
    panel.appendChild(s);
    return s;
  }

  _viz(parent, title, note) {
    const box = document.createElement("div");
    box.className = "viz";
    box.innerHTML = `<h4>${title}</h4>` + (note ? `<p class="note">${note}</p>` : "");
    const mount = document.createElement("div");
    box.appendChild(mount);
    parent.appendChild(box);
    return mount;
  }

  _grid(parent, nodes) {
    const g = document.createElement("div");
    g.className = "knob-grid";
    for (const n of nodes) g.appendChild(n);
    parent.appendChild(g);
    return g;
  }

  /* =========================== tab: region =========================== */
  _region(panel) {
    const grp = this.twinParams.groups.find(g => g.id === "region");
    const s = this._section(panel, "Region & terrain", grp && grp.blurb);

    const twin = this.state.twin;
    const scene = this.state.scene;

    const modeBox = document.createElement("div");
    modeBox.className = "field";
    modeBox.innerHTML = `<label for="data-mode">Data source</label>`;
    const sel = document.createElement("select");
    sel.id = "data-mode";
    sel.disabled = this.readonly;
    for (const [v, t] of [
      ["hamilton", "Hamilton, Ontario — real street grid, escarpment and harbour"],
      ["synthetic", "Procedural Hamilton-like region (fully offline)"],
      ["auto", "Any place — live OpenStreetMap footprints over procedural terrain"],
    ]) {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      sel.appendChild(o);
    }
    sel.value = this.state.dataMode || "hamilton";
    sel.onchange = () => { this.state.dataMode = sel.value; this.onChange({ id: "mode" }); };
    modeBox.appendChild(sel);
    s.appendChild(modeBox);

    const place = document.createElement("div");
    place.className = "field";
    place.innerHTML = `<label for="place">Place</label>`;
    const inp = document.createElement("input");
    inp.type = "text";
    inp.id = "place";
    inp.value = this.state.place || "Hamilton (lower city)";
    inp.disabled = this.readonly;
    inp.oninput = () => { this.state.place = inp.value; };
    place.appendChild(inp);
    s.appendChild(place);

    if (twin) {
      const g = twin.grid;
      const facts = [
        ["Grid", `${g.nx} × ${g.ny} cells @ ${g.cell_size} m`],
        ["Extent", `${(g.extent_m[0] / 1000).toFixed(2)} × ${(g.extent_m[1] / 1000).toFixed(2)} km`],
        ["Elevation", twin.elevation_range
          ? `${twin.elevation_range[0]} – ${twin.elevation_range[1]} m ASL` : "—"],
        ["Buildings", twin.buildings.toLocaleString()],
        ["Households", twin.households.toLocaleString()],
        ["Sewer nodes", `${twin.sewer_nodes} (${twin.combined_sewer_nodes} combined)`],
      ];
      const tiles = document.createElement("div");
      tiles.className = "mini-tiles";
      tiles.innerHTML = facts.map(([k, v]) =>
        `<div><span>${k}</span><b>${v}</b></div>`).join("");
      s.appendChild(tiles);
    } else {
      const hint = document.createElement("p");
      hint.className = "blurb";
      hint.textContent = "Build the twin to see its terrain profile and land cover.";
      s.appendChild(hint);
    }

    if (scene) {
      // south→north section: the escarpment toe down to the harbour, which is
      // the whole hydrological story of the lower city in one line
      const [ny, nx] = scene.shape;
      const pts = [];
      for (let i = 0; i < ny; i += Math.max(1, Math.floor(ny / 120))) {
        let sum = 0;
        for (let j = 0; j < nx; j++) sum += scene.dtm[i * nx + j];
        pts.push([i * scene.cell_size / 1000, sum / nx]);
      }
      const mount = this._viz(s, "Terrain profile, south to north",
        "Mean ground elevation across the width of the twin. Left is the escarpment " +
        "toe, right is the harbour — water runs right.");
      C.curves(mount, [{ label: "mean ground elevation", points: pts, color: C.S2 }], {
        xLabel: "km north from the south edge", yLabel: "m ASL",
        yMax: Math.max(...pts.map(p => p[1])) * 1.02,
        xFmt: v => v.toFixed(1), yFmt: v => v.toFixed(0), legend: false, height: 200,
      });

      const counts = {};
      const names = scene.landcover_classes || {};
      for (const c of scene.landcover) counts[c] = (counts[c] || 0) + 1;
      const total = scene.landcover.length;
      const entries = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => [names[k] || `class ${k}`, v,
                          `${(v / total * 100).toFixed(1)}%`]);
      const lcMount = this._viz(s, "Land cover",
        "Drives roughness, imperviousness and infiltration on every cell.");
      C.bars(lcMount, entries, { compact: true, color: C.S3, fmt: v => v.toLocaleString() });
    }

    const roBox = this._section(panel, "Geography (read-only)",
      "These describe Hamilton rather than a modelling choice, so they are shown in " +
      "full but not offered for editing.");
    this._grid(roBox, this._paramsFor("region"));
  }

  /* ====================== tabs: stock / sewer ======================== */
  _group(panel, group) {
    const grp = this.twinParams.groups.find(g => g.id === group);
    const s = this._section(panel, grp ? grp.label : group, grp && grp.blurb);
    this._grid(s, this._paramsFor(group));

    const twin = this.state.twin;
    if (group === "building_stock" && twin && twin.stock) {
      const st = twin.stock;
      const decades = Object.entries(st.decades).map(([k, v]) => [`${k}s`, v]);
      C.bars(this._viz(s, "Construction era",
        `Built stock as generated — median year ${st.median_year}. Age drives ` +
        `material, and material drives which depth-damage curve applies.`),
        decades, { compact: true, color: C.S1 });
      C.bars(this._viz(s, "Wall material",
        "Only masonry and wood frame have distinct basement curves."),
        Object.entries(st.materials).sort((a, b) => b[1] - a[1]),
        { compact: true, color: C.S2 });
      C.bars(this._viz(s, "Storeys", ""),
        Object.entries(st.storeys), { compact: true, color: C.S3 });
      const bs = document.createElement("p");
      bs.className = "readout";
      bs.innerHTML = `<b>${(st.basement_share * 100).toFixed(0)}%</b> of buildings have ` +
        `a basement — the population exposed to sewer backup at all.`;
      s.appendChild(bs);
    }

    if (group === "sewer" && twin) {
      const share = twin.sewer_nodes
        ? twin.combined_sewer_nodes / twin.sewer_nodes : 0;
      const r = document.createElement("p");
      r.className = "readout";
      r.innerHTML = `<b>${twin.combined_sewer_nodes}</b> of <b>${twin.sewer_nodes}</b> ` +
        `nodes (${(share * 100).toFixed(0)}%) are on combined sewers. Only these can ` +
        `produce basement backup — the dominant damage mechanism in this model.`;
      s.appendChild(r);
    }
    if (group === "sewer") {
      const p = this.hazardValues;
      const mount = this._viz(s, "The backup pathway",
        "How street-level surcharge reaches a basement floor, and which constant " +
        "governs each step.");
      C.crossSection(mount, {
        sill_depth: p.sill_depth, backup_freeboard: p.backup_freeboard,
        lateral_factor: p.lateral_factor,
        basement_depth: (this.twinValues.basement_depth_min +
                         this.twinValues.basement_depth_max) / 2,
        first_floor_height: (this.twinValues.first_floor_height_min +
                             this.twinValues.first_floor_height_max) / 2,
      });
    }
  }

  /* ========================== tab: storm ============================= */
  _storm(panel) {
    const s = this._section(panel, "Storm & IDF",
      "Rainfall depth comes from a Hamilton-area intensity-duration-frequency table. " +
      "The ensemble does not run 'the 100-year storm' — it samples the annual " +
      "exceedance distribution, so the tail is populated by rare events rather than " +
      "assumed.");

    const hz = document.createElement("div");
    hz.className = "field";
    hz.innerHTML = `<label>Hazard module</label><div class="hazards" id="hazard-list"></div>`;
    s.appendChild(hz);

    const curve = this.assumptions.curves.idf;
    const idfMount = this._viz(s, curve.label, curve.note);
    const ddMount = this._viz(s, "Design-storm hyetograph",
      "The Chicago storm actually handed to the solver, at the midpoint of the " +
      "sampled duration and peak-timing ranges.");

    this._grid(s, this._knobsFor("storm"));
    this._redrawStorm = () => {
      const p = this.hazardValues;
      const table = curve.points;
      // same log-T interpolation the solver uses, so the picture cannot drift
      const depth1h = T => {
        const keys = table.map(q => q.T), vals = table.map(q => q.mm);
        const lt = Math.log(Math.min(Math.max(T, 1.01), p.return_period_cap));
        const logs = keys.map(Math.log);
        let i1 = logs.findIndex(v => v >= lt);
        if (i1 <= 0) i1 = lt <= logs[0] ? 1 : logs.length - 1;
        const i0 = i1 - 1;
        const f = (lt - logs[i0]) / (logs[i1] - logs[i0]);
        return (vals[i0] + f * (vals[i1] - vals[i0])) * p.idf_scale * p.climate_factor;
      };
      const series = [2, 5, 10, 25, 100].map(T => ({
        label: `${T}-year`,
        points: [0.5, 1, 2, 3, 4, 6, 12, 24].map(
          d => [d, depth1h(T) * Math.pow(d, 1 - p.idf_decay_c)]),
      }));
      // fixed for the same reason as the depth-damage axis: otherwise scaling
      // every curve rescales the axis with it and nothing appears to change
      C.curves(idfMount, series, {
        xLabel: "storm duration (hours)", yLabel: "total rainfall (mm)",
        xFmt: v => `${v}h`, yFmt: v => v.toFixed(0), yMax: 130, clip: true,
      });
      const durMid = (p.duration_min_h + p.duration_max_h) / 2;
      const peakMid = (p.peak_frac_min + p.peak_frac_max) / 2;
      C.hyetograph(ddMount, depth1h(25) * Math.pow(durMid, 1 - p.idf_decay_c),
                   durMid, peakMid, p.idf_decay_c);
    };
    this._redrawStorm();
  }

  /* ====================== tab: vulnerability ======================== */
  _vulnerability(panel) {
    const s = this._section(panel, "Vulnerability",
      "Depth-damage curves turn a water depth into a fraction of a building's value. " +
      "These are shaped after the southern-Ontario literature and calibrated so a " +
      "full-surcharge basement backup lands in the published $40–45k range — they are " +
      "not digitised source curves, and the severity knob is the honest uncertainty " +
      "on that calibration.");

    const dd = this.assumptions.curves.depth_damage;
    const mount = this._viz(s, dd.label, dd.note);
    const xsMount = this._viz(s, "How water gets in",
      "Three mechanisms, evaluated per building per run. The worst basement pathway " +
      "wins; main-floor flooding is additive and by far the most expensive.");

    this._grid(s, this._knobsFor("vulnerability"));

    const mech = this.assumptions.curves.mechanisms;
    const list = document.createElement("div");
    list.className = "mech-list";
    list.innerHTML = mech.items.map(m =>
      `<div><b>${m.label}</b><p>${m.description}</p></div>`).join("");
    s.appendChild(list);

    this._redrawVuln = () => {
      const p = this.hazardValues;
      const lo = Math.exp(-0.5 * p.ddc_sigma), hi = Math.exp(0.5 * p.ddc_sigma);
      const series = dd.series.map(x => ({
        label: x.label,
        points: x.points.map(q => [q[0], q[1] * p.ddc_severity]),
        band: p.ddc_sigma > 0.01 ? [lo, hi] : null,
      }));
      // Pinned to the full 0–100% of value. Auto-scaling would rescale by
      // exactly the factor the severity knob applies, so the curves would never
      // appear to move however far the slider went — and a curve running past
      // 100% is itself worth seeing, because the model does not cap loss at the
      // building's value.
      C.curves(mount, series, {
        xLabel: "water depth (m)", yLabel: "fraction of value lost",
        xFmt: v => `${v.toFixed(1)}m`, yFmt: v => `${(v * 100).toFixed(0)}%`,
        yMax: 1.0, clip: true, height: 270,
      });
      C.crossSection(xsMount, {
        sill_depth: p.sill_depth, backup_freeboard: p.backup_freeboard,
        lateral_factor: p.lateral_factor,
        basement_depth: (this.twinValues.basement_depth_min +
                         this.twinValues.basement_depth_max) / 2,
        first_floor_height: (this.twinValues.first_floor_height_min +
                             this.twinValues.first_floor_height_max) / 2,
      });
    };
    this._redrawVuln();
  }

  /* ========================= tab: economics ========================= */
  _economics(panel) {
    const grp = this.twinParams.groups.find(g => g.id === "economics");
    const s = this._section(panel, "Economics", grp && grp.blurb);
    this._grid(s, this._paramsFor("economics"));

    const out = document.createElement("div");
    out.className = "readout";
    s.appendChild(out);
    this._redrawEcon = () => {
      const twin = this.state.twin;
      if (!twin || !twin.exposure) {
        out.innerHTML = "Build the twin to see total exposure.";
        return;
      }
      let struct = 0, contents = 0, n = 0;
      for (const e of Object.values(twin.exposure)) {
        struct += e.structure; contents += e.contents; n += e.count;
      }
      // the built twin carries the parameters it was built with; changing a knob
      // here only takes effect on the next build, and saying so beats a number
      // that quietly disagrees with the map
      const dirty = this.twinIsDirty();
      out.innerHTML =
        `<b>${C.fmt$(struct + contents)}</b> total exposure across ` +
        `${n.toLocaleString()} buildings — ${C.fmt$(struct)} structure, ` +
        `${C.fmt$(contents)} contents.` +
        (dirty ? `<div class="warn">Reflects the twin as built. Rebuild to apply the ` +
                 `values above.</div>` : "");
      const entries = Object.entries(twin.exposure)
        .map(([k, e]) => [k, e.structure + e.contents, `${e.count} buildings`])
        .sort((a, b) => b[1] - a[1]);
      C.bars(mount, entries, { compact: true, color: C.S2, fmt: C.fmt$ });
    };
    const mount = this._viz(s, "Exposure by use", "Replacement cost plus contents.");
    this._redrawEcon();
  }

  /* ======================== tab: Monte-Carlo ======================== */
  _sampling(panel) {
    const s = this._section(panel, "Monte-Carlo",
      "Each run is one year's worst storm. Seven dimensions are drawn by Latin " +
      "Hypercube, so the ensemble covers the space evenly rather than clumping. " +
      "Every run carries its own seed and is exactly reproducible.");

    const ens = document.createElement("div");
    ens.className = "row";
    ens.innerHTML =
      `<div class="field"><label for="nruns">Runs in the ensemble</label>
        <select id="nruns" ${this.readonly ? "disabled" : ""}>
          <option>48</option><option selected>96</option>
          <option>150</option><option>300</option></select></div>
      <div class="field"><label for="seed">Seed</label>
        <input type="number" id="seed" value="${this.state.seed}"
               ${this.readonly ? "disabled" : ""}></div>`;
    s.appendChild(ens);
    ens.querySelector("#seed").onchange = e => {
      this.state.seed = parseInt(e.target.value) || 1234;
    };

    const dims = [
      ["Return period", 1.01, this.hazardValues.return_period_cap, 1.01, 1000, "yr"],
      ["Storm duration", this.hazardValues.duration_min_h,
       this.hazardValues.duration_max_h, 0.5, 24, "h"],
      ["Peak timing", this.hazardValues.peak_frac_min,
       this.hazardValues.peak_frac_max, 0, 1, ""],
      ["Antecedent moisture", this.hazardValues.infil_min,
       this.hazardValues.infil_max, 0, 2, "×"],
      ["Surface roughness", this.hazardValues.manning_min,
       this.hazardValues.manning_max, 0.5, 2, "×"],
      ["Pipe capacity", this.hazardValues.blockage_min,
       this.hazardValues.blockage_max, 0.2, 1, "×"],
      ["Depth-damage draw", Math.exp(-0.5 * this.hazardValues.ddc_sigma),
       Math.exp(0.5 * this.hazardValues.ddc_sigma), 0.3, 2.5, "×"],
    ];
    const box = document.createElement("div");
    box.className = "strips";
    s.appendChild(box);
    for (const [label, lo, hi, min, max, unit] of dims) {
      const row = document.createElement("div");
      row.className = "strip-row";
      row.innerHTML = `<span>${label}</span>`;
      const mount = document.createElement("div");
      row.appendChild(mount);
      box.appendChild(row);
      C.rangeStrip(mount, lo, hi, min, max, { unit });
    }

    this._grid(s, this._knobsFor("sampling"));

    const mit = this._section(panel, "Mitigation",
      "Interventions tested against the same storms, so the difference is the " +
      "intervention and not the weather.");
    this._grid(mit, this._knobsFor("mitigation"));

    const adv = this._section(panel, "Solver",
      "Numerical settings. These change run time and stability, not the physics " +
      "being represented.");
    adv.classList.add("advanced");
    this._grid(adv, this._knobsFor("solver"));
  }

  /* ========================== tab: sources ========================== */
  _sources(panel) {
    const s = this._section(panel, "Sources",
      "What every number in this model traces back to, and how faithfully.");
    const tbl = document.createElement("table");
    tbl.className = "src-table";
    tbl.innerHTML =
      `<tr><th>Source</th><th>Informs</th><th>Fidelity</th></tr>` +
      [...this.assumptions.sources, ...this.twinParams.sources].map(x => `
        <tr>
          <td><b>${x.url ? `<a href="${x.url}" target="_blank" rel="noopener">${x.title}</a>`
                          : x.title}</b>
              <span>${x.publisher}${x.year ? `, ${x.year}` : ""}</span>
              ${x.note ? `<p>${x.note}</p>` : ""}</td>
          <td>${x.informs || ""}</td>
          <td><span class="fid ${x.fidelity}">${x.fidelity || ""}</span></td>
        </tr>`).join("");
    s.appendChild(tbl);

    const lim = this._section(panel, "Honest limitations",
      "Where this model is a useful approximation rather than an answer.");
    const ul = document.createElement("div");
    ul.className = "limits";
    ul.innerHTML = this.assumptions.limitations.map(l =>
      `<div><b>${l.title}</b><p>${l.detail}</p></div>`).join("");
    lim.appendChild(ul);

    if (this.state.twin && this.state.twin.sources) {
      const prov = this._section(panel, "This twin was built from", "");
      const list = document.createElement("ul");
      list.className = "prov";
      list.innerHTML = this.state.twin.sources.map(x => `<li>${x}</li>`).join("");
      prov.appendChild(list);
    }
  }

  /* Redraw whichever live visual belongs to the current tab. Called on every
   * knob move — cheap, because only one tab is mounted at a time. */
  refresh() {
    if (this.tab === "storm" && this._redrawStorm) this._redrawStorm();
    else if (this.tab === "vulnerability" && this._redrawVuln) this._redrawVuln();
    else if (this.tab === "economics" && this._redrawEcon) this._redrawEcon();
    else if (this.tab === "sewer") this.show("sewer");
  }
}

window.CitySimSetup = Setup;
})();
