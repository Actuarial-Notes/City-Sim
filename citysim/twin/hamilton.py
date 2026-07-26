"""Hamilton twin builder — a twin laid on the city's real geometry.

The procedural generator in ``synthetic.py`` produces a Hamilton-*like* region:
right escarpment, right harbour, right sewer configuration, invented streets.
This module builds the same kind of twin on Hamilton's actual street grid,
shoreline and escarpment, so the replay is recognizably the city rather than an
abstraction of it.

Geometry comes from ``data/hamilton_lower_city.json.gz`` when
``scripts/bake_hamilton.py`` has fetched it from OpenStreetMap, and otherwise
from the hand-digitized fallback in ``data/hamilton_base.py``. Both are the same
shape, so nothing downstream cares which one is in play — only the provenance
strings and the Sources tab change.

What is real: street centrelines and names, the escarpment brow, the harbour
shoreline, parks, rail corridors, the industrial north end, the combined-sewer
core, and — with the OSM bake — building footprints, storeys and use.

What is still generated: terrain elevations (conditioned on the real escarpment
and shoreline, since the LiDAR connector needs GDAL), the sewer network (laid
along the real streets), and per-building age, material, foundation and value.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from . import params as twin_params
from .schema import (
    Building, SewerConduit, SewerNetwork, SewerNode, Terrain, Twin,
)
from .synthetic import (
    _poly_area, _sample_material, _smooth, landcover_from_classes, replacement_costs,
)

DATA_DIR = Path(__file__).parent / "data"
BAKED = DATA_DIR / "hamilton_lower_city.json.gz"

M_PER_DEG_LAT = 111_320.0

# Paved half-width per road class, metres. This is the travelled way, not the
# full right-of-way — it feeds the imperviousness grid, so what matters is the
# asphalt, not the boulevard.
ROAD_HALF_WIDTH = {"motorway": 11.0, "primary": 8.0, "secondary": 6.0,
                   "residential": 4.5, "service": 3.0}
# how far apart to place lots along a street, and the front yard beyond the kerb
LOT_PITCH = {"residential": 11.0, "secondary": 14.0, "primary": 16.0}
FRONT_YARD = {"residential": 4.0, "secondary": 5.0, "primary": 4.0}


# --------------------------------------------------------------- asset load
def load_asset() -> dict | None:
    """The baked OSM extract if present, else the hand-digitized fallback."""
    if BAKED.exists():
        try:
            with gzip.open(BAKED, "rt", encoding="utf-8") as fh:
                asset = json.load(fh)
            if asset.get("roads"):
                return asset
        except (OSError, ValueError, EOFError):
            # a truncated or corrupt bake must not take the app down; the
            # fallback below is always available
            pass
    try:
        from .data.hamilton_base import base_asset
        return base_asset()
    except ImportError:
        return None


# ------------------------------------------------------------- projection
class Projection:
    """Local metric CRS: metres east/north of the simulation window's SW corner.

    An equirectangular projection about the window's centre latitude. Over a
    two-kilometre extent the distortion is far below the cell size, and it keeps
    the twin free of a projection dependency.
    """

    def __init__(self, sim_bbox: list[float]):
        self.w, self.s, self.e, self.n = sim_bbox
        self.lat0 = (self.s + self.n) / 2
        self.m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(self.lat0))
        self.width_m = (self.e - self.w) * self.m_per_deg_lon
        self.height_m = (self.n - self.s) * M_PER_DEG_LAT

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.w) * self.m_per_deg_lon, (lat - self.s) * M_PER_DEG_LAT)

    def line(self, pts) -> list[tuple[float, float]]:
        return [self.xy(p[0], p[1]) for p in pts]


# ------------------------------------------------------------------ helpers
def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                inside = not inside
    return inside


def _dist_to_polyline(X: np.ndarray, Y: np.ndarray,
                      pts: list[tuple[float, float]]) -> np.ndarray:
    """Distance from every grid point to a polyline, vectorised over segments."""
    best = np.full(X.shape, np.inf)
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 < 1e-9:
            continue
        t = np.clip(((X - x1) * dx + (Y - y1) * dy) / L2, 0.0, 1.0)
        px, py = x1 + t * dx, y1 + t * dy
        np.minimum(best, np.hypot(X - px, Y - py), out=best)
    return best


def _signed_north_of(X: np.ndarray, Y: np.ndarray,
                     pts: list[tuple[float, float]]) -> np.ndarray:
    """Metres north of a roughly west–east polyline (negative when south of it).

    Used for the escarpment: the brow is a line, and what matters hydrologically
    is which side of it you are on and how far.
    """
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    order = np.argsort(xs)
    line_y = np.interp(X, xs[order], ys[order])
    return Y - line_y


def _polyline_length(pts) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))


def _walk(pts: list[tuple[float, float]], step: float):
    """Yield (point, unit tangent) every ``step`` metres along a polyline."""
    carry = 0.0
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        seg = math.dist((x1, y1), (x2, y2))
        if seg < 1e-6:
            continue
        ux, uy = (x2 - x1) / seg, (y2 - y1) / seg
        d = carry
        while d < seg:
            yield (x1 + ux * d, y1 + uy * d), (ux, uy)
            d += step
        carry = d - seg


def _seg_intersect(a1, a2, b1, b2):
    """Intersection point of two segments, or None."""
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


# ------------------------------------------------------------------- terrain
def _build_terrain(proj: Projection, asset: dict, p: dict, nx: int, ny: int,
                   cell: float, rng) -> Terrain:
    """Elevations conditioned on the real escarpment and shoreline.

    The LiDAR DTM connector needs GDAL-class dependencies, so the surface itself
    is generated — but it is generated *against* real geometry rather than an
    invented landscape: ground falls toward the actual harbour shoreline and
    rises toward the actual escarpment brow, and the real creek corridors are
    carved in as the ponding lines they are.
    """
    xs = np.arange(nx) * cell
    ys = np.arange(ny) * cell
    X, Y = np.meshgrid(xs, ys)

    # --- fall toward the harbour shoreline (the southern edge of the bay polygon)
    shore = None
    for w in asset.get("water", []):
        if w.get("kind") == "bay" and w.get("poly") and w.get("name") != "Cootes Paradise":
            poly = proj.line(w["poly"])
            # the shoreline proper is the southern boundary of the water body
            south_edge = sorted(poly, key=lambda q: q[0])
            shore = [q for q in south_edge if q[1] < max(pt[1] for pt in poly) - 1.0]
            if len(shore) < 2:
                shore = south_edge
            break
    if shore and len(shore) >= 2:
        dist_from_shore = np.maximum(_signed_north_of(X, Y, shore) * -1.0, 0.0)
    else:
        dist_from_shore = np.maximum(proj.height_m - Y, 0.0)

    dtm = p["base_elev"] - 3.0 + dist_from_shore * p["slope_to_bay"]

    # --- rise toward the escarpment brow
    for esc in asset.get("escarpment", []):
        brow = proj.line(esc["pts"])
        if len(brow) < 2:
            continue
        north_of_brow = _signed_north_of(X, Y, brow)
        # logistic toe: full height on the mountain, decaying into the lower city
        dtm = dtm + p["escarpment_height"] / (1.0 + np.exp(north_of_brow / 220.0))

    # --- carve the creek corridors
    for w in asset.get("water", []):
        if w.get("kind") != "creek" or not w.get("pts"):
            continue
        line = proj.line(w["pts"])
        if len(line) < 2:
            continue
        d = _dist_to_polyline(X, Y, line)
        dtm = dtm - p["valley_depth"] * np.exp(-(d ** 2) / (2 * (70.0 ** 2)))

    dtm = dtm + _smooth(rng.normal(0.0, 0.40, (ny, nx)), passes=3)
    return Terrain(dtm=dtm.astype(np.float64), cell_size=cell)


# ------------------------------------------------------------------ land use
def _use_at(x: float, y: float, landuse_local: list[tuple[str, list]]) -> str | None:
    for kind, poly in landuse_local:
        if _point_in_poly(x, y, poly):
            return kind
    return None


# ----------------------------------------------------------------- buildings
def _generate_stock(proj: Projection, asset: dict, terrain: Terrain, p: dict,
                    rng) -> list[Building]:
    """Place building stock fronting the real streets.

    Real cities are built along their streets, so rather than detecting blocks
    and filling them, this walks each street centreline and sets lots back from
    it on both sides — which reproduces the street wall, the setback, and the
    back-to-back rear yards without any block topology at all. An occupancy grid
    keeps lots from colliding where streets converge.
    """
    W, H = proj.width_m, proj.height_m
    cell = terrain.cell_size

    landuse_local = [(lu["kind"], proj.line(lu["poly"]))
                     for lu in asset.get("landuse", []) if lu.get("poly")]
    parks_local = [proj.line(pk["poly"]) for pk in asset.get("parks", []) if pk.get("poly")]
    water_local = [proj.line(w["poly"]) for w in asset.get("water", []) if w.get("poly")]

    # coarse occupancy grid — 3 m is finer than the narrowest side yard
    og_cell = 3.0
    og = np.zeros((int(H / og_cell) + 2, int(W / og_cell) + 2), dtype=bool)

    def _box(pts):
        xs_ = [q[0] for q in pts]
        ys_ = [q[1] for q in pts]
        return (int(min(xs_) / og_cell), int(max(xs_) / og_cell) + 1,
                int(min(ys_) / og_cell), int(max(ys_) / og_cell) + 1)

    def occupied(pts) -> bool:
        j0, j1, i0, i1 = _box(pts)
        if j0 < 0 or i0 < 0 or j1 >= og.shape[1] or i1 >= og.shape[0]:
            return True
        return bool(og[i0:i1, j0:j1].any())

    def occupy(pts) -> None:
        j0, j1, i0, i1 = _box(pts)
        og[max(i0, 0):i1, max(j0, 0):j1] = True

    # street rights-of-way are off limits
    for road in asset.get("roads", []):
        half = ROAD_HALF_WIDTH.get(road.get("class", "residential"), 5.0)
        line = proj.line(road["pts"])
        if len(line) < 2:
            continue
        for (px, py), (ux, uy) in _walk(line, og_cell):
            if -half <= px <= W + half and -half <= py <= H + half:
                occupy([(px - half, py - half), (px + half, py + half)])

    buildings: list[Building] = []
    bid = 0
    escarp_lines = [proj.line(e["pts"]) for e in asset.get("escarpment", [])]

    for road in asset.get("roads", []):
        cls = road.get("class", "residential")
        if cls == "motorway":
            continue                       # no frontage on a freeway
        line = proj.line(road["pts"])
        if len(line) < 2 or _polyline_length(line) < 40:
            continue
        pitch = LOT_PITCH.get(cls, 13.0)
        half_road = ROAD_HALF_WIDTH.get(cls, 5.0)
        front_yard = FRONT_YARD.get(cls, 6.0)

        for (px, py), (ux, uy) in _walk(line, pitch):
            for side in (-1, 1):
                # normal to the street; the narrow face of the lot fronts it
                nxn, nyn = -uy * side, ux * side

                use_probe_x = px + nxn * (half_road + front_yard + 8.0)
                use_probe_y = py + nyn * (half_road + front_yard + 8.0)
                use = _use_at(use_probe_x, use_probe_y, landuse_local) or "residential"
                if use == "residential" and cls == "primary":
                    use = "commercial" if rng.random() < 0.45 else "residential"

                if use == "residential":
                    w = rng.uniform(8.0, 11.5)
                    d = rng.uniform(9.0, 14.0)
                elif use == "industrial":
                    w, d = rng.uniform(24.0, 45.0), rng.uniform(20.0, 38.0)
                else:
                    w, d = rng.uniform(16.0, 30.0), rng.uniform(14.0, 26.0)

                # the lot sits clear of the right-of-way: half the road, a front
                # yard, then half the building depth
                setback = half_road + front_yard + d / 2
                cx = px + nxn * setback
                cy = py + nyn * setback
                if not (8 < cx < W - 8 and 8 < cy < H - 8):
                    continue
                if any(_point_in_poly(cx, cy, poly) for poly in parks_local):
                    continue
                if any(_point_in_poly(cx, cy, poly) for poly in water_local):
                    continue
                if rng.random() < p["park_block_share"]:
                    continue               # a vacant lot here and there

                # footprint oriented to the street: w along it, d away from it
                hw, hd = w / 2, d / 2
                fp = [(cx + ux * hw + nxn * hd, cy + uy * hw + nyn * hd),
                      (cx - ux * hw + nxn * hd, cy - uy * hw + nyn * hd),
                      (cx - ux * hw - nxn * hd, cy - uy * hw - nyn * hd),
                      (cx + ux * hw - nxn * hd, cy + uy * hw - nyn * hd)]

                if occupied(fp):
                    continue
                occupy(fp)
                fp = [(round(a, 2), round(b, 2)) for a, b in fp]

                year = _sample_year_hamilton(rng, cx, cy, W, H, escarp_lines)
                if use == "residential":
                    storeys = int(rng.choice(
                        [1, 2, 3], p=_storey_probs(p)))
                    is_apartment = rng.random() < p["apartment_share"]
                    if is_apartment:
                        storeys = int(rng.integers(3, 6))
                    basement = rng.random() < (p["basement_rate_pre1990"] if year < 1990
                                               else p["basement_rate_post1990"])
                    height = storeys * 3.0 + rng.uniform(0.5, 1.5)
                    material = _sample_material(rng, year, p)
                    units = int(rng.integers(6, 20)) if is_apartment else 1
                else:
                    storeys = int(rng.integers(1, 4)) if use != "industrial" else 1
                    basement = False
                    height = storeys * 4.5
                    material = "concrete" if use == "industrial" else "masonry"
                    units = 0

                buildings.append(Building(
                    id=f"b{bid}", footprint=fp, centroid=(round(cx, 2), round(cy, 2)),
                    height=round(height, 2), storeys=storeys, year_built=year,
                    use=use, material=material,
                    foundation="basement" if basement else "slab",
                    basement_depth=round(rng.uniform(p["basement_depth_min"],
                                                     p["basement_depth_max"]), 2)
                    if basement else 0.0,
                    ground_elev=terrain.elevation_at(cx, cy),
                    first_floor_height=round(rng.uniform(p["first_floor_height_min"],
                                                         p["first_floor_height_max"]), 2),
                    dwelling_units=units,
                ))
                bid += 1

    _assign_values(buildings, p, rng)
    return buildings


def _storey_probs(p: dict) -> list[float]:
    w = np.array([p["storey_weight_1"], p["storey_weight_2"], p["storey_weight_3"]],
                 dtype=float)
    return list(w / w.sum())


def _sample_year_hamilton(rng, x, y, W, H, escarp_lines) -> int:
    """Hamilton built outward from the harbour: oldest stock in the north end and
    the core, newer toward the escarpment."""
    frac_north = y / max(H, 1.0)
    if frac_north > 0.62:
        return int(rng.integers(1890, 1950))
    if frac_north > 0.30:
        return int(rng.integers(1900, 1975))
    return int(rng.integers(1940, 2015))


def _assign_values(buildings: list[Building], p: dict, rng) -> None:
    costs = replacement_costs(p)
    disp = p["value_dispersion"]
    for b in buildings:
        area = _poly_area(b.footprint) * max(b.storeys, 1)
        b.structure_value = round(area * costs[b.use]
                                  * rng.uniform(1.0 - disp, 1.0 + disp), -2)
        ratio = (p["contents_ratio_residential"] if b.use == "residential"
                 else p["contents_ratio_other"])
        b.contents_value = round(b.structure_value * ratio, -2)


def _buildings_from_osm(proj: Projection, asset: dict, terrain: Terrain, p: dict,
                        rng) -> list[Building]:
    """Real OSM footprints, with the attributes OSM does not carry inferred."""
    W, H = proj.width_m, proj.height_m
    buildings: list[Building] = []
    bid = 0
    escarp_lines = [proj.line(e["pts"]) for e in asset.get("escarpment", [])]
    for feat in asset["buildings"]:
        fp = proj.line(feat["poly"])
        if len(fp) < 3:
            continue
        cx = sum(q[0] for q in fp) / len(fp)
        cy = sum(q[1] for q in fp) / len(fp)
        if not (0 <= cx < W and 0 <= cy < H):
            continue
        area = _poly_area(fp)
        if area < 25:
            continue
        use = feat.get("use") or "residential"
        storeys = int(feat.get("storeys") or 0) or int(rng.choice([1, 2, 3],
                                                                  p=_storey_probs(p)))
        height = float(feat.get("height") or 0.0) or storeys * 3.0 + 1.0
        year = int(feat.get("year") or 0) or _sample_year_hamilton(
            rng, cx, cy, W, H, escarp_lines)
        basement = use == "residential" and rng.random() < (
            p["basement_rate_pre1990"] if year < 1990 else p["basement_rate_post1990"])
        buildings.append(Building(
            id=f"b{bid}", footprint=[(round(a, 2), round(b_, 2)) for a, b_ in fp],
            centroid=(round(cx, 2), round(cy, 2)), height=round(height, 2),
            storeys=storeys, year_built=year, use=use,
            material=_sample_material(rng, year, p) if use == "residential"
            else ("concrete" if use == "industrial" else "masonry"),
            foundation="basement" if basement else "slab",
            basement_depth=round(rng.uniform(p["basement_depth_min"],
                                             p["basement_depth_max"]), 2) if basement else 0.0,
            ground_elev=terrain.elevation_at(cx, cy),
            first_floor_height=round(rng.uniform(p["first_floor_height_min"],
                                                 p["first_floor_height_max"]), 2),
            dwelling_units=(int(feat.get("units") or 1)) if use == "residential" else 0,
        ))
        bid += 1
    _assign_values(buildings, p, rng)
    return buildings


# --------------------------------------------------------------- land cover
def _build_landcover(proj: Projection, asset: dict, terrain: Terrain,
                     buildings: list[Building], rng):
    ny, nx = terrain.shape
    cell = terrain.cell_size
    classes = np.full((ny, nx), 3, dtype=np.int8)          # grass by default
    xs = np.arange(nx) * cell
    ys = np.arange(ny) * cell
    X, Y = np.meshgrid(xs, ys)

    # tree pockets before anything else paves over them
    tree_noise = _smooth(rng.random((ny, nx)), passes=2)
    classes[tree_noise > 0.62] = 4

    for pk in asset.get("parks", []):
        if not pk.get("poly"):
            continue
        poly = proj.line(pk["poly"])
        inside = _rasterize_poly(poly, X, Y)
        classes[inside & (tree_noise <= 0.55)] = 3
        classes[inside & (tree_noise > 0.55)] = 4

    for w in asset.get("water", []):
        if w.get("poly"):
            classes[_rasterize_poly(proj.line(w["poly"]), X, Y)] = 0
        elif w.get("pts"):
            line = proj.line(w["pts"])
            if len(line) >= 2:
                classes[_dist_to_polyline(X, Y, line) < cell * 1.2] = 0

    # real streets become the paved network
    for road in asset.get("roads", []):
        line = proj.line(road["pts"])
        if len(line) < 2:
            continue
        half = ROAD_HALF_WIDTH.get(road.get("class", "residential"), 5.0)
        classes[_dist_to_polyline(X, Y, line) < half] = 1

    for r in asset.get("rail", []):
        line = proj.line(r["pts"])
        if len(line) >= 2:
            classes[_dist_to_polyline(X, Y, line) < 6.0] = 5     # ballast

    # Roofs. Stamping a footprint's bounding box would roughly quadruple the
    # built area at this cell size, so cells are claimed only when their centre
    # actually falls inside the polygon — with the centroid cell guaranteed so a
    # sub-cell building is not lost entirely.
    built = np.zeros((ny, nx), dtype=bool)
    for b in buildings:
        bx = [q[0] for q in b.footprint]
        by = [q[1] for q in b.footprint]
        j0 = max(int(min(bx) / cell), 0)
        j1 = min(int(max(bx) / cell) + 1, nx - 1)
        i0 = max(int(min(by) / cell), 0)
        i1 = min(int(max(by) / cell) + 1, ny - 1)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if _point_in_poly((j + 0.5) * cell, (i + 0.5) * cell, b.footprint):
                    built[i, j] = True
        built[min(max(int(b.centroid[1] / cell), 0), ny - 1),
              min(max(int(b.centroid[0] / cell), 0), nx - 1)] = True

    # Driveways, walkways, rear parking pads and laneways: the paved fabric that
    # fills a dense lower-city block between the roof and the street. Without it
    # the twin comes out mostly lawn and badly under-predicts runoff.
    ring = np.zeros((ny, nx), dtype=bool)
    ring[1:, :] |= built[:-1, :]
    ring[:-1, :] |= built[1:, :]
    ring[:, 1:] |= built[:, :-1]
    ring[:, :-1] |= built[:, 1:]
    yard = ring & ~built & (classes == 3) & (rng.random((ny, nx)) < 0.55)
    classes[yard] = 1
    classes[built] = 2

    return landcover_from_classes(classes)


def _rasterize_poly(poly, X, Y) -> np.ndarray:
    """Even-odd fill of a polygon over the grid, vectorised by edge."""
    inside = np.zeros(X.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        cond = (y1 > Y) != (y2 > Y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (Y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (X < xint)
    return inside


# -------------------------------------------------------------------- sewer
def _build_sewer(proj: Projection, asset: dict, terrain: Terrain, p: dict,
                 rng) -> SewerNetwork:
    """Sewers along the real streets, with manholes at real intersections.

    Nodes are placed where street centrelines actually cross, plus mid-block
    where a run is long. Conduits follow the street between consecutive nodes
    and are directed downhill, which makes the network a DAG by construction —
    exactly what the storage-routing solver walks.
    """
    W, H = proj.width_m, proj.height_m
    roads = [r for r in asset.get("roads", []) if r.get("class") != "motorway"]
    lines = [proj.line(r["pts"]) for r in roads]

    nodes: list[SewerNode] = []
    node_at: list[list[tuple[float, tuple[float, float], int]]] = [[] for _ in roads]
    seen: dict[tuple[int, int], int] = {}

    def add_node(x: float, y: float) -> int | None:
        if not (0 <= x < W and 0 <= y < H):
            return None
        key = (int(x / 12), int(y / 12))
        if key in seen:
            return seen[key]
        rim = terrain.elevation_at(x, y)
        k = len(nodes)
        nodes.append(SewerNode(
            id=f"mh{k}", x=round(x, 2), y=round(y, 2), kind="manhole",
            system="storm", rim_elev=rim, invert_elev=rim - p["invert_depth"],
            max_inflow=float(rng.uniform(p["catchbasin_capacity_min"],
                                         p["catchbasin_capacity_max"])),
        ))
        seen[key] = k
        return k

    # intersections between every pair of streets
    for ia in range(len(lines)):
        for ib in range(ia + 1, len(lines)):
            for sa in range(len(lines[ia]) - 1):
                a1, a2 = lines[ia][sa], lines[ia][sa + 1]
                for sb in range(len(lines[ib]) - 1):
                    hit = _seg_intersect(a1, a2, lines[ib][sb], lines[ib][sb + 1])
                    if hit is None:
                        continue
                    k = add_node(hit[0], hit[1])
                    if k is None:
                        continue
                    node_at[ia].append((_along(lines[ia], hit), hit, k))
                    node_at[ib].append((_along(lines[ib], hit), hit, k))

    # mid-block nodes so long runs are not one enormous conduit
    for i, line in enumerate(lines):
        for pt, _ in _walk(line, 140.0):
            k = add_node(pt[0], pt[1])
            if k is not None:
                node_at[i].append((_along(line, pt), pt, k))

    if len(nodes) < 8:
        return SewerNetwork(nodes=[], conduits=[])

    # combined-sewer core: the pre-amalgamation lower city
    core = asset.get("combined_sewer_area")
    ext = p["combined_extent"]
    if core and ext > 0:
        poly = proj.line(core)
        cx = sum(q[0] for q in poly) / len(poly)
        cy = sum(q[1] for q in poly) / len(poly)
        scaled = [(cx + (q[0] - cx) * ext, cy + (q[1] - cy) * ext) for q in poly]
        for n in nodes:
            if _point_in_poly(n.x, n.y, scaled):
                n.system = "combined"

    # conduits along each street, ordered by position, directed downhill
    conduits: list[SewerConduit] = []
    edges: set[tuple[int, int]] = set()
    for i, entries in enumerate(node_at):
        ordered = sorted({k: (t, pt) for t, pt, k in entries}.items(),
                         key=lambda kv: kv[1][0])
        for (ka, (_, pa)), (kb, (_, pb)) in zip(ordered[:-1], ordered[1:]):
            if ka == kb:
                continue
            a, b = nodes[ka], nodes[kb]
            # strictly downhill, ties broken by index, so no cycle can form
            if (a.rim_elev, ka) < (b.rim_elev, kb):
                a, b, ka, kb = b, a, kb, ka
            if (ka, kb) in edges:
                continue
            edges.add((ka, kb))
            length = max(math.dist((a.x, a.y), (b.x, b.y)), 1.0)
            slope = max((a.rim_elev - b.rim_elev) / length, p["min_design_slope"])
            conduits.append(SewerConduit(
                id=f"c{len(conduits)}", from_node=a.id, to_node=b.id,
                diameter=0.3, length=round(length, 1), slope=round(slope, 5),
                roughness=float(rng.uniform(0.012, 0.015)),
                system=a.system if a.system == b.system else "combined",
            ))

    _size_pipes(nodes, conduits, p["pipe_diameter_scale"])

    # outfall at the lowest node — the harbour, off the north edge
    low = min(nodes, key=lambda n: n.rim_elev)
    outfall = SewerNode(
        id="outfall", x=low.x, y=min(low.y + 40.0, H - 1), kind="outfall",
        system=low.system, rim_elev=low.rim_elev - 1.5,
        invert_elev=low.invert_elev - 1.0, max_inflow=1e9,
    )
    nodes.append(outfall)
    conduits.append(SewerConduit(
        id=f"c{len(conduits)}", from_node=low.id, to_node="outfall",
        diameter=1.8 * p["pipe_diameter_scale"], length=40.0, slope=0.004,
        system=low.system,
    ))
    return SewerNetwork(nodes=nodes, conduits=conduits)


def _along(line, pt) -> float:
    """Arc length from the start of a polyline to the projection of a point."""
    total = 0.0
    best, best_d = 0.0, float("inf")
    for (x1, y1), (x2, y2) in zip(line[:-1], line[1:]):
        seg = math.dist((x1, y1), (x2, y2))
        if seg < 1e-9:
            continue
        t = max(0.0, min(1.0, ((pt[0] - x1) * (x2 - x1) + (pt[1] - y1) * (y2 - y1)) / seg ** 2))
        px, py = x1 + t * (x2 - x1), y1 + t * (y2 - y1)
        d = math.dist(pt, (px, py))
        if d < best_d:
            best_d, best = d, total + t * seg
        total += seg
    return best


def _size_pipes(nodes, conduits, scale: float) -> None:
    """Diameter from upstream node count, the design-table proxy for area."""
    idx = {n.id: k for k, n in enumerate(nodes)}
    upstream = {n.id: 1 for n in nodes}
    # process high to low so upstream counts accumulate downhill in one pass
    order = sorted(range(len(nodes)), key=lambda k: -nodes[k].rim_elev)
    out_by = {}
    for c in conduits:
        out_by.setdefault(c.from_node, []).append(c)
    for k in order:
        nid = nodes[k].id
        for c in out_by.get(nid, []):
            upstream[c.to_node] = upstream.get(c.to_node, 1) + upstream[nid]
    for c in conduits:
        n_up = upstream.get(c.from_node, 1)
        c.diameter = round(float(np.clip(0.25 + 0.16 * math.sqrt(n_up), 0.25, 1.8) * scale), 3)
    _ = idx


# ------------------------------------------------------------------ features
def _viewer_features(proj: Projection, asset: dict) -> dict:
    """Real-world context geometry, projected into the twin's local metres.

    Deliberately not clipped to the simulation window: streets, the harbour and
    the escarpment continue past the grid so the replay reads as a place inside
    a city rather than a floating rectangle. The viewer decides how far out to
    draw them.
    """
    def line_feat(items, key="pts"):
        out = []
        for it in items:
            if not it.get(key):
                continue
            pts = [[round(a, 1), round(b, 1)] for a, b in proj.line(it[key])]
            out.append({k: v for k, v in it.items() if k not in ("pts", "poly")}
                       | {"pts": pts})
        return out

    def poly_feat(items):
        out = []
        for it in items:
            if not it.get("poly"):
                continue
            pts = [[round(a, 1), round(b, 1)] for a, b in proj.line(it["poly"])]
            out.append({k: v for k, v in it.items() if k not in ("pts", "poly")}
                       | {"poly": pts})
        return out

    water = poly_feat([w for w in asset.get("water", []) if w.get("poly")])
    water += line_feat([w for w in asset.get("water", []) if w.get("pts")])

    labels = []
    for lm in asset.get("landmarks", []):
        x, y = proj.xy(lm["lon"], lm["lat"])
        labels.append({"name": lm["name"], "kind": lm.get("kind", "landmark"),
                       "x": round(x, 1), "y": round(y, 1)})

    return {
        "roads": line_feat(asset.get("roads", [])),
        "rail": line_feat(asset.get("rail", [])),
        "water": water,
        "parks": poly_feat(asset.get("parks", [])),
        "escarpment": line_feat(asset.get("escarpment", [])),
        "labels": labels,
        "extent_m": [round(proj.width_m, 1), round(proj.height_m, 1)],
        "provenance": asset.get("provenance", "hand-digitized"),
    }


# -------------------------------------------------------------------- build
def build_hamilton_twin(place: str = "Hamilton (lower city)", params: dict | None = None,
                        nx: int | None = None, ny: int | None = None,
                        cell_size: float | None = None) -> Twin | None:
    """Build the Hamilton twin, or None if no geometry asset is available."""
    asset = load_asset()
    if not asset or not asset.get("roads"):
        return None

    p = twin_params.resolve(params)
    proj = Projection(asset["sim_bbox"])
    cell = float(cell_size or asset.get("cell_size") or p["cell_size"])
    nx = int(nx or round(proj.width_m / cell))
    ny = int(ny or round(proj.height_m / cell))

    seed = int(hashlib.sha256(f"hamilton:{place}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    terrain = _build_terrain(proj, asset, p, nx, ny, cell, rng)

    if asset.get("buildings"):
        buildings = _buildings_from_osm(proj, asset, terrain, p, rng)
        if len(buildings) < 25:                     # extract too sparse to be useful
            buildings = _generate_stock(proj, asset, terrain, p, rng)
    else:
        buildings = _generate_stock(proj, asset, terrain, p, rng)

    landcover = _build_landcover(proj, asset, terrain, buildings, rng)
    sewer = _build_sewer(proj, asset, terrain, p, rng)

    fp = twin_params.fingerprint(p)
    provenance = asset.get("provenance", "hand-digitized")
    sources = list(asset.get("sources") or [])
    if provenance == "osm":
        sources = ["OpenStreetMap streets, water, parks and building footprints "
                   "(© OpenStreetMap contributors, ODbL)"] + sources
    sources.append("terrain generated against the real escarpment and shoreline "
                   "(Ontario GeoHub LiDAR connector requires GDAL — see "
                   "citysim/twin/connectors/ontario_geohub.py)")
    sources.append("sewer network laid along the real street grid "
                   "(City of Hamilton Open Data connector not active in this runtime)")

    return Twin(
        id=f"twin-{hashlib.sha256(f'hamilton:{place}:{fp}'.encode()).hexdigest()[:10]}",
        name=asset.get("name", "Hamilton, Ontario — lower city"),
        region={"place": place, "bbox": asset["sim_bbox"], "data_bbox": asset.get("bbox"),
                "mode": "hamilton", "provenance": provenance, "seed": seed},
        terrain=terrain,
        buildings=buildings,
        sewer=sewer,
        landcover=landcover,
        params=p,
        features=_viewer_features(proj, asset),
        sources=sources,
    )
