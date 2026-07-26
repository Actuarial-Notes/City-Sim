"""Procedural fallback twin generator (Tier 2, offline mode).

Produces a deterministic, Hamilton-like demo region when live open-data
connectors are unavailable (no network / no GDAL). The geography mirrors the
lower city: the Niagara Escarpment rising to the south, ground sloping north
toward the harbour, an older combined-sewer core in the lowland — the exact
configuration that drives rain-induced sewer backup.

Everything is generated from a seed, so a given region name always yields the
identical twin (twin versioning stays meaningful).
"""

from __future__ import annotations

import hashlib

import numpy as np

from .schema import (
    Building,
    LandCover,
    SewerConduit,
    SewerNetwork,
    SewerNode,
    Terrain,
    Twin,
)

# Material / foundation priors by construction era (plan §5.4: infer from
# age + use when assessment data is unavailable). Loosely follows Ontario
# housing-stock literature: pre-war stock skews masonry on rubble/block
# basements; post-1980 skews wood-frame on poured concrete.
_ERA_MATERIAL = [
    (1950, [("masonry", 0.65), ("wood_frame", 0.35)]),
    (1980, [("masonry", 0.40), ("wood_frame", 0.60)]),
    (9999, [("masonry", 0.20), ("wood_frame", 0.80)]),
]

_REPLACEMENT_COST_PER_M2 = {   # CAD per m² of floor area, rough 2025 replacement costs
    "residential": 2400.0,
    "commercial": 2000.0,
    "industrial": 1500.0,
    "institutional": 2800.0,
}


def _smooth(a: np.ndarray, passes: int = 2) -> np.ndarray:
    """Cheap 3×3 box smoothing without scipy."""
    for _ in range(passes):
        p = np.pad(a, 1, mode="edge")
        a = (
            p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
            p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
            p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
        ) / 9.0
    return a


