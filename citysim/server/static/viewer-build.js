/* CitySim scene meshes — terrain, buildings, streets, vegetation, water.
 *
 * Everything the renderer draws is built here from the scene payload and
 * uploaded once. The goal in V2 is that the replay reads as Hamilton: real
 * street ribbons with markings, pitched roofs and brick walls with window rows,
 * escarpment slope shading, park canopy, and the harbour off the north edge.
 *
 * Nothing here is textured — there are no image assets in the project and the
 * static deploy has to stay self-contained. The detail comes from geometry,
 * per-vertex ambient occlusion, and procedural patterns evaluated in the
 * fragment shader from surface-local coordinates.
 */
"use strict";

(function () {
const { MeshBuilder, Color, V3, extent } = window.CitySimGL;

/* surface kinds, matching the switch in the fragment shader */
const KIND = { UNLIT: 0, LIT: 1, WATER: 2, WALL: 3, ROOF: 4, ROAD: 5, FOLIAGE: 6 };

/* ------------------------------------------------------------- palettes -- */
const LC_COLORS = [
  Color.hex("#2b4f74"),   // 0 water
  Color.hex("#55534e"),   // 1 paved — must read against grass from above
  Color.hex("#4a4643"),   // 2 building footprint (mostly hidden)
  Color.hex("#4a5738"),   // 3 grass
  Color.hex("#36452a"),   // 4 trees
  Color.hex("#6b6152"),   // 5 bare soil
];

/* Wall albedo by construction material. Hamilton's older stock is a lot of red
 * and buff brick; post-war is painted wood siding. */
const WALL_COLORS = {
  masonry: [Color.hex("#8d4b39"), Color.hex("#9c6046"), Color.hex("#a87250"),
            Color.hex("#7d4433")],
  wood_frame: [Color.hex("#b9b3a4"), Color.hex("#9fa89b"), Color.hex("#c4b49c"),
               Color.hex("#8fa0a6"), Color.hex("#cbc3b2")],
  concrete: [Color.hex("#9a9790"), Color.hex("#8b8880")],
  steel: [Color.hex("#7d848a"), Color.hex("#6d757c")],
};
/* Roofs are most of what an isometric view actually shows, so they carry the
 * variety: weathered asphalt shingle in greys, browns and the faded greens and
 * reds that show up across Hamilton's older stock. */
const ROOF_COLORS = {
  residential: [Color.hex("#5a534b"), Color.hex("#6b6055"), Color.hex("#474b47"),
                Color.hex("#4c4740"), Color.hex("#6e5f52"), Color.hex("#565e56"),
                Color.hex("#75604f"), Color.hex("#5e4f4a")],
  commercial: [Color.hex("#66625a"), Color.hex("#57534c"), Color.hex("#726c62")],
  industrial: [Color.hex("#798086"), Color.hex("#68707a"), Color.hex("#8a8f93")],
  institutional: [Color.hex("#6b6257"), Color.hex("#786d5f")],
};

const ROAD_COLORS = {
  motorway: Color.hex("#43423f"),
  primary: Color.hex("#46443f"),
  secondary: Color.hex("#413f3b"),
  residential: Color.hex("#3c3a37"),
  service: Color.hex("#383633"),
};
const ROAD_HALF = { motorway: 11, primary: 8, secondary: 6, residential: 4.5, service: 3 };

const LOSS_RAMP = ["#2f4858", "#5b6b56", "#a08b4a", "#d1762f", "#c9401f", "#8f1d10"]
  .map(Color.hex);
const AGE_RAMP = ["#7a3b2e", "#a86b45", "#c8a86a", "#8fa87c", "#5d8ba0"].map(Color.hex);
const VALUE_RAMP = ["#37474f", "#4b6b6a", "#6f8f6a", "#b0a35c", "#d98f4a"].map(Color.hex);

/* ============================ scene geometry ============================= */
class SceneBuilder {
  constructor(scene) {
    this.scene = scene;
    const [ny, nx] = scene.shape;
    this.nx = nx;
    this.ny = ny;
    this.cs = scene.cell_size;
    this.dtm = scene.dtm;
    this.lc = scene.landcover;
    const [zmin, zmax] = extent(scene.dtm);
    this.zmin = zmin;
    this.zmax = zmax;
    // A little vertical exaggeration: over two kilometres of city the twenty
    // metres of real relief between the harbour and the escarpment toe would
    // otherwise be invisible, and that relief is the whole story.
    this.zScale = 1.5;
    this.cx = (nx * this.cs) / 2;
    this.cy = (ny * this.cs) / 2;
    this.features = scene.features || {};
  }

  wx(x) { return x - this.cx; }
  wy(y) { return y - this.cy; }
  wz(z) { return (z - this.zmin) * this.zScale; }

  /* bare-earth elevation at a metric coordinate, edge-clamped so context
   * geometry outside the simulated window still has ground under it */
  elev(x, y) {
    const j = Math.min(Math.max(Math.round(x / this.cs), 0), this.nx - 1);
    const i = Math.min(Math.max(Math.round(y / this.cs), 0), this.ny - 1);
    return this.dtm[i * this.nx + j];
  }

  /* world-space z for a metric coordinate — the callback meshes are built with */
  zAt(dz) {
    const self = this;
    return (x, y) => self.wz(self.elev(x, y)) + (dz || 0);
  }

  /* ------------------------------------------------------------ terrain -- */
  terrain() {
    const m = new MeshBuilder();
    const { nx, ny, cs } = this;
    const relief = Math.max(this.zmax - this.zmin, 1);

    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        const k = i * nx + j;
        const z = this.dtm[k];
        const cls = this.lc[k];
        // central-difference normal, in world (exaggerated) units
        const zl = this.dtm[i * nx + Math.max(j - 1, 0)];
        const zr = this.dtm[i * nx + Math.min(j + 1, nx - 1)];
        const zd = this.dtm[Math.max(i - 1, 0) * nx + j];
        const zu = this.dtm[Math.min(i + 1, ny - 1) * nx + j];
        const n = V3.norm([-(zr - zl) * this.zScale / (2 * cs),
                           -(zu - zd) * this.zScale / (2 * cs), 1]);

        let c = LC_COLORS[cls] || LC_COLORS[3];
        // Elevation banding: the ground dries and thins as it climbs toward the
        // escarpment, and the banding makes twenty metres of relief legible.
        const band = (z - this.zmin) / relief;
        c = Color.mix(c, [c[0] * 1.16 + 0.03, c[1] * 1.08 + 0.02, c[2] * 0.94], band * 0.38);
        // steep faces read as exposed rock — this is what draws the escarpment
        const steep = Math.min(Math.max((1 - n[2]) * 4.5, 0), 1);
        c = Color.mix(c, Color.hex("#6d6155"), steep * 0.75);
        c = Color.jitter(c, k, 0.07);

        m.vertex([this.wx(j * cs), this.wy(i * cs), this.wz(z)],
                 c, n, 1, 1, KIND.LIT, 0, j * cs, i * cs);
      }
    }
    for (let i = 0; i < ny - 1; i++) {
      for (let j = 0; j < nx - 1; j++) {
        const a = i * nx + j;
        m.quad(a, a + 1, a + nx + 1, a + nx);
      }
    }
    return m;
  }

  /* A coarse apron continuing the ground past the simulated window, so the
   * streets and shoreline that carry on past the grid are not floating in the
   * void. Edge-clamped elevations, every eighth cell. */
  surround() {
    const m = new MeshBuilder();
    const pad = 1800, step = this.cs * 8;
    const x0 = -pad, x1 = this.nx * this.cs + pad;
    const y0 = -pad, y1 = this.ny * this.cs + pad;
    const cols = Math.ceil((x1 - x0) / step) + 1;
    const rows = Math.ceil((y1 - y0) / step) + 1;
    for (let i = 0; i < rows; i++) {
      for (let j = 0; j < cols; j++) {
        const x = x0 + j * step, y = y0 + i * step;
        const z = this.elev(x, y) - 0.4;
        // muted, so the apron reads as context and the twin stays the subject
        const c = Color.jitter(Color.mix(LC_COLORS[3], LC_COLORS[1], 0.55), i * cols + j, 0.05);
        m.vertex([this.wx(x), this.wy(y), this.wz(z)], c, [0, 0, 1], 1, 0.78,
                 KIND.LIT, 0, x, y);
      }
    }
    for (let i = 0; i < rows - 1; i++)
      for (let j = 0; j < cols - 1; j++) {
        const a = i * cols + j;
        m.quad(a, a + 1, a + cols + 1, a + cols);
      }
    return m;
  }

  /* ------------------------------------------------------------ streets -- */
  streets() {
    const m = new MeshBuilder();
    const roads = this.features.roads || [];
    for (const r of roads) {
      const cls = r.class || "residential";
      const half = ROAD_HALF[cls] || 4.5;
      const pts = r.pts.map(p => [p[0], p[1]]);
      if (pts.length < 2) continue;
      const local = pts.map(p => [this.wx(p[0]), this.wy(p[1])]);
      const zAt = (x, y) => this.wz(this.elev(x + this.cx, y + this.cy)) + 0.12;
      m.ribbon(local, half, zAt, ROAD_COLORS[cls] || ROAD_COLORS.residential,
               1, KIND.ROAD, cls === "residential" || cls === "service" ? 0 : 1);
    }
    // rail ballast
    for (const r of this.features.rail || []) {
      const local = r.pts.map(p => [this.wx(p[0]), this.wy(p[1])]);
      if (local.length < 2) continue;
      m.ribbon(local, 4.0, (x, y) => this.wz(this.elev(x + this.cx, y + this.cy)) + 0.2,
               Color.hex("#5b5349"), 1, KIND.LIT, 0);
    }
    return m;
  }

  /* -------------------------------------------------- parks & open water -- */
  landscape() {
    const m = new MeshBuilder();
    const up = [0, 0, 1];
    for (const pk of this.features.parks || []) {
      const pts = (pk.poly || []).map(p => [this.wx(p[0]), this.wy(p[1])]);
      if (pts.length < 3) continue;
      m.polygon(pts, (x, y) => this.wz(this.elev(x + this.cx, y + this.cy)) + 0.08,
                Color.hex("#4f6b3a"), up, 1, 1, KIND.LIT, 0);
    }
    for (const w of this.features.water || []) {
      if (w.poly) {
        const pts = w.poly.map(p => [this.wx(p[0]), this.wy(p[1])]);
        if (pts.length < 3) continue;
        // open water sits at a flat level, not draped on the bed
        const lvl = this.wz(this.zmin) + 0.6;
        m.polygon(pts, () => lvl, Color.hex("#20415f"), up, 0.94, 1, KIND.WATER, 1);
      } else if (w.pts) {
        const pts = w.pts.map(p => [this.wx(p[0]), this.wy(p[1])]);
        if (pts.length < 2) continue;
        m.ribbon(pts, 7, (x, y) => this.wz(this.elev(x + this.cx, y + this.cy)) + 0.15,
                 Color.hex("#2a5273"), 0.92, KIND.WATER, 1);
      }
    }
    return m;
  }

  /* --------------------------------------------------------- vegetation -- */
  trees() {
    const m = new MeshBuilder();
    const { nx, ny, cs } = this;
    const trunk = Color.hex("#4a3a2c");
    let seed = 1;
    const rand = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    };
    for (let i = 0; i < ny; i++) {
      for (let j = 0; j < nx; j++) {
        if (this.lc[i * nx + j] !== 4) continue;
        const n = rand() < 0.55 ? 2 : 1;
        for (let t = 0; t < n; t++) {
          const x = (j + rand()) * cs, y = (i + rand()) * cs;
          const h = 6 + rand() * 6;
          const r = 2.2 + rand() * 1.8;
          const base = this.wz(this.elev(x, y));
          const shade = 0.8 + rand() * 0.35;
          const canopy = Color.jitter(
            Color.mix(Color.hex("#3f5f2c"), Color.hex("#5c7a38"), rand()), t + i * 7 + j, 0.1);
          // trunk
          const tw = 0.35;
          this._box(m, this.wx(x), this.wy(y), base, base + h * 0.42, tw, tw,
                    trunk, 0.9, KIND.LIT);
          // two crossed billboard quads, cheap and convincing in a parallel view
          for (const ang of [0, Math.PI / 2]) {
            const ux = Math.cos(ang) * r, uy = Math.sin(ang) * r;
            const z0 = base + h * 0.3, z1 = base + h;
            const v = [
              m.vertex([this.wx(x) - ux, this.wy(y) - uy, z0], canopy, [0, 0, 1],
                       1, 0.72 * shade, KIND.FOLIAGE, 0, 0, 0),
              m.vertex([this.wx(x) + ux, this.wy(y) + uy, z0], canopy, [0, 0, 1],
                       1, 0.72 * shade, KIND.FOLIAGE, 0, 1, 0),
              m.vertex([this.wx(x) + ux, this.wy(y) + uy, z1], canopy, [0, 0, 1],
                       1, 1.05 * shade, KIND.FOLIAGE, 0, 1, 1),
              m.vertex([this.wx(x) - ux, this.wy(y) - uy, z1], canopy, [0, 0, 1],
                       1, 1.05 * shade, KIND.FOLIAGE, 0, 0, 1),
            ];
            m.quad(v[0], v[1], v[2], v[3]);
          }
        }
      }
    }
    return m;
  }

  _box(m, x, y, z0, z1, hw, hd, c, ao, kind) {
    const corners = [[x - hw, y - hd], [x + hw, y - hd], [x + hw, y + hd], [x - hw, y + hd]];
    for (let i = 0; i < 4; i++) {
      const [ax, ay] = corners[i], [bx, by] = corners[(i + 1) % 4];
      const n = V3.norm([by - ay, ax - bx, 0]);
      const v = [
        m.vertex([ax, ay, z0], c, n, 1, ao * 0.7, kind, 0, 0, 0),
        m.vertex([bx, by, z0], c, n, 1, ao * 0.7, kind, 0, 1, 0),
        m.vertex([bx, by, z1], c, n, 1, ao, kind, 0, 1, z1 - z0),
        m.vertex([ax, ay, z1], c, n, 1, ao, kind, 0, 0, z1 - z0),
      ];
      m.quad(v[0], v[1], v[2], v[3]);
    }
    const top = corners.map(p => m.vertex([p[0], p[1], z1], c, [0, 0, 1], 1, ao, kind, 0, 0, 0));
    m.quad(top[0], top[1], top[2], top[3]);
  }

  /* ---------------------------------------------------------- buildings -- */
  buildings(mode, lossArr, hoverId, selectedId) {
    const m = new MeshBuilder();
    const bs = this.scene.buildings;
    this.buildingBounds = [];

    let lossMax = 1;
    if (mode === "loss" && lossArr) {
      const [, hi] = extent(lossArr);
      lossMax = Math.max(hi, 1);
    }
    const years = bs.map(b => b.year_built);
    const [yMin, yMax] = extent(years);
    const values = bs.map(b => b.structure_value);
    const [, vMax] = extent(values);

    bs.forEach((b, bi) => {
      const fp = b.footprint;
      if (!fp || fp.length < 3) return;
      const seedN = bi + 1;
      const wall = this._wallColor(b, mode, lossArr ? lossArr[bi] : 0, lossMax,
                                  yMin, yMax, vMax, seedN);
      const roofBase = ROOF_COLORS[b.use] || ROOF_COLORS.residential;
      let roof = Color.jitter(roofBase[bi % roofBase.length], seedN * 3, 0.10);
      if (mode !== "class") roof = Color.mix(wall, roof, 0.35);

      const z0 = this.wz(b.ground_elev) - 0.6;
      const eave = this.wz(b.ground_elev + b.height);
      const highlight = b.id === hoverId ? 0.35 : (b.id === selectedId ? 0.22 : 0);
      const wallC = highlight ? Color.mix(wall, [1, 0.93, 0.75], highlight) : wall;
      const roofC = highlight ? Color.mix(roof, [1, 0.93, 0.75], highlight) : roof;

      // --- walls, one quad per footprint edge, with window rows in the shader
      const storeyH = b.storeys > 0 ? b.height / b.storeys : 3;
      for (let i = 0; i < fp.length; i++) {
        const [x1, y1] = fp[i];
        const [x2, y2] = fp[(i + 1) % fp.length];
        const len = Math.hypot(x2 - x1, y2 - y1);
        if (len < 0.2) continue;
        const n = V3.norm([y2 - y1, x1 - x2, 0]);
        // ambient occlusion darkens the base, where a wall meets the ground and
        // light cannot reach — the single cheapest cue that a box is a building
        const v = [
          m.vertex([this.wx(x1), this.wy(y1), z0], wallC, n, 1, 0.72, KIND.WALL,
                   storeyH, 0, 0),
          m.vertex([this.wx(x2), this.wy(y2), z0], wallC, n, 1, 0.72, KIND.WALL,
                   storeyH, len, 0),
          m.vertex([this.wx(x2), this.wy(y2), eave], wallC, n, 1, 1.0, KIND.WALL,
                   storeyH, len, eave - z0),
          m.vertex([this.wx(x1), this.wy(y1), eave], wallC, n, 1, 1.0, KIND.WALL,
                   storeyH, 0, eave - z0),
        ];
        m.quad(v[0], v[1], v[2], v[3]);
      }

      // --- roof
      const pitched = b.use === "residential" && b.storeys <= 3 && fp.length === 4;
      if (pitched) this._gableRoof(m, fp, eave, roofC);
      else this._flatRoof(m, fp, eave, roofC, b, seedN);

      const xs = fp.map(p => p[0]), ys = fp.map(p => p[1]);
      this.buildingBounds.push({
        id: b.id, b,
        minx: this.wx(Math.min(...xs)), maxx: this.wx(Math.max(...xs)),
        miny: this.wy(Math.min(...ys)), maxy: this.wy(Math.max(...ys)),
        z0, z1: eave + (pitched ? 2.2 : 1.0),
      });
    });
    return m;
  }

  _wallColor(b, mode, loss, lossMax, yMin, yMax, vMax, seedN) {
    if (mode === "loss") {
      const t = Math.log1p(loss || 0) / Math.log1p(lossMax);
      return Color.ramp(LOSS_RAMP, t);
    }
    if (mode === "age") {
      return Color.ramp(AGE_RAMP, (b.year_built - yMin) / Math.max(yMax - yMin, 1));
    }
    if (mode === "value") {
      return Color.ramp(VALUE_RAMP, Math.log1p(b.structure_value) / Math.log1p(vMax || 1));
    }
    if (mode === "storeys") {
      return Color.ramp(VALUE_RAMP, Math.min((b.storeys - 1) / 5, 1));
    }
    if (mode === "material") {
      const base = WALL_COLORS[b.material] || WALL_COLORS.masonry;
      return base[0];
    }
    if (mode === "basement") {
      return b.has_basement ? Color.hex("#c9401f") : Color.hex("#4a6b7a");
    }
    // "class" — the real look: material palette, aged by construction era
    const palette = WALL_COLORS[b.material] || WALL_COLORS.masonry;
    let c = palette[seedN % palette.length];
    const age = Math.min(Math.max((1975 - b.year_built) / 90, 0), 1);
    c = Color.mix(c, [c[0] * 0.82, c[1] * 0.78, c[2] * 0.74], age * 0.45);
    return Color.jitter(c, seedN, 0.09);
  }

  /* A gable over a quad footprint: ridge along the longer axis. Pitched roofs
   * are what stop a residential street looking like a row of shipping crates. */
  _gableRoof(m, fp, eave, c) {
    const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const d01 = Math.hypot(fp[1][0] - fp[0][0], fp[1][1] - fp[0][1]);
    const d12 = Math.hypot(fp[2][0] - fp[1][0], fp[2][1] - fp[1][1]);
    // ridge runs parallel to the longer pair of edges
    const [a, b, cc, d] = d01 >= d12 ? [fp[0], fp[1], fp[2], fp[3]]
                                     : [fp[1], fp[2], fp[3], fp[0]];
    const rise = Math.min(Math.max(Math.min(d01, d12) * 0.26, 1.2), 3.2);
    const ridgeZ = eave + rise;
    const r1 = mid(a, d), r2 = mid(b, cc);
    const top = Color.mix(c, [1, 1, 1], 0.10);

    const face = (p1, p2, q2, q1, ao) => {
      const n = V3.norm([
        (p2[1] - p1[1]) * 0 - 0, 0, 1,
      ]);
      const e1 = [q1[0] - p1[0], q1[1] - p1[1], ridgeZ - eave];
      const e2 = [p2[0] - p1[0], p2[1] - p1[1], 0];
      const nn = V3.norm(V3.cross(e2, e1));
      const v = [
        m.vertex([this.wx(p1[0]), this.wy(p1[1]), eave], c, nn, 1, ao, KIND.ROOF, 0, 0, 0),
        m.vertex([this.wx(p2[0]), this.wy(p2[1]), eave], c, nn, 1, ao, KIND.ROOF, 0, 1, 0),
        m.vertex([this.wx(q2[0]), this.wy(q2[1]), ridgeZ], top, nn, 1, 1, KIND.ROOF, 0, 1, 1),
        m.vertex([this.wx(q1[0]), this.wy(q1[1]), ridgeZ], top, nn, 1, 1, KIND.ROOF, 0, 0, 1),
      ];
      m.quad(v[0], v[1], v[2], v[3]);
      void n;
    };
    face(a, b, r2, r1, 0.86);
    face(cc, d, r1, r2, 0.72);

    // gable ends
    for (const [p, q, r] of [[a, d, r1], [b, cc, r2]]) {
      const v = [
        m.vertex([this.wx(p[0]), this.wy(p[1]), eave], c, [0, 0, 1], 1, 0.8, KIND.ROOF, 0, 0, 0),
        m.vertex([this.wx(q[0]), this.wy(q[1]), eave], c, [0, 0, 1], 1, 0.8, KIND.ROOF, 0, 0, 0),
        m.vertex([this.wx(r[0]), this.wy(r[1]), ridgeZ], c, [0, 0, 1], 1, 0.9, KIND.ROOF, 0, 0, 0),
      ];
      m.tri(v[0], v[1], v[2]);
    }
  }

  /* Flat roof with a parapet lip and a rooftop mechanical unit — the silhouette
   * that separates a commercial block from a house at a glance. */
  _flatRoof(m, fp, eave, c, b, seedN) {
    const up = [0, 0, 1];
    const deck = Color.mix(c, [0.25, 0.25, 0.24], 0.35);
    m.polygon(fp.map(p => [this.wx(p[0]), this.wy(p[1])]), () => eave,
              deck, up, 1, 0.95, KIND.ROOF, 0);
    // parapet
    const lip = eave + 0.7;
    for (let i = 0; i < fp.length; i++) {
      const [x1, y1] = fp[i];
      const [x2, y2] = fp[(i + 1) % fp.length];
      const n = V3.norm([y2 - y1, x1 - x2, 0]);
      const v = [
        m.vertex([this.wx(x1), this.wy(y1), eave], c, n, 1, 0.9, KIND.LIT, 0, 0, 0),
        m.vertex([this.wx(x2), this.wy(y2), eave], c, n, 1, 0.9, KIND.LIT, 0, 0, 0),
        m.vertex([this.wx(x2), this.wy(y2), lip], c, n, 1, 1.05, KIND.LIT, 0, 0, 0),
        m.vertex([this.wx(x1), this.wy(y1), lip], c, n, 1, 1.05, KIND.LIT, 0, 0, 0),
      ];
      m.quad(v[0], v[1], v[2], v[3]);
    }
    if (b.use !== "residential" && (seedN % 3) !== 0) {
      let cxx = 0, cyy = 0;
      for (const p of fp) { cxx += p[0]; cyy += p[1]; }
      cxx /= fp.length; cyy /= fp.length;
      this._box(m, this.wx(cxx), this.wy(cyy), eave, eave + 1.8,
                2.0 + (seedN % 3), 1.6 + (seedN % 2),
                Color.hex("#7e8288"), 1, KIND.LIT);
    }
  }

  /* Soft drop shadows: the footprint offset along the sun direction, laid on
   * the ground. Not a shadow map — in a fixed parallel view this reads as one
   * at a fraction of the cost, and WebGL 1 makes real shadow mapping expensive
   * for what it buys here. */
  shadows() {
    const m = new MeshBuilder();
    const up = [0, 0, 1];
    const dark = [0.05, 0.05, 0.07];
    for (const b of this.scene.buildings) {
      const fp = b.footprint;
      if (!fp || fp.length < 3) continue;
      const off = Math.min(b.height * 0.55, 14);
      const pts = fp.map(p => [this.wx(p[0] + off * 0.72), this.wy(p[1] - off * 0.62)]);
      m.polygon(pts, (x, y) => this.wz(this.elev(x + this.cx, y + this.cy)) + 0.05,
                dark, up, 0.30, 1, KIND.UNLIT, 0);
    }
    return m;
  }

  /* ------------------------------------------------------------- sewer --- */
  sewer() {
    const pipes = new MeshBuilder();
    const nodes = this.scene.sewer.nodes;
    const byId = {};
    nodes.forEach(n => { byId[n.id] = n; });
    const combined = Color.hex("#d95926");
    const storm = Color.hex("#3987e5");

    for (const c of this.scene.sewer.conduits) {
      const a = byId[c.from], b = byId[c.to];
      if (!a || !b) continue;
      const col = c.system === "combined" ? combined : storm;
      const za = this.wz(a.invert_elev !== undefined ? a.invert_elev : a.rim_elev - 2.2);
      const zb = this.wz(b.invert_elev !== undefined ? b.invert_elev : b.rim_elev - 2.2);
      pipes.pos.push(this.wx(a.x), this.wy(a.y), za, this.wx(b.x), this.wy(b.y), zb);
      pipes.col.push(col[0], col[1], col[2], col[0], col[1], col[2]);
      pipes.nrm.push(0, 0, 1, 0, 0, 1);
      pipes.par.push(0.85, 1, KIND.UNLIT, 0, 0.85, 1, KIND.UNLIT, 0);
      pipes.uv.push(0, 0, 0, 0);
      pipes.idx.push(pipes.idx.length, pipes.idx.length + 1);
    }

    const manholes = new MeshBuilder();
    this.manholeRange = [];
    for (const n of nodes) {
      const start = manholes.vertexCount;
      const z = this.wz(n.rim_elev) + 0.5;
      const r = 2.6;
      const c = n.system === "combined" ? Color.hex("#8a5a3a") : Color.hex("#5a6470");
      const v = [
        manholes.vertex([this.wx(n.x) - r, this.wy(n.y) - r, z], c, [0, 0, 1], 1, 1, KIND.UNLIT, 0, 0, 0),
        manholes.vertex([this.wx(n.x) + r, this.wy(n.y) - r, z], c, [0, 0, 1], 1, 1, KIND.UNLIT, 0, 0, 0),
        manholes.vertex([this.wx(n.x) + r, this.wy(n.y) + r, z], c, [0, 0, 1], 1, 1, KIND.UNLIT, 0, 0, 0),
        manholes.vertex([this.wx(n.x) - r, this.wy(n.y) + r, z], c, [0, 0, 1], 1, 1, KIND.UNLIT, 0, 0, 0),
      ];
      manholes.quad(v[0], v[1], v[2], v[3]);
      this.manholeRange.push([start, 4]);
    }
    return { pipes, manholes };
  }

  /* ------------------------------------------------------------- water --- */
  /* The flood surface: one vertex per grid cell, coincident with the terrain
   * and parked below it while dry. Positions, colours and alphas are rewritten
   * every replay frame, so this mesh is DYNAMIC_DRAW. */
  water() {
    const m = new MeshBuilder();
    const { nx, ny, cs } = this;
    for (let i = 0; i < ny; i++)
      for (let j = 0; j < nx; j++)
        m.vertex([this.wx(j * cs), this.wy(i * cs), this.wz(this.dtm[i * nx + j]) - 0.5],
                 [0.33, 0.55, 0.78], [0, 0, 1], 0, 1, KIND.WATER, 0, j * cs, i * cs);
    for (let i = 0; i < ny - 1; i++)
      for (let j = 0; j < nx - 1; j++) {
        const a = i * nx + j;
        m.quad(a, a + 1, a + nx + 1, a + nx);
      }
    return m;
  }
}

window.CitySimScene = { SceneBuilder, KIND, LOSS_RAMP, AGE_RAMP, VALUE_RAMP, LC_COLORS };
})();
