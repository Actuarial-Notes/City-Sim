/* CitySim SVG chart kit.
 *
 * Hand-built SVG, no charting library — the front end has no build step and the
 * static export has to be self-contained. Lifted out of app.js in V2 because
 * the Setup screen needs to plot the model's own curves (IDF, depth-damage,
 * sampling ranges, stock distributions) alongside the result charts, and a
 * single chart vocabulary across both keeps them reading as one system.
 *
 * Every chart takes a mount element and re-renders into it, so callers can
 * redraw on every slider move without bookkeeping.
 */
"use strict";

(function () {
const NS = "http://www.w3.org/2000/svg";

const INK = "#ffffff", INK2 = "#c3c2b7", MUTED = "#898781";
const GRID = "#2c2c2a", BASE = "#383835";
const S1 = "#3987e5", S2 = "#d95926", S3 = "#0ca30c", S4 = "#b07fd4", S5 = "#d4b44a";
const SERIES = [S1, S2, S3, S4, S5];

const fmt$ = v => v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B`
  : v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M`
  : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : `$${Math.round(v)}`;

const fmtNum = v => Math.abs(v) >= 1000 ? v.toLocaleString()
  : Math.abs(v) >= 10 ? v.toFixed(0)
  : Math.abs(v) >= 1 ? v.toFixed(1)
  : Math.abs(v) >= 0.01 ? v.toFixed(2) : String(v);

function el(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

/* ------------------------------------------------------------- tooltip -- */
let tipEl = null;
function tip() {
  if (!tipEl) tipEl = document.getElementById("viz-tooltip");
  return tipEl;
}
function showTip(html, x, y) {
  const t = tip();
  if (!t) return;
  t.innerHTML = html;
  t.style.display = "block";
  t.style.left = `${Math.min(x + 14, innerWidth - 210)}px`;
  t.style.top = `${Math.min(y + 12, innerHeight - 90)}px`;
}
function hideTip() { const t = tip(); if (t) t.style.display = "none"; }

/* --------------------------------------------------------------- frame -- */
function frame(mount, W, H, pad) {
  mount.innerHTML = "";
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
  mount.appendChild(svg);
  return { svg, W, H, x0: pad.l, x1: W - pad.r, y0: H - pad.b, y1: pad.t };
}

function axes(f, opts) {
  const { xTicks, yTicks, xLabel, yLabel, xFmt, yFmt } = opts;
  for (const t of yTicks) {
    const y = t.y;
    f.svg.appendChild(el("line", { x1: f.x0, x2: f.x1, y1: y, y2: y,
                                   stroke: GRID, "stroke-width": 1 }));
    f.svg.appendChild(el("text", { x: f.x0 - 7, y: y + 4, "text-anchor": "end",
                                   fill: MUTED, "font-size": 10 },
                         (yFmt || fmtNum)(t.v)));
  }
  f.svg.appendChild(el("line", { x1: f.x0, x2: f.x1, y1: f.y0, y2: f.y0,
                                 stroke: BASE, "stroke-width": 1 }));
  for (const t of xTicks) {
    f.svg.appendChild(el("text", { x: t.x, y: f.y0 + 16, "text-anchor": "middle",
                                   fill: MUTED, "font-size": 10 },
                         (xFmt || fmtNum)(t.v)));
  }
  if (xLabel)
    f.svg.appendChild(el("text", { x: (f.x0 + f.x1) / 2, y: f.H - 2,
                                   "text-anchor": "middle", fill: MUTED,
                                   "font-size": 10 }, xLabel));
  if (yLabel)
    f.svg.appendChild(el("text", {
      x: 10, y: (f.y0 + f.y1) / 2, "text-anchor": "middle", fill: MUTED,
      "font-size": 10, transform: `rotate(-90 10 ${(f.y0 + f.y1) / 2})`,
    }, yLabel));
}

function ticks(lo, hi, n, scale) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const v = lo + (hi - lo) * i / n;
    out.push({ v, x: scale(v), y: scale(v) });
  }
  return out;
}

/* ===================== result charts (from the dashboard) ================ */
function histogram(mount, values, opts) {
  opts = opts || {};
  const f = frame(mount, 460, 240, { l: 48, r: 12, t: 12, b: 34 });
  let max = 0;
  for (const v of values) if (v > max) max = v;
  max = Math.max(max, 1);
  const nb = opts.bins || 18;
  const edges = [], counts = new Array(nb).fill(0);
  for (let i = 0; i <= nb; i++) edges.push(max * i / nb);
  for (const v of values) counts[Math.min(Math.floor(v / max * nb), nb - 1)]++;
  const cmax = Math.max(...counts, 1);
  const sx = v => f.x0 + (f.x1 - f.x0) * v / max;
  const sy = c => f.y0 - (f.y0 - f.y1) * c / cmax;

  axes(f, {
    yTicks: ticks(0, cmax, 4, sy).map(t => ({ v: Math.round(t.v), y: t.y })),
    xTicks: ticks(0, max, 4, sx).map(t => ({ v: t.v, x: t.x })),
    xFmt: opts.xFmt || fmt$, yFmt: v => String(v),
    xLabel: opts.xLabel, yLabel: opts.yLabel || "runs",
  });

  counts.forEach((c, i) => {
    if (!c) return;
    const x = sx(edges[i]) + 1;
    const w = Math.max(sx(edges[i + 1]) - sx(edges[i]) - 2, 2);
    const y = sy(c), h = f.y0 - y;
    const r = el("path", {
      d: roundedBar(x, y, w, h), fill: opts.color || S1,
    });
    r.addEventListener("mousemove", e => showTip(
      `<div class="t">${(opts.xFmt || fmt$)(edges[i])} – ${(opts.xFmt || fmt$)(edges[i + 1])}</div>` +
      `<b>${c} ${c > 1 ? (opts.unit || "runs") : (opts.unitOne || "run")}</b>`,
      e.clientX, e.clientY));
    r.addEventListener("mouseleave", hideTip);
    f.svg.appendChild(r);
  });
}

function roundedBar(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} ` +
         `L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

function exceedance(mount, points) {
  const f = frame(mount, 460, 240, { l: 48, r: 12, t: 12, b: 34 });
  const pts = points.filter(p => p.loss > 0);
  if (!pts.length) {
    mount.innerHTML = `<div class="empty">no non-zero losses in this ensemble</div>`;
    return;
  }
  const lmax = pts[0].loss;
  const sx = l => f.x0 + (f.x1 - f.x0) * l / lmax;
  const sy = p => f.y0 - (f.y0 - f.y1) * p;

  axes(f, {
    yTicks: ticks(0, 1, 4, sy).map(t => ({ v: t.v, y: t.y })),
    xTicks: ticks(0, lmax, 4, sx).map(t => ({ v: t.v, x: t.x })),
    xFmt: fmt$, yFmt: v => `${Math.round(v * 100)}%`,
    xLabel: "annual damage per household", yLabel: "chance of exceeding",
  });

  const d = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.loss).toFixed(1)},${sy(p.prob).toFixed(1)}`).join(" ");
  f.svg.appendChild(el("path", { d: `${d} L${sx(0)},${sy(0)} Z`, fill: S1,
                                 "fill-opacity": 0.10, stroke: "none" }));
  f.svg.appendChild(el("path", { d, fill: "none", stroke: S1, "stroke-width": 2,
                                 "stroke-linejoin": "round" }));

  const cross = el("line", { y1: f.y1, y2: f.y0, stroke: MUTED, "stroke-width": 1,
                             "stroke-dasharray": "3,3", visibility: "hidden" });
  const dot = el("circle", { r: 4, fill: S1, stroke: "#1a1a19", "stroke-width": 2,
                             visibility: "hidden" });
  f.svg.appendChild(cross);
  f.svg.appendChild(dot);
  const hover = el("rect", { x: f.x0, y: f.y1, width: f.x1 - f.x0,
                             height: f.y0 - f.y1, fill: "transparent" });
  hover.addEventListener("mousemove", e => {
    const r = f.svg.getBoundingClientRect();
    const loss = ((e.clientX - r.left) / r.width * f.W - f.x0) / (f.x1 - f.x0) * lmax;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.loss - loss) < Math.abs(best.loss - loss)) best = p;
    cross.setAttribute("x1", sx(best.loss));
    cross.setAttribute("x2", sx(best.loss));
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", sx(best.loss));
    dot.setAttribute("cy", sy(best.prob));
    dot.setAttribute("visibility", "visible");
    showTip(`<div class="t">annual damage / household</div><b>${(best.prob * 100).toFixed(1)}%</b>` +
            ` chance of exceeding <b>${fmt$(best.loss)}</b>`, e.clientX, e.clientY);
  });
  hover.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    hideTip();
  });
  f.svg.appendChild(hover);
}

/* horizontal labelled bars — damage mechanisms, stock breakdowns */
function bars(mount, entries, opts) {
  opts = opts || {};
  mount.innerHTML = "";
  if (!entries.length) {
    mount.innerHTML = `<div class="empty">${opts.empty || "nothing to show"}</div>`;
    return;
  }
  const W = 460, rowH = opts.compact ? 30 : 42;
  const H = 14 + entries.length * rowH;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
  const max = Math.max(...entries.map(e => e[1]), 1);
  const x0 = 10, x1 = W - 84;
  entries.forEach(([k, v, sub], i) => {
    const y = (opts.compact ? 16 : 26) + i * rowH;
    svg.appendChild(el("text", { x: x0, y: y - 6, fill: INK2, "font-size": 11.5 },
                       opts.labels ? (opts.labels[k] || k) : k));
    const w = Math.max((x1 - x0) * v / max, 3);
    svg.appendChild(el("path", { d: roundedBar(x0, y, w, 11),
                                 fill: opts.color || S1 }));
    svg.appendChild(el("text", { x: x0 + w + 8, y: y + 10, fill: INK2,
                                 "font-size": 11.5, "font-weight": 600 },
                       opts.fmt ? opts.fmt(v) : fmtNum(v)));
    if (sub)
      svg.appendChild(el("text", { x: W - 6, y: y - 6, fill: MUTED,
                                   "font-size": 10.5, "text-anchor": "end" }, sub));
  });
  mount.appendChild(svg);
}

/* ==================== assumption charts (the Setup screen) =============== */

/* A multi-series line chart. Used for the depth-damage curve family and the IDF
 * curves — the same numbers the solver interpolates, so the picture cannot drift
 * away from the model. `band` shades an uncertainty envelope around a series. */
function curves(mount, series, opts) {
  opts = opts || {};
  const f = frame(mount, 470, opts.height || 250, { l: 52, r: 14, t: 14, b: 38 });
  let xMax = opts.xMax, yMax = opts.yMax;
  if (xMax === undefined) xMax = Math.max(...series.flatMap(s => s.points.map(p => p[0])));
  if (yMax === undefined) yMax = Math.max(...series.flatMap(s => s.points.map(p => p[1])));
  const xMin = opts.xMin || 0;
  yMax = yMax * 1.06 || 1;
  const sx = v => f.x0 + (f.x1 - f.x0) * (v - xMin) / Math.max(xMax - xMin, 1e-9);
  const sy = v => f.y0 - (f.y0 - f.y1) * v / yMax;

  /* A fixed axis lets a multiplier knob visibly scale its curves — with
   * auto-scaling, multiplying every series by a constant rescales the axis by
   * the same constant and the picture never moves. Anything that runs off the
   * top is clipped to the plot rather than drawn over the heading. */
  let clip = "";
  if (opts.clip) {
    clip = `clip-${Math.random().toString(36).slice(2, 9)}`;
    const cp = el("clipPath", { id: clip });
    cp.appendChild(el("rect", { x: f.x0, y: f.y1, width: f.x1 - f.x0,
                                height: f.y0 - f.y1 }));
    f.svg.appendChild(cp);
  }
  const clipAttr = clip ? { "clip-path": `url(#${clip})` } : {};

  axes(f, {
    yTicks: ticks(0, yMax, 4, sy).map(t => ({ v: t.v, y: t.y })),
    xTicks: ticks(xMin, xMax, opts.xTickCount || 5, sx).map(t => ({ v: t.v, x: t.x })),
    xFmt: opts.xFmt, yFmt: opts.yFmt,
    xLabel: opts.xLabel, yLabel: opts.yLabel,
  });

  series.forEach((s, i) => {
    const color = s.color || SERIES[i % SERIES.length];
    if (s.band) {
      const up = s.points.map((p, k) => `${k ? "L" : "M"}${sx(p[0])},${sy(p[1] * s.band[1])}`);
      const dn = s.points.slice().reverse()
        .map(p => `L${sx(p[0])},${sy(p[1] * s.band[0])}`);
      f.svg.appendChild(el("path", { d: `${up.join(" ")} ${dn.join(" ")} Z`,
                                     fill: color, "fill-opacity": 0.16, stroke: "none",
                                     ...clipAttr }));
    }
    const d = s.points.map((p, k) => `${k ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
    f.svg.appendChild(el("path", {
      d, fill: "none", stroke: color, "stroke-width": s.width || 2,
      "stroke-linejoin": "round", "stroke-dasharray": s.dash || "",
      "stroke-opacity": s.faded ? 0.4 : 1, ...clipAttr,
    }));
    for (const p of s.points) {
      if (p[1] > yMax) continue;
      const dot = el("circle", { cx: sx(p[0]), cy: sy(p[1]), r: 2.6, fill: color,
                                 "fill-opacity": s.faded ? 0.4 : 0.95 });
      dot.addEventListener("mousemove", e => showTip(
        `<div class="t">${s.label}</div><b>${(opts.xFmt || fmtNum)(p[0])}</b> → ` +
        `<b>${(opts.yFmt || fmtNum)(p[1])}</b>`, e.clientX, e.clientY));
      dot.addEventListener("mouseleave", hideTip);
      f.svg.appendChild(dot);
    }
  });

  if (opts.legend !== false) {
    const wrap = document.createElement("div");
    wrap.className = "chart-legend";
    series.forEach((s, i) => {
      const color = s.color || SERIES[i % SERIES.length];
      const item = document.createElement("span");
      item.innerHTML = `<i style="background:${color}"></i>${s.label}`;
      wrap.appendChild(item);
    });
    mount.appendChild(wrap);
  }
}

/* The sampled range of one Monte-Carlo dimension, as a strip against its full
 * allowed span — so "0.3 to 1.3 out of 0 to 2" is one glance, not arithmetic. */
function rangeStrip(mount, lo, hi, min, max, opts) {
  opts = opts || {};
  mount.innerHTML = "";
  const W = 300, H = 26;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart strip" });
  const sx = v => 4 + (W - 8) * (v - min) / Math.max(max - min, 1e-9);
  svg.appendChild(el("rect", { x: 4, y: 10, width: W - 8, height: 6, rx: 3,
                               fill: GRID }));
  svg.appendChild(el("rect", { x: sx(lo), y: 8, width: Math.max(sx(hi) - sx(lo), 3),
                               height: 10, rx: 5, fill: opts.color || S1 }));
  for (const [v, anchor] of [[lo, "start"], [hi, "end"]])
    svg.appendChild(el("text", {
      x: Math.min(Math.max(sx(v), 12), W - 12), y: 6,
      "text-anchor": anchor, fill: INK2, "font-size": 9.5,
    }, `${fmtNum(v)}${opts.unit || ""}`));
  mount.appendChild(svg);
}

/* A design-storm hyetograph preview. Recomputes the same Chicago form the
 * solver uses, so moving the duration or peak slider shows the actual storm
 * that will be simulated. */
function hyetograph(mount, totalMm, durationH, peakFrac, decayC) {
  const n = Math.max(Math.round(durationH * 60), 8);
  const t = [], shape = [];
  const b = 0.08;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const th = (i + 0.5) / 60;
    const tp = peakFrac * durationH;
    const theta = th < tp ? (tp - th) / Math.max(peakFrac, 1e-6)
                          : (th - tp) / Math.max(1 - peakFrac, 1e-6);
    const s = ((1 - decayC) * theta + b) / Math.pow(theta + b, 1 + decayC);
    t.push(th);
    shape.push(s);
    sum += s;
  }
  const pts = shape.map((s, i) => [t[i], s / sum * totalMm * 60]);   // mm/h
  curves(mount, [{ label: "rainfall intensity", points: pts, color: S1, width: 2 }], {
    xLabel: "hours into the storm", yLabel: "mm/h", height: 190,
    xFmt: v => `${v.toFixed(1)}h`, yFmt: v => v.toFixed(0), legend: false,
  });
}

/* An annotated section through a house, showing where each damage mechanism
 * gets in and which constant governs it. The three pathways are the hardest
 * part of the model to hold in your head from a table of numbers. */
function crossSection(mount, p) {
  mount.innerHTML = "";
  const W = 470, H = 250;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
  const gy = 132;                                    // grade line
  const scale = 34;                                  // px per metre
  const bx = 118, bw = 190;

  const ffh = p.first_floor_height !== undefined ? p.first_floor_height : 0.35;
  const bdepth = p.basement_depth !== undefined ? p.basement_depth : 2.1;
  const floorY = gy - ffh * scale;
  const basementY = gy + bdepth * scale;

  svg.appendChild(el("rect", { x: 0, y: gy, width: W, height: H - gy,
                               fill: "#241f1b" }));
  svg.appendChild(el("line", { x1: 0, x2: W, y1: gy, y2: gy, stroke: "#5b5349",
                               "stroke-width": 1.5 }));
  // basement box
  svg.appendChild(el("rect", { x: bx, y: gy, width: bw, height: basementY - gy,
                               fill: "#2f2a26", stroke: "#6a6055", "stroke-width": 1.5 }));
  // above-grade storey
  svg.appendChild(el("rect", { x: bx, y: floorY - 66, width: bw, height: 66 + (gy - floorY),
                               fill: "#3d342e", stroke: "#8d4b39", "stroke-width": 1.5 }));
  svg.appendChild(el("path", { d: `M${bx - 12},${floorY - 66} L${bx + bw / 2},${floorY - 98} ` +
                                  `L${bx + bw + 12},${floorY - 66} Z`,
                               fill: "#4a453f", stroke: "#6a6055" }));

  const note = (x, y, text, color, anchor) =>
    svg.appendChild(el("text", { x, y, fill: color, "font-size": 10.5,
                                 "text-anchor": anchor || "start" }, text));

  // 1 — surface water against the wall, and the sill threshold
  const sillY = gy - p.sill_depth * scale;
  svg.appendChild(el("rect", { x: 0, y: sillY, width: bx, height: gy - sillY,
                               fill: S1, "fill-opacity": 0.35 }));
  svg.appendChild(el("line", { x1: 0, x2: bx + bw, y1: sillY, y2: sillY,
                               stroke: S1, "stroke-dasharray": "4,3" }));
  note(6, sillY - 5, `sill depth ${p.sill_depth.toFixed(2)} m → basement inundation`, S1);

  // 2 — main floor threshold
  svg.appendChild(el("line", { x1: 0, x2: W, y1: floorY, y2: floorY,
                               stroke: S2, "stroke-dasharray": "4,3" }));
  note(W - 6, floorY - 5, `first floor +${ffh.toFixed(2)} m → overland flooding`, S2, "end");

  // 3 — the sewer backup pathway
  const sewerY = gy + 2.6 * scale;
  svg.appendChild(el("line", { x1: 0, x2: W, y1: sewerY, y2: sewerY,
                               stroke: "#8a5a3a", "stroke-width": 6,
                               "stroke-opacity": 0.5 }));
  svg.appendChild(el("path", { d: `M${bx + bw / 2},${sewerY} L${W - 40},${sewerY}`,
                               stroke: S3, "stroke-width": 2, fill: "none",
                               "stroke-dasharray": "5,4" }));
  const hglY = gy - 0.6 * scale;
  svg.appendChild(el("line", { x1: bx + bw, x2: W, y1: hglY, y2: hglY,
                               stroke: S3, "stroke-dasharray": "3,3" }));
  note(W - 6, hglY - 5, "street hydraulic grade line", S3, "end");
  note(bx + 8, basementY - 8,
       `× lateral factor ${p.lateral_factor.toFixed(2)}, ` +
       `− freeboard ${p.backup_freeboard.toFixed(2)} m`, S3);
  note(bx + 8, gy + 16, `basement floor −${bdepth.toFixed(2)} m`, INK2);
  mount.appendChild(svg);
}

/* small inline sparkline histogram, for the building inspector */
function sparkHist(values, color) {
  let max = 1;
  for (const v of values) if (v > max) max = v;
  const nb = 16, counts = new Array(nb).fill(0);
  for (const v of values) counts[Math.min(Math.floor(v / max * nb), nb - 1)]++;
  const cmax = Math.max(...counts, 1);
  const W = 240, H = 50;
  let out = "";
  counts.forEach((c, i) => {
    const h = c / cmax * (H - 10);
    if (h) out += `<rect x="${(i * W / nb + 1).toFixed(1)}" y="${(H - h).toFixed(1)}" ` +
                  `width="${(W / nb - 2).toFixed(1)}" height="${h.toFixed(1)}" rx="2" ` +
                  `fill="${color || S1}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart">${out}` +
         `<line x1="0" x2="${W}" y1="${H}" y2="${H}" stroke="${BASE}" stroke-width="1"/></svg>`;
}

window.CitySimCharts = {
  histogram, exceedance, bars, curves, rangeStrip, hyetograph, crossSection,
  sparkHist, showTip, hideTip, fmt$, fmtNum, SERIES, S1, S2, S3, INK, INK2, MUTED,
};
})();
