/* CitySim 3D viewer — orthographic isometric / top-down renderer.
 *
 * V2 changes the camera contract. The old viewer used a perspective orbit
 * camera: dragging rotated the city, which made it hard to hold a mental map of
 * where you were, and a perspective "isometric" is not isometric at all across
 * a two-kilometre scene. The camera here is orthographic with a fixed heading,
 * in two modes — isometric and straight-down 2D — with pan and zoom only. There
 * is deliberately no rotate.
 *
 * Lighting, weather and materials are procedural: the project ships no image
 * assets and the static deploy has to stay self-contained, so surface detail
 * comes from geometry, baked ambient occlusion, and patterns evaluated per
 * fragment from surface-local coordinates.
 *
 * Matrices, mesh assembly and colour helpers live in gl-core.js; the scene
 * meshes themselves are built in viewer-build.js.
 */
"use strict";

(function () {
const { V3, M4, Color, buildProgram } = window.CitySimGL;
const { SceneBuilder } = window.CitySimScene;

/* true isometric: the angle at which the three axes foreshorten equally */
const ISO_PITCH = Math.atan(1 / Math.SQRT2);      // 35.264°
const ISO_YAW = -Math.PI / 4;
const TOP_PITCH = Math.PI / 2 - 1e-4;             // exactly 90° degenerates lookAt
const CAM_DIST = 24000;

/* ================================ shaders ================================ */
const VS = `
attribute vec3 aPos;
attribute vec3 aColor;
attribute vec3 aNormal;
attribute vec4 aParam;    // alpha, ambient occlusion, kind, phase
attribute vec2 aUV;       // surface-local metres

uniform mat4 uMVP;
uniform vec3 uFocus;      // what the camera is looking at

varying vec3 vColor;
varying vec3 vNormal;
varying vec4 vParam;
varying vec2 vUV;
varying float vDist;

void main() {
  gl_Position = uMVP * vec4(aPos, 1.0);
  vColor = aColor;
  vNormal = aNormal;
  vParam = aParam;
  vUV = aUV;
  // Distance from what the camera is looking at, not from the eye. Under a
  // parallel projection the eye is an arbitrary distance away — using it would
  // put every fragment in the same fog band and grey out the whole scene.
  vDist = length(aPos.xy - uFocus.xy);
}`;

const FS = `
precision mediump float;

uniform vec3 uSun;         // direction toward the sun
uniform vec3 uSunColor;
uniform vec3 uSkyColor;    // ambient / fog colour, darkens with the storm
uniform float uStorm;      // 0 clear .. 1 peak rainfall
uniform float uTime;
uniform float uFogNear;
uniform float uFogFar;

varying vec3 vColor;
varying vec3 vNormal;
varying vec4 vParam;
varying vec2 vUV;
varying float vDist;

// window rows on a wall, from surface-local (along, up) metres
float windows(vec2 uv, float storeyH) {
  float sh = max(storeyH, 2.2);
  float row = mod(uv.y - 1.1, sh) / sh;        // position within a storey
  float col = mod(uv.x - 0.6, 3.1) / 3.1;
  float inRow = step(0.20, row) * step(row, 0.62);
  float inCol = step(0.24, col) * step(col, 0.74);
  return inRow * inCol * step(1.6, uv.y);
}

void main() {
  float kind = vParam.z;
  vec3 base = vColor;
  vec3 n = normalize(vNormal);
  float lambert = max(dot(n, uSun), 0.0);
  // Hemispheric ambient rather than a single constant: up-facing surfaces see
  // the sky, vertical ones see less of it. Without this the walls — which are
  // near-perpendicular to a high sun — go almost black and the brick and siding
  // palettes never read at all.
  // Calibrated so a flat, sun-facing surface lands at roughly its base albedo
  // rather than 20% above it — otherwise the ground blows out and the buildings
  // sitting on it look darker than they are.
  float sky = n.z * 0.5 + 0.5;
  float ambient = mix(0.40, 0.62, sky) - uStorm * 0.09;

  if (kind > 2.5 && kind < 3.5) {
    // wall: punch in window rows; glazing reads darker and cooler than brick
    float w = windows(vUV, vParam.w);
    vec3 glass = mix(vec3(0.10, 0.13, 0.17), vec3(0.55, 0.62, 0.70),
                     0.25 + 0.45 * lambert);
    // a few windows light up once the storm darkens the sky
    float lit = step(0.86, fract(sin(floor(vUV.x * 0.32) * 12.9898
                                     + floor(vUV.y * 0.34) * 78.233) * 43758.5453));
    glass = mix(glass, vec3(0.95, 0.82, 0.55), lit * uStorm * 0.8);
    base = mix(base, glass, w);
    base = mix(base, base * 0.72, step(vUV.y, 0.9));      // foundation band
  } else if (kind > 4.5 && kind < 5.5) {
    // road: worn crown, and a dashed centre line on anything above local class
    float edge = smoothstep(0.0, 1.4, abs(vUV.y));
    base = mix(base * 1.18, base, edge);
    float centre = (1.0 - smoothstep(0.10, 0.34, abs(vUV.y))) * vParam.w;
    float dash = step(0.42, fract(vUV.x / 9.0));
    base = mix(base, vec3(0.78, 0.72, 0.42), centre * dash * 0.85);
  }

  vec3 lit3;
  if (kind < 0.5) {
    lit3 = base;                                          // unlit overlay
  } else if (kind > 1.5 && kind < 2.5) {
    // water: diffuse plus a specular glint and a shimmer that never settles
    vec3 v = vec3(0.0, 0.0, 1.0);
    float spec = pow(max(dot(reflect(-uSun, n), v), 0.0), 22.0);
    float ripple = 0.5 + 0.5 * sin(vUV.x * 0.22 + uTime * 1.7)
                             * sin(vUV.y * 0.19 - uTime * 1.3);
    lit3 = base * (0.62 + 0.38 * lambert) + uSkyColor * 0.20
           + uSunColor * spec * (0.35 + 0.3 * ripple) * (1.0 - uStorm * 0.6);
  } else if (kind > 5.5) {
    // foliage: wrapped lighting — a leaf mass has no hard terminator
    float wrap = max((dot(n, uSun) + 0.7) / 1.7, 0.0);
    lit3 = base * (ambient + 0.62 * wrap) * vParam.y;
  } else {
    lit3 = base * (ambient + 0.44 * lambert) * vParam.y;
    lit3 += uSunColor * lambert * 0.07 * (1.0 - uStorm);
  }

  lit3 *= (1.0 - uStorm * 0.22);                          // wet-surface darkening

  float fog = clamp((vDist - uFogNear) / max(uFogFar - uFogNear, 1.0), 0.0, 1.0);
  gl_FragColor = vec4(mix(lit3, uSkyColor, fog * 0.85), vParam.x);
}`;

/* the sky is a full-screen gradient drawn before everything else */
const SKY_VS = `
attribute vec2 aPos;
varying vec2 vP;
void main() { vP = aPos; gl_Position = vec4(aPos, 0.999, 1.0); }`;

const SKY_FS = `
precision mediump float;
uniform vec3 uTop;
uniform vec3 uBottom;
varying vec2 vP;
void main() {
  float t = clamp(vP.y * 0.5 + 0.5, 0.0, 1.0);
  gl_FragColor = vec4(mix(uBottom, uTop, pow(t, 0.85)), 1.0);
}`;

/* ================================= viewer ================================ */
class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl", { antialias: true, alpha: false });
    if (!gl) throw new Error("WebGL unavailable");
    this.gl = gl;

    this.prog = buildProgram(gl, VS, FS,
      ["aPos", "aColor", "aNormal", "aParam", "aUV"],
      ["uMVP", "uFocus", "uSun", "uSunColor", "uSkyColor", "uStorm", "uTime",
       "uFogNear", "uFogFar"]);
    this.skyProg = buildProgram(gl, SKY_VS, SKY_FS, ["aPos"], ["uTop", "uBottom"]);
    this.skyBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.skyBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]),
                  gl.STATIC_DRAW);

    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    this.meshes = {};
    this.scene = null;
    this.showPipes = false;
    this.showTrees = true;
    this.showShadows = true;
    this.colorMode = "class";
    this.lossArr = null;
    this.storm = 0;
    this.onPick = null;
    this.onHover = null;
    this.onModeChange = null;
    this.hoverId = null;
    this.selectedId = null;

    /* Orthographic, fixed heading. `tilt` tweens 0 (straight down) to 1
     * (isometric), so switching modes is a movement rather than a cut. */
    this.cam = { target: [0, 0, 0], zoom: 500, tilt: 1 };
    this._tiltTarget = 1;
    this._t0 = performance.now();
    this._bindInput();
    addEventListener("resize", () => this.requestRender());
  }

  /* ------------------------------------------------------------- scene -- */
  setScene(scene) {
    const gl = this.gl;
    for (const m of Object.values(this.meshes)) this._free(m);
    this.meshes = {};

    this.scene = scene;
    const b = this.builder = new SceneBuilder(scene);

    this.meshes.surround = this._upload(b.surround(), {});
    this.meshes.terrain = this._upload(b.terrain(), {});
    this.meshes.landscape = this._upload(b.landscape(), {});
    this.meshes.streets = this._upload(b.streets(), {});
    this.meshes.shadows = this._upload(b.shadows(), {});
    this.meshes.trees = this._upload(b.trees(), {});
    this._rebuildBuildings();

    const sewer = b.sewer();
    this.meshes.pipes = this._upload(sewer.pipes, { mode: gl.LINES });
    this.meshes.manholes = this._upload(sewer.manholes, { dynamic: true });
    this.manholeRange = b.manholeRange;
    this.manholeBase = new Float32Array(sewer.manholes.col);

    const water = b.water();
    this.meshes.water = this._upload(water, { dynamic: true });
    this.waterPos = new Float32Array(water.pos);
    this.waterCol = new Float32Array(water.col);
    this.waterPar = new Float32Array(water.par);

    const [ny, nx] = scene.shape;
    this.extentM = [nx * scene.cell_size, ny * scene.cell_size];
    this.cam.target = [0, 0, b.wz((b.zmin + b.zmax) / 2)];
    this.homeZoom = this._fitZoom();
    this.cam.zoom = this.homeZoom;
    this._buildLabels();
    this.requestRender();
  }

  _upload(builder, opts) {
    const gl = this.gl;
    if (!builder || !builder.idx.length) return null;
    const m = builder.upload(gl);
    m.mode = opts.mode || gl.TRIANGLES;
    if (opts.dynamic) {
      for (const [key, src] of [["pos", builder.pos], ["col", builder.col],
                                ["par", builder.par]]) {
        gl.bindBuffer(gl.ARRAY_BUFFER, m[key]);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(src), gl.DYNAMIC_DRAW);
      }
    }
    return m;
  }

  _free(m) {
    if (!m) return;
    for (const k of ["pos", "col", "nrm", "par", "uv", "idx"])
      if (m[k]) this.gl.deleteBuffer(m[k]);
  }

  _rebuildBuildings() {
    this._free(this.meshes.buildings);
    const mb = this.builder.buildings(this.colorMode, this.lossArr,
                                      this.hoverId, this.selectedId);
    this.meshes.buildings = this._upload(mb, {});
    this.buildingBounds = this.builder.buildingBounds;
  }

  recolorBuildings(mode, lossArr) {
    this.colorMode = mode;
    this.lossArr = lossArr || null;
    this._rebuildBuildings();
    this.requestRender();
  }

  setHover(id) {
    if (id === this.hoverId) return;
    this.hoverId = id;
    this._rebuildBuildings();
    this.requestRender();
  }

  setSelected(id) {
    if (id === this.selectedId) return;
    this.selectedId = id;
    this._rebuildBuildings();
    this.requestRender();
  }

  /* ------------------------------------------------------------ camera -- */
  setMode(mode) {
    const next = mode === "2d" ? 0 : 1;
    if (next === this._tiltTarget) return;
    // Straight down needs a different zoom than isometric to frame the same
    // ground. Scale by the ratio of the two fits so the user keeps whatever
    // zoom level they had chosen relative to the city.
    const before = this._fitZoom(this._tiltTarget);
    const after = this._fitZoom(next);
    if (before > 0) this.cam.zoom *= after / before;
    this._tiltTarget = next;
    this.homeZoom = after;
    this._animate();
  }

  get mode() { return this._tiltTarget === 0 ? "2d" : "iso"; }

  /* The zoom that frames the whole twin in the current mode.
   *
   * Rotating a W×H rectangle 45° for the isometric view projects it to a
   * diamond (W+H)/√2 wide and that times sin(pitch) tall; straight down it is
   * simply W×H. The two need noticeably different zooms, so this is recomputed
   * whenever the mode changes rather than fixed once. */
  _fitZoom(tilt) {
    if (!this.extentM) return 500;
    const t = tilt === undefined ? this._tiltTarget : tilt;
    const [W, H] = this.extentM;
    const c = this.canvas;
    const aspect = Math.max((c.clientWidth || 1600) / (c.clientHeight || 900), 0.5);
    const pitch = TOP_PITCH + (ISO_PITCH - TOP_PITCH) * t;
    const iso = t > 0.5;
    const projW = iso ? (W + H) / Math.SQRT2 : W;
    const projH = (iso ? (W + H) / Math.SQRT2 : H) * Math.sin(pitch);
    return Math.max(projH / 2, projW / (2 * aspect)) * 1.14;
  }

  resetView() {
    const b = this.builder;
    this.cam.target = [0, 0, b ? b.wz((b.zmin + b.zmax) / 2) : 0];
    this.homeZoom = this._fitZoom();
    this.cam.zoom = this.homeZoom;
    this.requestRender();
  }

  _animate() {
    if (this._anim) return;
    const tick = () => {
      const d = this._tiltTarget - this.cam.tilt;
      if (Math.abs(d) < 0.002) {
        this.cam.tilt = this._tiltTarget;
        this._anim = null;
        this.render();
        return;
      }
      this.cam.tilt += d * 0.18;
      this.render();
      this._anim = requestAnimationFrame(tick);
    };
    this._anim = requestAnimationFrame(tick);
  }

  _pitch() { return TOP_PITCH + (ISO_PITCH - TOP_PITCH) * this.cam.tilt; }
  _yaw() { return ISO_YAW * this.cam.tilt; }

  _basis() {
    const yaw = this._yaw(), pitch = this._pitch();
    const dir = [Math.cos(pitch) * Math.sin(yaw), -Math.cos(pitch) * Math.cos(yaw),
                 Math.sin(pitch)];
    const eye = V3.add(this.cam.target, V3.scale(dir, CAM_DIST));
    const fwd = V3.scale(dir, -1);
    const right = V3.norm(V3.cross(fwd, [0, 0, 1]));
    const up = V3.cross(right, fwd);
    return { eye, fwd, right, up, pitch };
  }

  _matrices() {
    const c = this.canvas;
    const aspect = (c.clientWidth || 1) / (c.clientHeight || 1);
    const { eye, right, up } = this._basis();
    const z = this.cam.zoom;
    const proj = M4.ortho(-z * aspect, z * aspect, -z, z, 1, CAM_DIST * 2);
    const view = M4.lookAt(eye, this.cam.target, [0, 0, 1]);
    return { mvp: M4.mul(proj, view), eye, right, up, aspect };
  }

  /* screen pixel → point on the horizontal plane through the camera target */
  _groundAt(px, py) {
    const rect = this.canvas.getBoundingClientRect();
    const ndcX = ((px - rect.left) / rect.width) * 2 - 1;
    const ndcY = 1 - ((py - rect.top) / rect.height) * 2;
    const { eye, fwd, right, up } = this._basis();
    const z = this.cam.zoom;
    const aspect = rect.width / rect.height;
    const o = V3.add(eye, V3.add(V3.scale(right, ndcX * z * aspect),
                                 V3.scale(up, ndcY * z)));
    const t = (this.cam.target[2] - o[2]) / (fwd[2] || -1e-6);
    return [o[0] + fwd[0] * t, o[1] + fwd[1] * t];
  }

  _bindInput() {
    const c = this.canvas;
    const drag = { on: false, x: 0, y: 0, moved: false };

    const start = (x, y) => {
      drag.on = true; drag.x = x; drag.y = y; drag.moved = false;
      c.classList.add("dragging");
    };
    const move = (x, y) => {
      if (!drag.on) return false;
      const dx = x - drag.x, dy = y - drag.y;
      drag.x = x; drag.y = y;
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
      this._panPixels(dx, dy);
      return true;
    };
    const end = () => { drag.on = false; c.classList.remove("dragging"); };

    c.addEventListener("mousedown", e => { e.preventDefault(); c.focus(); start(e.clientX, e.clientY); });
    addEventListener("mousemove", e => {
      if (move(e.clientX, e.clientY)) return;
      if (this.onHover && e.target === c) this.onHover(this._pick(e.clientX, e.clientY),
                                                       e.clientX, e.clientY);
    });
    addEventListener("mouseup", e => {
      if (drag.on && !drag.moved && this.onPick) this.onPick(this._pick(e.clientX, e.clientY));
      end();
    });
    c.addEventListener("mouseleave", () => { if (this.onHover) this.onHover(null); });
    c.addEventListener("contextmenu", e => e.preventDefault());

    c.addEventListener("wheel", e => {
      e.preventDefault();
      this._zoomAt(Math.exp(e.deltaY * 0.0014), e.clientX, e.clientY);
    }, { passive: false });

    /* Touch: one finger pans, two pinch-zoom. The old viewer was mouse-only, so
     * on a phone the camera could not be moved at all. */
    let pinch = 0;
    c.addEventListener("touchstart", e => {
      if (e.touches.length === 1) start(e.touches[0].clientX, e.touches[0].clientY);
      else if (e.touches.length === 2) { end(); pinch = this._touchDist(e); }
    }, { passive: true });
    c.addEventListener("touchmove", e => {
      if (e.touches.length === 1) {
        move(e.touches[0].clientX, e.touches[0].clientY);
      } else if (e.touches.length === 2 && pinch) {
        e.preventDefault();
        const d = this._touchDist(e);
        this._zoomAt(pinch / (d || 1),
                     (e.touches[0].clientX + e.touches[1].clientX) / 2,
                     (e.touches[0].clientY + e.touches[1].clientY) / 2);
        pinch = d;
      }
    }, { passive: false });
    c.addEventListener("touchend", () => { end(); pinch = 0; });

    c.tabIndex = 0;
    c.addEventListener("keydown", e => {
      const step = this.cam.zoom * 0.12;
      const k = e.key.toLowerCase();
      if (k === "arrowleft" || k === "a") this._panWorld(-step, 0);
      else if (k === "arrowright" || k === "d") this._panWorld(step, 0);
      else if (k === "arrowup" || k === "w") this._panWorld(0, step);
      else if (k === "arrowdown" || k === "s") this._panWorld(0, -step);
      else if (k === "+" || k === "=") this._zoomBy(1 / 1.2);
      else if (k === "-" || k === "_") this._zoomBy(1.2);
      else if (k === "r") this.resetView();
      else if (k === "2" || k === "3") {
        this.setMode(k === "2" ? "2d" : "iso");
        if (this.onModeChange) this.onModeChange(this.mode);
      } else return;
      e.preventDefault();
    });
  }

  _touchDist(e) {
    return Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                      e.touches[0].clientY - e.touches[1].clientY);
  }

  _panPixels(dx, dy) {
    const rect = this.canvas.getBoundingClientRect();
    const perPx = (2 * this.cam.zoom) / Math.max(rect.height, 1);
    this._panWorld(-dx * perPx, dy * perPx);
  }

  _panWorld(a, b) {
    const { right, pitch } = this._basis();
    // the ground is foreshortened by sin(pitch), so a unit of vertical screen
    // travel covers more ground the flatter the camera lies
    const fwdGround = V3.norm(V3.cross([0, 0, 1], right));
    const bb = b / Math.max(Math.sin(pitch), 0.2);
    this.cam.target[0] += right[0] * a + fwdGround[0] * bb;
    this.cam.target[1] += right[1] * a + fwdGround[1] * bb;
    this.requestRender();
  }

  _zoomBy(f) {
    this.cam.zoom = Math.min(Math.max(this.cam.zoom * f, 25), (this.homeZoom || 500) * 3);
    this.requestRender();
  }

  /* zoom anchored at the cursor: the world point under the pointer stays put */
  _zoomAt(f, px, py) {
    const before = this._groundAt(px, py);
    this._zoomBy(f);
    const after = this._groundAt(px, py);
    this.cam.target[0] += before[0] - after[0];
    this.cam.target[1] += before[1] - after[1];
    this.requestRender();
  }

  /* ------------------------------------------------------------ picking -- */
  /* Under a parallel projection every ray shares one direction, so this is a
   * screen-plane offset plus a slab test — no frustum maths, and no hidden
   * dependence on a field of view that has to be kept in sync with render(). */
  _pick(px, py) {
    if (!this.buildingBounds) return null;
    const rect = this.canvas.getBoundingClientRect();
    const ndcX = ((px - rect.left) / rect.width) * 2 - 1;
    const ndcY = 1 - ((py - rect.top) / rect.height) * 2;
    if (Math.abs(ndcX) > 1 || Math.abs(ndcY) > 1) return null;
    const { eye, fwd, right, up } = this._basis();
    const z = this.cam.zoom;
    const aspect = rect.width / rect.height;
    const o = V3.add(eye, V3.add(V3.scale(right, ndcX * z * aspect),
                                 V3.scale(up, ndcY * z)));
    let best = null, bestT = Infinity;
    for (const bb of this.buildingBounds) {
      const t = rayBox(o, fwd, bb);
      if (t !== null && t < bestT) { bestT = t; best = bb; }
    }
    return best ? best.b : null;
  }

  /* ------------------------------------------------------------- labels -- */
  /* Landmark names live in a DOM overlay rather than in the GL scene: text as
   * geometry in WebGL 1 means shipping a glyph atlas, and these have to stay
   * crisp at every zoom level. */
  setLabelHost(el) { this.labelHost = el; this._buildLabels(); }

  _buildLabels() {
    if (!this.labelHost) return;
    this.labelHost.innerHTML = "";
    this.labelEls = [];
    const feats = (this.scene && this.scene.features) || {};
    for (const l of feats.labels || []) {
      const el = document.createElement("div");
      el.className = `map-label kind-${l.kind || "landmark"}`;
      el.textContent = l.name;
      this.labelHost.appendChild(el);
      this.labelEls.push({
        el, kind: l.kind,
        p: [this.builder.wx(l.x), this.builder.wy(l.y),
            this.builder.wz(this.builder.elev(l.x, l.y)) + 8],
      });
    }
  }

  /* Districts fade out as you zoom in and landmarks as you zoom out, so the
   * map never becomes a wall of text. */
  _placeLabels(mvp) {
    if (!this.labelEls) return;
    const rect = this.canvas.getBoundingClientRect();
    const z = this.cam.zoom;
    for (const L of this.labelEls) {
      const [cx, cy] = M4.project(mvp, L.p);
      const on = cx > -1.02 && cx < 1.02 && cy > -1.02 && cy < 1.02;
      let vis = on ? 1 : 0;
      if (L.kind === "district") vis *= z > 280 ? 1 : Math.max(0, (z - 150) / 130);
      else if (L.kind === "landmark") vis *= z < 900 ? 1 : 0;
      L.el.style.transform =
        `translate(${((cx * 0.5 + 0.5) * rect.width).toFixed(1)}px, ` +
        `${((1 - (cy * 0.5 + 0.5)) * rect.height).toFixed(1)}px)`;
      L.el.style.opacity = vis.toFixed(2);
    }
  }

  /* -------------------------------------------------------- water frame -- */
  setWaterFrame(depth, surcharging, rainMmh) {
    const m = this.meshes.water;
    if (!m) return;
    const gl = this.gl;
    const b = this.builder;
    const { nx, ny } = b;
    const pos = this.waterPos, col = this.waterCol, par = this.waterPar;
    const shallow = [0.42, 0.63, 0.86], deep = [0.05, 0.19, 0.40];

    for (let k = 0; k < nx * ny; k++) {
      const d = depth[k];
      const zt = b.wz(b.dtm[k]);
      if (d > 0.015) {
        const f = Math.min(d / 1.2, 1);
        col[k * 3] = shallow[0] * (1 - f) + deep[0] * f;
        col[k * 3 + 1] = shallow[1] * (1 - f) + deep[1] * f;
        col[k * 3 + 2] = shallow[2] * (1 - f) + deep[2] * f;
        par[k * 4] = 0.34 + 0.56 * f;
        // Depth is exaggerated on top of the global vertical scale: the hundred
        // millimetres of water in a street that floods a basement would be
        // invisible at true scale across two kilometres of city.
        pos[k * 3 + 2] = zt + Math.min(d * 2.5, 6) * b.zScale + 0.12;
      } else {
        par[k * 4] = 0;
        pos[k * 3 + 2] = zt - 0.5;
      }
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, m.pos);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pos);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.col);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, col);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.par);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, par);

    this._setSurcharging(surcharging);
    this.storm = Math.min((rainMmh || 0) / 70, 1);
    this.requestRender();
  }

  _setSurcharging(list) {
    const m = this.meshes.manholes;
    if (!m || !this.manholeRange) return;
    const col = new Float32Array(this.manholeBase);
    for (const ni of list || []) {
      const r = this.manholeRange[ni];
      if (!r) continue;
      for (let v = r[0]; v < r[0] + r[1]; v++) {
        col[v * 3] = 0.95; col[v * 3 + 1] = 0.32; col[v * 3 + 2] = 0.18;
      }
    }
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, m.col);
    this.gl.bufferSubData(this.gl.ARRAY_BUFFER, 0, col);
  }

  clearWater() {
    const m = this.meshes.water;
    if (!m) return;
    for (let k = 0; k < this.waterPar.length / 4; k++) this.waterPar[k * 4] = 0;
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, m.par);
    this.gl.bufferSubData(this.gl.ARRAY_BUFFER, 0, this.waterPar);
    this.storm = 0;
    this._setSurcharging([]);
    this.requestRender();
  }

  /* ------------------------------------------------------------- render -- */
  requestRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this.render(); });
  }

  render() {
    const gl = this.gl, c = this.canvas;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round((c.clientWidth || 1) * dpr);
    const h = Math.round((c.clientHeight || 1) * dpr);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    const storm = this.storm;
    const skyTop = Color.mix(Color.hex("#5b86b8"), Color.hex("#20262d"), storm);
    const skyLow = Color.mix(Color.hex("#c6d6e5"), Color.hex("#3a4046"), storm);

    // --- sky first, depth-write off so it never occludes the city
    gl.useProgram(this.skyProg.prog);
    gl.depthMask(false);
    gl.disable(gl.DEPTH_TEST);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.skyBuf);
    gl.enableVertexAttribArray(this.skyProg.aPos);
    gl.vertexAttribPointer(this.skyProg.aPos, 2, gl.FLOAT, false, 0, 0);
    gl.uniform3fv(this.skyProg.uTop, skyTop);
    gl.uniform3fv(this.skyProg.uBottom, skyLow);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.DEPTH_TEST);
    gl.depthMask(true);

    if (!this.meshes.terrain) return;

    const { mvp } = this._matrices();
    const P = this.prog;
    gl.useProgram(P.prog);
    gl.disableVertexAttribArray(this.skyProg.aPos);
    gl.uniformMatrix4fv(P.uMVP, false, mvp);
    gl.uniform3fv(P.uFocus, new Float32Array(this.cam.target));
    gl.uniform3fv(P.uSun, V3.norm([0.44, 0.30, 0.84]));
    gl.uniform3fv(P.uSunColor,
                  Color.mix(Color.hex("#ffe9c4"), Color.hex("#8f97a3"), storm));
    gl.uniform3fv(P.uSkyColor, skyLow);
    gl.uniform1f(P.uStorm, storm);
    gl.uniform1f(P.uTime, (performance.now() - this._t0) / 1000);
    // fog only reaches the far apron, so the city itself never greys out
    gl.uniform1f(P.uFogNear, this.cam.zoom * 1.7);
    gl.uniform1f(P.uFogFar, this.cam.zoom * 4.2);

    const order = ["surround", "terrain", "landscape", "streets"];
    if (this.showShadows) order.push("shadows");
    order.push("buildings");
    if (this.showTrees) order.push("trees");
    order.push("manholes");
    if (this.showPipes) order.push("pipes");
    order.push("water");

    const noDepthWrite = { water: 1, shadows: 1, landscape: 1 };
    for (const name of order) {
      const m = this.meshes[name];
      if (!m || !m.count) continue;
      if (noDepthWrite[name]) gl.depthMask(false);
      if (name === "pipes") gl.disable(gl.DEPTH_TEST);     // x-ray through ground
      this._draw(m);
      if (noDepthWrite[name]) gl.depthMask(true);
      if (name === "pipes") gl.enable(gl.DEPTH_TEST);
    }

    this._placeLabels(mvp);
    // water shimmer and lit windows keep moving while the replay is paused
    if (storm > 0.02 && !this._anim) this.requestRender();
  }

  _draw(m) {
    const gl = this.gl, P = this.prog;
    const bind = (buf, loc, size) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    };
    bind(m.pos, P.aPos, 3);
    bind(m.col, P.aColor, 3);
    bind(m.nrm, P.aNormal, 3);
    bind(m.par, P.aParam, 4);
    bind(m.uv, P.aUV, 2);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.idx);
    gl.drawElements(m.mode, m.count, m.idxType, 0);
  }
}

/* slab method; under a parallel projection the direction is constant */
function rayBox(o, d, b) {
  let t0 = -Infinity, t1 = Infinity;
  const lo = [b.minx, b.miny, b.z0], hi = [b.maxx, b.maxy, b.z1];
  for (let k = 0; k < 3; k++) {
    if (Math.abs(d[k]) < 1e-9) {
      if (o[k] < lo[k] || o[k] > hi[k]) return null;
      continue;
    }
    let a = (lo[k] - o[k]) / d[k], bb = (hi[k] - o[k]) / d[k];
    if (a > bb) { const t = a; a = bb; bb = t; }
    t0 = Math.max(t0, a);
    t1 = Math.min(t1, bb);
    if (t0 > t1) return null;
  }
  return t1 < 0 ? null : Math.max(t0, 0);
}

window.CitySimViewer = Viewer;
})();
