/* CitySim 3D viewer — dependency-free WebGL.
 *
 * Renders the hazard-agnostic twin scene (terrain + LoD1 buildings + sewer)
 * and animates a hazard overlay on top: for flood, a translucent water
 * surface whose height/opacity follow the replay depth grids, plus manhole
 * markers that flash while surcharging. The twin rendering never changes per
 * hazard — only the overlay does (plan §5.9).
 */
"use strict";

/* ---------------- tiny matrix helpers ---------------- */
const M4 = {
  mul(a, b) {
    const o = new Float32Array(16);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  },
  perspective(fov, aspect, near, far) {
    const f = 1 / Math.tan(fov / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0,
      0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  },
  lookAt(eye, at, up) {
    const z = norm3(sub3(eye, at)), x = norm3(cross3(up, z)), y = cross3(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
      -dot3(x, eye), -dot3(y, eye), -dot3(z, eye), 1]);
  },
};
function sub3(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function cross3(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function dot3(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function norm3(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }

/* ---------------- shaders ---------------- */
const VS = `
attribute vec3 aPos; attribute vec3 aColor; attribute vec3 aNormal; attribute float aAlpha;
uniform mat4 uMVP; varying vec3 vColor; varying float vAlpha; varying vec3 vNormal;
void main(){ gl_Position = uMVP * vec4(aPos,1.0); vColor = aColor; vAlpha = aAlpha; vNormal = aNormal; }`;
const FS = `
precision mediump float;
varying vec3 vColor; varying float vAlpha; varying vec3 vNormal;
uniform vec3 uLight; uniform float uLit;
void main(){
  float lam = uLit > 0.5 ? (0.55 + 0.45 * max(dot(normalize(vNormal), uLight), 0.0)) : 1.0;
  gl_FragColor = vec4(vColor * lam, vAlpha);
}`;

/* landcover classes: 0 water 1 paved 2 building 3 grass 4 trees 5 bare */
const LC_COLORS = [
  [0.16, 0.25, 0.34], [0.235, 0.235, 0.25], [0.30, 0.28, 0.27],
  [0.22, 0.31, 0.20], [0.16, 0.26, 0.16], [0.32, 0.28, 0.22],
];
const USE_COLORS = {
  residential: [0.62, 0.58, 0.52], commercial: [0.45, 0.55, 0.66],
  industrial: [0.55, 0.48, 0.58], institutional: [0.66, 0.60, 0.42],
};
// sequential orange ramp for $-risk choropleth (water already owns blue)
const LOSS_RAMP = [[0.42, 0.40, 0.38], [0.55, 0.42, 0.30], [0.72, 0.45, 0.22],
  [0.85, 0.42, 0.15], [0.90, 0.30, 0.10], [0.82, 0.16, 0.16]];

class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext("webgl", { antialias: true });
    if (!gl) throw new Error("WebGL unavailable");
    this.gl = gl;
    const prog = this._makeProgram(VS, FS);
    this.prog = prog;
    this.loc = {
      aPos: gl.getAttribLocation(prog, "aPos"),
      aColor: gl.getAttribLocation(prog, "aColor"),
      aNormal: gl.getAttribLocation(prog, "aNormal"),
      aAlpha: gl.getAttribLocation(prog, "aAlpha"),
      uMVP: gl.getUniformLocation(prog, "uMVP"),
      uLight: gl.getUniformLocation(prog, "uLight"),
      uLit: gl.getUniformLocation(prog, "uLit"),
    };
    this.meshes = {};           // name -> {vbo fields, count, mode, alpha}
    this.cam = { yaw: -0.7, pitch: 0.9, dist: 700, target: [0, 0, 0] };
    this.showPipes = false;
    this.onPick = null;
    this._bindInput();
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  }

  _makeProgram(vsSrc, fsSrc) {
    const gl = this.gl;
    const mk = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, mk(gl.VERTEX_SHADER, vsSrc));
    gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fsSrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  }

  /* ------------- scene building ------------- */
  setScene(scene) {
    this.scene = scene;
    const [ny, nx] = scene.shape;
    const cs = scene.cell_size;
    this.nx = nx; this.ny = ny; this.cs = cs;
    this.zScale = 1.6;                       // gentle vertical exaggeration
    const dtm = scene.dtm;
    this.zmin = Math.min(...dtm);
    this.cx = (nx * cs) / 2; this.cy = (ny * cs) / 2;
    this.cam.target = [0, 0, (this._avg(dtm) - this.zmin) * this.zScale];
    this.cam.dist = Math.max(nx, ny) * cs * 0.85;

    this._buildTerrain();
    this._buildBuildings("class", null);
    this._buildSewer();
    this._buildWater();
    this.requestRender();
  }

  _avg(a) { let s = 0; for (const v of a) s += v; return s / a.length; }
  _wx(x) { return x - this.cx; }
  _wy(y) { return y - this.cy; }
  _wz(z) { return (z - this.zmin) * this.zScale; }
  _terrainZ(i, j) { return this.scene.dtm[i * this.nx + j]; }

  _buildTerrain() {
    const { nx, ny, cs } = this;
    const lc = this.scene.landcover;
    const nv = nx * ny;
    const pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3),
      nrm = new Float32Array(nv * 3), alp = new Float32Array(nv).fill(1);
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) {
      const k = i * nx + j;
      pos[k * 3] = this._wx(j * cs); pos[k * 3 + 1] = this._wy(i * cs);
      pos[k * 3 + 2] = this._wz(this._terrainZ(i, j));
      const c = LC_COLORS[lc[k]] || LC_COLORS[5];
      col[k * 3] = c[0]; col[k * 3 + 1] = c[1]; col[k * 3 + 2] = c[2];
      // central-difference normal
      const zl = this._terrainZ(i, Math.max(j - 1, 0)), zr = this._terrainZ(i, Math.min(j + 1, nx - 1));
      const zd = this._terrainZ(Math.max(i - 1, 0), j), zu = this._terrainZ(Math.min(i + 1, ny - 1), j);
      const n = norm3([-(zr - zl) * this.zScale / (2 * cs), -(zu - zd) * this.zScale / (2 * cs), 1]);
      nrm[k * 3] = n[0]; nrm[k * 3 + 1] = n[1]; nrm[k * 3 + 2] = n[2];
    }
    const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let q = 0;
    for (let i = 0; i < ny - 1; i++) for (let j = 0; j < nx - 1; j++) {
      const a = i * nx + j, b = a + 1, c = a + nx, d = c + 1;
      idx[q++] = a; idx[q++] = b; idx[q++] = c; idx[q++] = b; idx[q++] = d; idx[q++] = c;
    }
    this._setMesh("terrain", pos, col, nrm, alp, idx, this.gl.TRIANGLES, true);
  }

  buildingColor(b, mode, lossArr, lossMax) {
    if (mode === "loss" && lossArr) {
      const v = lossArr[b._idx] || 0;
      if (v <= 0) return LOSS_RAMP[0];
      const f = Math.min(Math.log1p(v) / Math.log1p(lossMax || 1), 1);
      const t = f * (LOSS_RAMP.length - 1), k = Math.min(Math.floor(t), LOSS_RAMP.length - 2), r = t - k;
      return [0, 1, 2].map(ch => LOSS_RAMP[k][ch] * (1 - r) + LOSS_RAMP[k + 1][ch] * r);
    }
    return USE_COLORS[b.use] || USE_COLORS.residential;
  }

  _buildBuildings(mode, lossArr) {
    const bl = this.scene.buildings;
    let lossMax = 1;
    if (lossArr) lossMax = Math.max(...lossArr, 1);
    const pos = [], col = [], nrm = [], alp = [], idx = [];
    let base = 0;
    this.buildingBounds = [];
    bl.forEach((b, bi) => {
      b._idx = bi;
      const c = this.buildingColor(b, mode, lossArr, lossMax);
      const fp = b.footprint;
      const z0 = this._wz(b.ground_elev) - 0.5;
      const z1 = this._wz(b.ground_elev + b.height);
      let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
      const n = fp.length;
      const roofStart = base;
      // roof (triangle fan) + record bounds
      for (const p of fp) {
        const x = this._wx(p[0]), y = this._wy(p[1]);
        minx = Math.min(minx, x); maxx = Math.max(maxx, x);
        miny = Math.min(miny, y); maxy = Math.max(maxy, y);
        pos.push(x, y, z1); col.push(c[0] * 1.08, c[1] * 1.08, c[2] * 1.08);
        nrm.push(0, 0, 1); alp.push(1);
      }
      for (let k = 1; k < n - 1; k++) idx.push(roofStart, roofStart + k, roofStart + k + 1);
      base += n;
      // walls
      for (let k = 0; k < n; k++) {
        const p = fp[k], q2 = fp[(k + 1) % n];
        const x1 = this._wx(p[0]), y1 = this._wy(p[1]), x2 = this._wx(q2[0]), y2 = this._wy(q2[1]);
        const wn = norm3([y2 - y1, x1 - x2, 0]);
        const shade = 0.82;
        for (const [x, y, z] of [[x1, y1, z0], [x2, y2, z0], [x2, y2, z1], [x1, y1, z1]]) {
          pos.push(x, y, z); col.push(c[0] * shade, c[1] * shade, c[2] * shade);
          nrm.push(wn[0], wn[1], wn[2]); alp.push(1);
        }
        idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
        base += 4;
      }
      this.buildingBounds.push({ id: b.id, minx, miny, maxx, maxy, z0, z1, b });
    });
    this._setMesh("buildings", new Float32Array(pos), new Float32Array(col),
      new Float32Array(nrm), new Float32Array(alp), new Uint32Array(idx), this.gl.TRIANGLES, true);
  }

  recolorBuildings(mode, lossArr) { this._buildBuildings(mode, lossArr); this.requestRender(); }

  _buildSewer() {
    const s = this.scene.sewer;
    const nodeById = {};
    s.nodes.forEach(n => nodeById[n.id] = n);
    // pipes as underground lines (x-ray)
    const pos = [], col = [], nrm = [], alp = [];
    for (const c of s.conduits) {
      const a = nodeById[c.from], b = nodeById[c.to];
      if (!a || !b) continue;
      const cc = c.system === "combined" ? [0.85, 0.35, 0.15] : [0.25, 0.53, 0.90];
      for (const n of [a, b]) {
        pos.push(this._wx(n.x), this._wy(n.y), this._wz(n.rim_elev - 2.2));
        col.push(cc[0], cc[1], cc[2]); nrm.push(0, 0, 1); alp.push(0.9);
      }
    }
    this._setMesh("pipes", new Float32Array(pos), new Float32Array(col),
      new Float32Array(nrm), new Float32Array(alp), null, this.gl.LINES, false);

    // manhole markers: small boxes at rims; recolored per-frame while surcharging
    const mp = [], mc = [], mn = [], ma = [], mi = [];
    this.manholeVerts = [];  // vertex ranges per node index
    let vb = 0;
    s.nodes.forEach((n) => {
      const x = this._wx(n.x), y = this._wy(n.y), z = this._wz(n.rim_elev), r = 2.2;
      const startV = vb;
      const zt = z + 1.2;
      const cc = n.system === "combined" ? [0.55, 0.35, 0.25] : [0.35, 0.40, 0.48];
      const corners = [[-r, -r], [r, -r], [r, r], [-r, r]];
      for (const [dx, dy] of corners) { mp.push(x + dx, y + dy, zt); mc.push(...cc); mn.push(0, 0, 1); ma.push(1); }
      mi.push(vb, vb + 1, vb + 2, vb, vb + 2, vb + 3);
      vb += 4;
      this.manholeVerts.push({ start: startV, count: 4, base: cc });
    });
    this._setMesh("manholes", new Float32Array(mp), new Float32Array(mc),
      new Float32Array(mn), new Float32Array(ma), new Uint32Array(mi), this.gl.TRIANGLES, true);
  }

  _buildWater() {
    const { nx, ny, cs } = this;
    const nv = nx * ny;
    const pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3),
      nrm = new Float32Array(nv * 3), alp = new Float32Array(nv);
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) {
      const k = i * nx + j;
      pos[k * 3] = this._wx(j * cs); pos[k * 3 + 1] = this._wy(i * cs);
      pos[k * 3 + 2] = this._wz(this._terrainZ(i, j)) - 0.5;
      nrm[k * 3 + 2] = 1;
    }
    const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
    let q = 0;
    for (let i = 0; i < ny - 1; i++) for (let j = 0; j < nx - 1; j++) {
      const a = i * nx + j, b = a + 1, c = a + nx, d = c + 1;
      idx[q++] = a; idx[q++] = b; idx[q++] = c; idx[q++] = b; idx[q++] = d; idx[q++] = c;
    }
    this._setMesh("water", pos, col, nrm, alp, idx, this.gl.TRIANGLES, false);
    this.waterPos = pos; this.waterCol = col; this.waterAlp = alp;
  }

  /* depth: Float32Array(ny*nx) metres; surcharging: array of node indices */
  setWaterFrame(depth, surcharging) {
    const { nx, ny } = this;
    const pos = this.waterPos, col = this.waterCol, alp = this.waterAlp;
    // sequential blue ramp shallow→deep
    for (let k = 0; k < nx * ny; k++) {
      const d = depth[k];
      const i = (k / nx) | 0, j = k % nx;
      if (d > 0.015) {
        const f = Math.min(d / 1.2, 1);
        col[k * 3] = 0.53 * (1 - f) + 0.05 * f;
        col[k * 3 + 1] = 0.71 * (1 - f) + 0.21 * f;
        col[k * 3 + 2] = 0.94 * (1 - f) + 0.42 * f;
        alp[k] = 0.35 + 0.55 * f;
        pos[k * 3 + 2] = this._wz(this._terrainZ(i, j) + d * 2.5) + 0.15;
      } else {
        alp[k] = 0; pos[k * 3 + 2] = this._wz(this._terrainZ(i, j)) - 0.5;
      }
    }
    const gl = this.gl, m = this.meshes.water;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.pos); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.col); gl.bufferData(gl.ARRAY_BUFFER, col, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.alp); gl.bufferData(gl.ARRAY_BUFFER, alp, gl.DYNAMIC_DRAW);

    // manhole flash
    const mm = this.meshes.manholes;
    if (mm && this.manholeVerts) {
      const flash = new Set(surcharging || []);
      const colArr = mm.colArr;
      const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 120);
      this.manholeVerts.forEach((v, ni) => {
        const c = flash.has(ni) ? [0.95, 0.25 + 0.4 * pulse, 0.2] : v.base;
        for (let k = 0; k < v.count; k++) {
          colArr[(v.start + k) * 3] = c[0]; colArr[(v.start + k) * 3 + 1] = c[1]; colArr[(v.start + k) * 3 + 2] = c[2];
        }
      });
      gl.bindBuffer(gl.ARRAY_BUFFER, mm.col); gl.bufferData(gl.ARRAY_BUFFER, colArr, gl.DYNAMIC_DRAW);
    }
    this.requestRender();
  }

  clearWater() {
    this.waterAlp.fill(0);
    const gl = this.gl, m = this.meshes.water;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.alp); gl.bufferData(gl.ARRAY_BUFFER, this.waterAlp, gl.DYNAMIC_DRAW);
    this.requestRender();
  }

  _setMesh(name, pos, col, nrm, alp, idx, mode, lit) {
    const gl = this.gl;
    const old = this.meshes[name];
    if (old) for (const k of ["pos", "col", "nrm", "alp", "idx"]) if (old[k]) gl.deleteBuffer(old[k]);
    const mk = (data, target = gl.ARRAY_BUFFER) => {
      const b = gl.createBuffer(); gl.bindBuffer(target, b);
      gl.bufferData(target, data, gl.STATIC_DRAW); return b;
    };
    this.meshes[name] = {
      pos: mk(pos), col: mk(col), nrm: mk(nrm), alp: mk(alp),
      idx: idx ? mk(idx, gl.ELEMENT_ARRAY_BUFFER) : null,
      count: idx ? idx.length : pos.length / 3, mode, lit, colArr: col,
    };
  }

  /* ------------- input ------------- */
  _bindInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener("mousedown", e => {
      drag = { x: e.clientX, y: e.clientY, btn: e.button, moved: false };
      c.classList.add("dragging");
    });
    window.addEventListener("mousemove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.btn === 2 || e.shiftKey) {
        const s = this.cam.dist / 700;
        const cy = Math.cos(this.cam.yaw), sy = Math.sin(this.cam.yaw);
        this.cam.target[0] -= (dx * cy - dy * sy) * s * 0.8;
        this.cam.target[1] += (dx * sy + dy * cy) * s * 0.8;
      } else {
        this.cam.yaw -= dx * 0.006;
        this.cam.pitch = Math.min(Math.max(this.cam.pitch - dy * 0.005, 0.12), 1.5);
      }
      drag.x = e.clientX; drag.y = e.clientY;
      this.requestRender();
    });
    window.addEventListener("mouseup", e => {
      if (drag && !drag.moved && drag.btn === 0 && this.onPick) {
        const hit = this._pick(e.clientX, e.clientY);
        this.onPick(hit);
      }
      drag = null; c.classList.remove("dragging");
    });
    c.addEventListener("wheel", e => {
      e.preventDefault();
      this.cam.dist *= Math.exp(e.deltaY * 0.0012);
      this.cam.dist = Math.min(Math.max(this.cam.dist, 60), 6000);
      this.requestRender();
    }, { passive: false });
    c.addEventListener("contextmenu", e => e.preventDefault());
  }

  _eye() {
    const { yaw, pitch, dist, target } = this.cam;
    return [
      target[0] + dist * Math.cos(pitch) * Math.sin(yaw),
      target[1] - dist * Math.cos(pitch) * Math.cos(yaw),
      target[2] + dist * Math.sin(pitch),
    ];
  }

  _pick(cx, cy) {
    // unproject a ray and test building AABBs
    const rect = this.canvas.getBoundingClientRect();
    const ndcX = ((cx - rect.left) / rect.width) * 2 - 1;
    const ndcY = 1 - ((cy - rect.top) / rect.height) * 2;
    const eye = this._eye();
    const fwd = norm3(sub3(this.cam.target, eye));
    const right = norm3(cross3(fwd, [0, 0, 1]));
    const up = cross3(right, fwd);
    const fov = 0.9, aspect = rect.width / rect.height;
    const ty = Math.tan(fov / 2);
    const dir = norm3([
      fwd[0] + right[0] * ndcX * ty * aspect + up[0] * ndcY * ty,
      fwd[1] + right[1] * ndcX * ty * aspect + up[1] * ndcY * ty,
      fwd[2] + right[2] * ndcX * ty * aspect + up[2] * ndcY * ty,
    ]);
    let best = null, bestT = 1e12;
    for (const bb of this.buildingBounds || []) {
      const t = rayBox(eye, dir, [bb.minx, bb.miny, bb.z0], [bb.maxx, bb.maxy, bb.z1]);
      if (t !== null && t < bestT) { bestT = t; best = bb.b; }
    }
    if (best) return best;
    // forgiving fallback: nearest building centre within ~1.5 cells of the ray
    let bestD = 12;
    for (const bb of this.buildingBounds || []) {
      const c = [(bb.minx + bb.maxx) / 2, (bb.miny + bb.maxy) / 2, (bb.z0 + bb.z1) / 2];
      const v = sub3(c, eye);
      const t = dot3(v, dir);
      if (t <= 0) continue;
      const d = Math.hypot(v[0] - dir[0] * t, v[1] - dir[1] * t, v[2] - dir[2] * t);
      if (d < bestD) { bestD = d; best = bb.b; }
    }
    return best;
  }

  /* ------------- render ------------- */
  requestRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this.render(); });
  }

  render() {
    const gl = this.gl, c = this.canvas;
    const w = c.clientWidth * devicePixelRatio, h = c.clientHeight * devicePixelRatio;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clearColor(0.051, 0.051, 0.051, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.prog);

    const eye = this._eye();
    const proj = M4.perspective(0.9, c.clientWidth / c.clientHeight, 2, 20000);
    const view = M4.lookAt(eye, this.cam.target, [0, 0, 1]);
    gl.uniformMatrix4fv(this.loc.uMVP, false, M4.mul(proj, view));
    gl.uniform3fv(this.loc.uLight, norm3([0.4, 0.3, 0.85]));

    const order = ["terrain", "buildings", "manholes"];
    if (this.showPipes) order.push("pipes");
    order.push("water");
    for (const name of order) {
      const m = this.meshes[name];
      if (!m || !m.count) continue;
      gl.uniform1f(this.loc.uLit, m.lit ? 1 : 0);
      if (name === "water") gl.depthMask(false);
      if (name === "pipes") gl.disable(gl.DEPTH_TEST);   // x-ray through terrain
      this._drawMesh(m);
      if (name === "water") gl.depthMask(true);
      if (name === "pipes") gl.enable(gl.DEPTH_TEST);
    }
  }

  _drawMesh(m) {
    const gl = this.gl, L = this.loc;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.pos);
    gl.enableVertexAttribArray(L.aPos); gl.vertexAttribPointer(L.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.col);
    gl.enableVertexAttribArray(L.aColor); gl.vertexAttribPointer(L.aColor, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.nrm);
    gl.enableVertexAttribArray(L.aNormal); gl.vertexAttribPointer(L.aNormal, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.alp);
    gl.enableVertexAttribArray(L.aAlpha); gl.vertexAttribPointer(L.aAlpha, 1, gl.FLOAT, false, 0, 0);
    if (m.idx) {
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.idx);
      const ext = gl.getExtension("OES_element_index_uint");
      gl.drawElements(m.mode, m.count, ext ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
    } else {
      gl.drawArrays(m.mode, 0, m.count);
    }
  }
}

function rayBox(o, d, lo, hi) {
  let tmin = 0, tmax = 1e12;
  for (let a = 0; a < 3; a++) {
    if (Math.abs(d[a]) < 1e-9) {
      if (o[a] < lo[a] || o[a] > hi[a]) return null;
    } else {
      let t1 = (lo[a] - o[a]) / d[a], t2 = (hi[a] - o[a]) / d[a];
      if (t1 > t2) [t1, t2] = [t2, t1];
      tmin = Math.max(tmin, t1); tmax = Math.min(tmax, t2);
      if (tmin > tmax) return null;
    }
  }
  return tmin;
}

window.CitySimViewer = Viewer;
