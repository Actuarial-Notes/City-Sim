/* CitySim WebGL core — matrices, shader plumbing, mesh assembly.
 *
 * Split out of viewer.js in V2. The viewer grew a photoreal renderer and an
 * orthographic camera with two modes, and keeping the linear algebra and the
 * buffer bookkeeping in the same file as the scene logic stopped being
 * readable.
 *
 * No dependencies, no build step — this is a plain script, same as the rest of
 * the front end.
 */
"use strict";

/* ============================= vector helpers ============================= */
const V3 = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  scale: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
  cross: (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  norm: a => {
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
  },
};

/* ============================== 4×4 matrices ============================== */
const M4 = {
  mul(a, b) {
    const o = new Float32Array(16);
    for (let i = 0; i < 4; i++)
      for (let j = 0; j < 4; j++)
        o[j * 4 + i] = a[i] * b[j * 4] + a[4 + i] * b[j * 4 + 1] +
                       a[8 + i] * b[j * 4 + 2] + a[12 + i] * b[j * 4 + 3];
    return o;
  },

  /* The V2 camera is orthographic in both modes. An isometric view *is* a
   * parallel projection — under perspective the "isometric" look is a lie that
   * gets worse toward the edges of a city-sized scene, and parallel projection
   * also makes picking a constant-direction ray instead of a per-pixel frustum
   * calculation. */
  ortho(l, r, b, t, n, f) {
    return new Float32Array([
      2 / (r - l), 0, 0, 0,
      0, 2 / (t - b), 0, 0,
      0, 0, -2 / (f - n), 0,
      -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1,
    ]);
  },

  lookAt(eye, at, up) {
    const z = V3.norm(V3.sub(eye, at));
    const x = V3.norm(V3.cross(up, z));
    const y = V3.cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -V3.dot(x, eye), -V3.dot(y, eye), -V3.dot(z, eye), 1,
    ]);
  },

  /* world → clip, for placing DOM labels over the canvas */
  project(m, p) {
    const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
    const y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
    const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    return [x / w, y / w];
  },
};

/* ============================= shader plumbing ============================ */
function compileShader(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS))
    throw new Error(`shader: ${gl.getShaderInfoLog(sh)}`);
  return sh;
}

function buildProgram(gl, vsSrc, fsSrc, attribs, uniforms) {
  const prog = gl.createProgram();
  gl.attachShader(prog, compileShader(gl, gl.VERTEX_SHADER, vsSrc));
  gl.attachShader(prog, compileShader(gl, gl.FRAGMENT_SHADER, fsSrc));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
    throw new Error(`link: ${gl.getProgramInfoLog(prog)}`);
  const loc = { prog };
  for (const a of attribs) loc[a] = gl.getAttribLocation(prog, a);
  for (const u of uniforms) loc[u] = gl.getUniformLocation(prog, u);
  return loc;
}

/* ============================ mesh construction ==========================
 * Meshes are built into plain JS arrays and uploaded once. The vertex format is
 * shared by every lit mesh in the scene:
 *
 *   aPos    vec3   world position
 *   aColor  vec3   base albedo
 *   aNormal vec3   surface normal
 *   aParam  vec4   (alpha, ambient occlusion, surface kind, phase)
 *   aUV     vec2   surface-local metres — (along, up) on walls, (x, y) on ground
 *
 * `kind` drives the fragment shader: 0 flat/unlit, 1 lit, 2 water,
 * 3 wall (draws window rows from aUV.y), 4 roof, 5 road (centre-line marking).
 */
class MeshBuilder {
  constructor() {
    this.pos = [];
    this.col = [];
    this.nrm = [];
    this.par = [];
    this.uv = [];
    this.idx = [];
  }

  get count() { return this.idx.length; }
  get vertexCount() { return this.pos.length / 3; }

  vertex(p, c, n, alpha, ao, kind, phase, u, v) {
    this.pos.push(p[0], p[1], p[2]);
    this.col.push(c[0], c[1], c[2]);
    this.nrm.push(n[0], n[1], n[2]);
    this.par.push(alpha, ao, kind, phase || 0);
    this.uv.push(u || 0, v || 0);
    return this.vertexCount - 1;
  }

  tri(a, b, c) { this.idx.push(a, b, c); }