def generate_synthetic_twin(
    name: str = "Hamilton demo (lower city)",
    nx: int = 150,
    ny: int = 110,
    cell_size: float = 6.0,
    seed: int | None = None,
) -> Twin:
    if seed is None:
        seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------ terrain
    # y index 0 = south (escarpment toe), max = north (harbour edge).
    xs = np.arange(nx) * cell_size
    ys = np.arange(ny) * cell_size
    X, Y = np.meshgrid(xs, ys)
    extent_y = ny * cell_size

    base = 78.0                                     # lake-plain datum, m ASL
    slope_to_bay = (extent_y - Y) * 0.006           # gentle fall to the north
    escarpment = 35.0 / (1.0 + np.exp((Y - 0.12 * extent_y) / (0.035 * extent_y)))
    # A shallow buried-creek valley running north — the classic ponding corridor.
    valley_axis = 0.55 * nx * cell_size + 40.0 * np.sin(Y / extent_y * 2.5)
    valley = -1.8 * np.exp(-((X - valley_axis) ** 2) / (2 * (55.0 ** 2)))
    noise = _smooth(rng.normal(0.0, 0.45, (ny, nx)), passes=3)
    dtm = base + slope_to_bay + escarpment + valley + noise
    terrain = Terrain(dtm=dtm.astype(np.float64), cell_size=cell_size)

    # ------------------------------------------------------------------ street grid
    block_w, block_h = 90.0, 72.0                   # metres between street centrelines
    street_half = 5.0                               # half street width
    street_xs = np.arange(block_w / 2, nx * cell_size, block_w)
    street_ys = np.arange(block_h / 2, ny * cell_size, block_h)

    street_mask = np.zeros((ny, nx), dtype=bool)
    for sx in street_xs:
        j0, j1 = int((sx - street_half) / cell_size), int((sx + street_half) / cell_size) + 1
        street_mask[:, max(j0, 0):min(j1, nx)] = True
    for sy in street_ys:
        i0, i1 = int((sy - street_half) / cell_size), int((sy + street_half) / cell_size) + 1
        street_mask[max(i0, 0):min(i1, ny), :] = True

    # The old combined-sewer core: the low-lying north-central district.
    core_x = (X > 0.30 * nx * cell_size) & (X < 0.72 * nx * cell_size)
    core_y = Y > 0.45 * extent_y
    core_mask = core_x & core_y

    # ------------------------------------------------------------------ buildings
    buildings: list[Building] = []
    bid = 0
    for gy in range(len(street_ys) - 1):
        for gx in range(len(street_xs) - 1):
            # block interior bounds
            x0 = street_xs[gx] + street_half + 3
            x1 = street_xs[gx + 1] - street_half - 3
            y0 = street_ys[gy] + street_half + 3
            y1 = street_ys[gy + 1] - street_half - 3
            if x1 - x0 < 20 or y1 - y0 < 20:
                continue
            cx_blk = (x0 + x1) / 2
            cy_blk = (y0 + y1) / 2
            i_blk = min(int(cy_blk / cell_size), ny - 1)
            j_blk = min(int(cx_blk / cell_size), nx - 1)
            in_core = bool(core_mask[i_blk, j_blk])

            # a park block now and then
            if rng.random() < 0.06:
                continue

            block_use = "residential"
            r = rng.random()
            if not in_core and r < 0.08:
                block_use = "industrial"
            elif r < 0.16:
                block_use = "commercial"

            if block_use == "residential":
                n_lots = int((x1 - x0) // 15)
                for k in range(n_lots):
                    if rng.random() < 0.12:
                        continue
                    lot_x0 = x0 + k * (x1 - x0) / n_lots
                    w = rng.uniform(8.0, 11.0)
                    d = rng.uniform(9.0, 13.0)
                    # two rows: front (south) and back (north) of block
                    for row_y in (y0 + rng.uniform(2, 5), y1 - d - rng.uniform(2, 5)):
                        if rng.random() < 0.25:
                            continue
                        fp = [(lot_x0, row_y), (lot_x0 + w, row_y),
                              (lot_x0 + w, row_y + d), (lot_x0, row_y + d)]
                        cx, cy = lot_x0 + w / 2, row_y + d / 2
                        year = _sample_year(rng, in_core, cy / extent_y)
                        material = _sample_material(rng, year)
                        storeys = int(rng.choice([1, 2, 2, 3], p=[0.3, 0.3, 0.3, 0.1]))
                        is_apartment = rng.random() < 0.05
                        if is_apartment:
                            storeys = int(rng.integers(3, 6))
                        basement = rng.random() < (0.9 if year < 1990 else 0.75)
                        buildings.append(Building(
                            id=f"b{bid}",
                            footprint=fp,
                            centroid=(cx, cy),
                            height=storeys * 3.0 + rng.uniform(0.5, 1.5),
                            storeys=storeys,
                            year_built=year,
                            use="residential",
                            material=material,
                            foundation="basement" if basement else "slab",
                            basement_depth=rng.uniform(1.8, 2.4) if basement else 0.0,
                            ground_elev=terrain.elevation_at(cx, cy),
                            first_floor_height=rng.uniform(0.15, 0.6),
                            dwelling_units=int(rng.integers(6, 20)) if is_apartment else 1,
                            structure_value=0.0,  # filled below
                        ))
                        bid += 1
            else:
                # one or two large slab structures
                for _ in range(int(rng.integers(1, 3))):
                    w = rng.uniform(20.0, min(45.0, x1 - x0 - 2))
                    d = rng.uniform(15.0, min(35.0, y1 - y0 - 2))
                    px = rng.uniform(x0, x1 - w)
                    py = rng.uniform(y0, y1 - d)
                    fp = [(px, py), (px + w, py), (px + w, py + d), (px, py + d)]
                    cx, cy = px + w / 2, py + d / 2
                    year = _sample_year(rng, in_core, cy / extent_y)
                    storeys = int(rng.integers(1, 3))
                    buildings.append(Building(
                        id=f"b{bid}",
                        footprint=fp,
                        centroid=(cx, cy),
                        height=storeys * 4.5,
                        storeys=storeys,
                        year_built=year,
                        use=block_use,
                        material="concrete" if block_use == "industrial" else "masonry",
                        foundation="slab",
                        basement_depth=0.0,
                        ground_elev=terrain.elevation_at(cx, cy),
                        first_floor_height=rng.uniform(0.1, 0.3),
                        dwelling_units=0,
                    ))
                    bid += 1

    # value assignment: floor area × replacement cost; contents ~ 35 % of structure
    for b in buildings:
        area = _poly_area(b.footprint) * max(b.storeys, 1)
        b.structure_value = round(area * _REPLACEMENT_COST_PER_M2[b.use]
                                  * rng.uniform(0.85, 1.15), -2)
        b.contents_value = round(b.structure_value * (0.35 if b.use == "residential" else 0.5), -2)

    # ------------------------------------------------------------------ sewer network
    sewer = _build_sewer(terrain, street_xs, street_ys, core_mask, cell_size, rng)

    # ------------------------------------------------------------------ land cover
    landcover = _build_landcover(terrain, street_mask, buildings, rng)

    twin_id = f"twin-{hashlib.sha256(f'{name}:{seed}'.encode()).hexdigest()[:10]}"
    return Twin(
        id=twin_id,
        name=name,
        region={"place": name, "bbox": None, "seed": seed, "mode": "synthetic"},
        terrain=terrain,
        buildings=buildings,
        sewer=sewer,
        landcover=landcover,
        sources=["synthetic procedural generator (offline fallback; "
                 "see citysim.twin.connectors for live open-data sources)"],
    )


def _sample_year(rng: np.random.Generator, in_core: bool, y_frac: float) -> int:
    """Older stock in the core lowland, newer toward the escarpment."""
    if in_core:
        return int(rng.integers(1890, 1955))
    if y_frac > 0.5:
        return int(rng.integers(1920, 1985))
    return int(rng.integers(1955, 2020))


def _sample_material(rng: np.random.Generator, year: int) -> str:
    for cutoff, dist in _ERA_MATERIAL:
        if year < cutoff:
            mats, probs = zip(*dist)
            return str(rng.choice(mats, p=probs))
    return "wood_frame"


def _poly_area(fp: list[tuple[float, float]]) -> float:
    x = np.array([p[0] for p in fp])
    y = np.array([p[1] for p in fp])
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def _build_sewer(terrain, street_xs, street_ys, core_mask, cell_size, rng) -> SewerNetwork:
    """Manholes at intersections, pipes along streets, flow toward the bay
    (north). Hydraulic attributes are gap-filled the way the plan prescribes:
    slope from the DTM, diameter from upstream drainage area, invert from
    minimum-cover standards."""
    nodes: list[SewerNode] = []
    conduits: list[SewerConduit] = []
    ny, nx = terrain.shape

    grid_ids: dict[tuple[int, int], str] = {}
    for gy, sy in enumerate(street_ys):
        for gx, sx in enumerate(street_xs):
            node_id = f"mh_{gx}_{gy}"
            i = min(int(sy / cell_size), ny - 1)
            j = min(int(sx / cell_size), nx - 1)
            system = "combined" if core_mask[i, j] else "storm"
            rim = terrain.elevation_at(sx, sy)
            nodes.append(SewerNode(
                id=node_id, x=sx, y=sy, kind="manhole", system=system,
                rim_elev=rim, invert_elev=rim - 2.6,
                # one model node stands in for the several street inlets that
                # drain to it, so grate capacity is a multiple of a single CB
                max_inflow=rng.uniform(0.12, 0.22),
            ))
            grid_ids[(gx, gy)] = node_id

    node_map = {n.id: n for n in nodes}

    # Trunk logic: every north-south street drains north; cross links drain
    # toward the trunk column with the largest upstream area (the valley line).
    n_cols = len(street_xs)
    n_rows = len(street_ys)
    upstream_count: dict[str, int] = {n.id: 1 for n in nodes}

    cid = 0
    for gx in range(n_cols):
        for gy in range(n_rows - 1):
            a, b = grid_ids[(gx, gy)], grid_ids[(gx, gy + 1)]
            conduits.append(_make_conduit(f"c{cid}", node_map[a], node_map[b], rng))
            upstream_count[b] += upstream_count[a]
            cid += 1
    # east-west collectors on every second row, draining toward centre column
    centre = n_cols // 2
    for gy in range(0, n_rows, 2):
        for gx in range(n_cols - 1):
            if gx < centre:
                a, b = grid_ids[(gx, gy)], grid_ids[(gx + 1, gy)]
            else:
                a, b = grid_ids[(gx + 1, gy)], grid_ids[(gx, gy)]
            conduits.append(_make_conduit(f"c{cid}", node_map[a], node_map[b], rng))
            cid += 1

    # size pipes from upstream count (proxy for drained area) — design-table style
    for c in conduits:
        n_up = upstream_count.get(c.from_node, 1)
        c.diameter = float(np.clip(0.25 + 0.16 * np.sqrt(n_up), 0.25, 1.8))

    # outfall at the north end of the centre (valley) column
    out_node = node_map[grid_ids[(centre, n_rows - 1)]]
    outfall = SewerNode(
        id="outfall", x=out_node.x, y=out_node.y + 40.0, kind="outfall",
        system="combined", rim_elev=out_node.rim_elev - 1.5,
        invert_elev=out_node.invert_elev - 1.0, max_inflow=1e9,
    )
    nodes.append(outfall)
    conduits.append(SewerConduit(
        id=f"c{cid}", from_node=out_node.id, to_node="outfall",
        diameter=1.8, length=40.0, slope=0.004, system=out_node.system,
    ))

    return SewerNetwork(nodes=nodes, conduits=conduits)


def _make_conduit(cid: str, a: SewerNode, b: SewerNode, rng) -> SewerConduit:
    length = float(np.hypot(a.x - b.x, a.y - b.y))
    ground_slope = (a.rim_elev - b.rim_elev) / max(length, 1.0)
    slope = float(max(ground_slope, 0.0015))          # min design slope
    system = a.system if a.system == b.system else "combined"
    return SewerConduit(
        id=cid, from_node=a.id, to_node=b.id,
        diameter=0.3, length=length, slope=slope,
        roughness=float(rng.uniform(0.012, 0.015)), system=system,
    )


def _build_landcover(terrain, street_mask, buildings, rng) -> LandCover:
    ny, nx = terrain.shape
    cell = terrain.cell_size
    classes = np.full((ny, nx), 3, dtype=np.int8)          # default grass
    classes[street_mask] = 1                               # paved

    tree_noise = _smooth(rng.random((ny, nx)), passes=2)
    classes[(classes == 3) & (tree_noise > 0.62)] = 4      # tree pockets

    for b in buildings:
        xs = [p[0] for p in b.footprint]
        ys_ = [p[1] for p in b.footprint]
        j0, j1 = int(min(xs) / cell), int(np.ceil(max(xs) / cell))
        i0, i1 = int(min(ys_) / cell), int(np.ceil(max(ys_) / cell))
        classes[max(i0, 0):min(i1, ny), max(j0, 0):min(j1, nx)] = 2

    manning = np.choose(classes, [0.03, 0.013, 0.02, 0.05, 0.10, 0.035]).astype(np.float32)
    imperv = np.choose(classes, [1.0, 0.95, 0.98, 0.05, 0.02, 0.15]).astype(np.float32)
    # Hamilton lowland soils are clay-heavy: modest infiltration capacity.
    infil = np.choose(classes, [0.0, 0.5, 0.0, 9.0, 14.0, 6.0]).astype(np.float32)

    return LandCover(classes=classes, manning_n=manning,
                     imperviousness=imperv, infiltration=infil)