  quad(a, b, c, d) { this.idx.push(a, b, c, a, c, d); }

  /* A convex-ish polygon as a fan from its centroid. Every polygon in the scene
   * — footprints, park and water outlines — is star-shaped about its centroid,
   * so a fan is correct and avoids shipping an ear-clipping triangulator. */
  polygon(pts, zAt, c, n, alpha, ao, kind, phase) {
    if (pts.length < 3) return;
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p[0]; cy += p[1]; }
    cx /= pts.length; cy /= pts.length;
    const centre = this.vertex([cx, cy, zAt(cx, cy)], c, n, alpha, ao, kind, phase, 0, 0);
    const ring = pts.map(p =>
      this.vertex([p[0], p[1], zAt(p[0], p[1])], c, n, alpha, ao, kind, phase,
                  p[0] - cx, p[1] - cy));
    for (let i = 0; i < ring.length; i++)
      this.tri(centre, ring[i], ring[(i + 1) % ring.length]);
  }

  /* A polyline widened into a ground-hugging ribbon — streets, rail, creeks.
   * Segments are emitted independently with a small overlap at the joints,
   * which avoids mitre maths and is invisible at street width. */
  ribbon(pts, halfWidth, zAt, c, alpha, kind, phase) {
    const up = [0, 0, 1];
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      const dx = x2 - x1, dy = y2 - y1;
      const len = Math.hypot(dx, dy);
      if (len < 1e-3) continue;
      const ux = dx / len, uy = dy / len;
      const nx = -uy * halfWidth, ny = ux * halfWidth;
      // extend each end by half a width so corners fill in
      const ex = ux * halfWidth, ey = uy * halfWidth;
      const corners = [
        [x1 - ex + nx, y1 - ey + ny], [x2 + ex + nx, y2 + ey + ny],
        [x2 + ex - nx, y2 + ey - ny], [x1 - ex - nx, y1 - ey - ny],
      ];
      const v = corners.map((p, k) =>
        this.vertex([p[0], p[1], zAt(p[0], p[1])], c, up, alpha, 1, kind, phase,
                    k === 1 || k === 2 ? len : 0, k < 2 ? halfWidth : -halfWidth));
      this.quad(v[0], v[1], v[2], v[3]);
    }
  }

  upload(gl) {
    const buf = data => {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      return b;
    };
    const pos = new Float32Array(this.pos);
    const mesh = {
      pos: buf(pos), col: buf(new Float32Array(this.col)),
      nrm: buf(new Float32Array(this.nrm)), par: buf(new Float32Array(this.par)),
      uv: buf(new Float32Array(this.uv)),
      count: this.idx.length, vertices: this.vertexCount,
    };
    const ib = gl.createBuffer();
    gl.bindElementArrayBuffer = null;
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
    const big = this.vertexCount > 65535;
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,
                  big ? new Uint32Array(this.idx) : new Uint16Array(this.idx),
                  gl.STATIC_DRAW);
    mesh.idx = ib;
    mesh.idxType = big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
    return mesh;
  }
}

/* ============================== colour helpers =========================== */
const Color = {
  hex(h) {
    const n = parseInt(h.replace("#", ""), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  },
  mix(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  },
  /* deterministic per-object jitter, so a street of identical houses still
   * reads as a street of individual houses */
  jitter(c, seed, amount) {
    const r = (Math.sin(seed * 12.9898) * 43758.5453) % 1;
    const f = 1 + (r - 0.5) * 2 * amount;
    return [
      Math.min(Math.max(c[0] * f, 0), 1),
      Math.min(Math.max(c[1] * f, 0), 1),
      Math.min(Math.max(c[2] * f, 0), 1),
    ];
  },
  /* sample a ramp of hex stops at t ∈ [0, 1] */
  ramp(stops, t) {
    const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
    const i = Math.min(Math.floor(x), stops.length - 2);
    return Color.mix(stops[i], stops[i + 1], x - i);
  },
};

/* min/max over a large typed array.
 * `Math.min(...arr)` throws past roughly 100k elements — the argument limit —
 * and the Hamilton grid is well past that. */
function extent(arr) {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return [lo === Infinity ? 0 : lo, hi === -Infinity ? 0 : hi];
}

window.CitySimGL = { V3, M4, MeshBuilder, Color, buildProgram, extent };
