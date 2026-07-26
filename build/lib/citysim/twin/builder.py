"""Twin builder (Tier 2 orchestrator): place name → digital twin.

Modes:
  * ``synthetic`` — fully procedural deterministic twin (always works, offline).
  * ``auto``      — geocode the place and pull real OSM building footprints,
                    laying them over procedurally generated terrain + sewer
                    (a *hybrid* twin). Any network failure falls back to
                    synthetic — the platform never dead-ends on data access.

Full live terrain/sewer ingest (Ontario GeoHub LiDAR, Hamilton sewer layers)
plugs in through ``citysim.twin.connectors`` when GDAL-class dependencies are
present; see each connector's docstring.
"""

from __future__ import annotations

import hashlib

import numpy as np

from .schema import Building, Twin
from .synthetic import generate_synthetic_twin, _sample_material, _poly_area
from . import connectors


def build_twin(place: str = "Hamilton demo (lower city)", mode: str = "synthetic",
               nx: int = 150, ny: int = 110, cell_size: float = 6.0) -> Twin:
    twin = generate_synthetic_twin(name=place, nx=nx, ny=ny, cell_size=cell_size)

    if mode == "auto":
        hybrid = _try_osm_overlay(twin, place)
        if hybrid is not None:
            return hybrid
        twin.sources.append("live OSM fetch failed or empty — using procedural fallback")
    return twin


def _try_osm_overlay(twin: Twin, place: str) -> Twin | None:
    """Replace procedural buildings with real OSM footprints when reachable."""
    geo = connectors.geocode(place)
    if geo is None:
        return None

    # Work over a viewport centred on the geocode, matching the twin extent.
    ext_x = twin.terrain.shape[1] * twin.terrain.cell_size
    ext_y = twin.terrain.shape[0] * twin.terrain.cell_size
    import math
    m_lat = 111_320.0
    m_lon = m_lat * math.cos(math.radians(geo["lat"]))
    half_lon = (ext_x / 2) / m_lon
    half_lat = (ext_y / 2) / m_lat
    bbox = [geo["lon"] - half_lon, geo["lat"] - half_lat,
            geo["lon"] + half_lon, geo["lat"] + half_lat]

    feats = connectors.fetch_osm_buildings(bbox)
    if not feats:
        return None

    rng = np.random.default_rng(int(hashlib.sha256(place.encode()).hexdigest()[:8], 16))
    buildings: list[Building] = []
    origin_lon, origin_lat = bbox[0], bbox[1]
    for i, f in enumerate(feats):
        fp = [connectors.osm.lonlat_to_local(lon, lat, origin_lon, origin_lat)
              for lon, lat in f["footprint_lonlat"]]
        xs = [p[0] for p in fp]
        ys = [p[1] for p in fp]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        if not (0 <= cx < ext_x and 0 <= cy < ext_y):
            continue
        area = _poly_area(fp)
        if area < 20:
            continue
        tags = f["tags"]
        use = _use_from_tags(tags)
        storeys = int(tags.get("building:levels", "0") or 0) or int(rng.choice([1, 2, 2, 3]))
        try:
            height = float(str(tags.get("height", "")).replace(" m", ""))
        except ValueError:
            height = 0.0
        height = height or storeys * 3.0 + 1.0
        year = int(rng.integers(1900, 2015))
        basement = use == "residential" and rng.random() < 0.85
        b = Building(
            id=f"b{i}", footprint=fp, centroid=(cx, cy), height=height,
            storeys=storeys, year_built=year, use=use,
            material=_sample_material(rng, year),
            foundation="basement" if basement else "slab",
            basement_depth=float(rng.uniform(1.8, 2.4)) if basement else 0.0,
            ground_elev=twin.terrain.elevation_at(cx, cy),
            first_floor_height=float(rng.uniform(0.15, 0.6)),
            dwelling_units=1 if use == "residential" else 0,
        )
        from .synthetic import _REPLACEMENT_COST_PER_M2
        b.structure_value = round(area * max(storeys, 1)
                                  * _REPLACEMENT_COST_PER_M2[use] * rng.uniform(0.85, 1.15), -2)
        b.contents_value = round(b.structure_value * 0.35, -2)
        buildings.append(b)

    if len(buildings) < 10:
        return None

    twin.buildings = buildings
    _restamp_building_cells(twin)
    twin.name = geo["display_name"].split(",")[0] + " (hybrid)"
    twin.region = {"place": place, "bbox": bbox, "mode": "hybrid",
                   "geocode": geo["display_name"]}
    twin.sources = [
        "OpenStreetMap footprints via Overpass API (ODbL)",
        "procedural terrain/sewer/landcover (LiDAR + sewer connectors not active in this runtime)",
    ]
    twin.id = f"twin-{hashlib.sha256(('osm:' + place).encode()).hexdigest()[:10]}"
    return twin


def _use_from_tags(tags: dict) -> str:
    v = tags.get("building", "yes")
    if v in ("house", "residential", "apartments", "detached", "semidetached_house",
             "terrace", "bungalow", "yes"):
        return "residential"
    if v in ("industrial", "warehouse"):
        return "industrial"
    if v in ("school", "hospital", "church", "civic", "university"):
        return "institutional"
    return "commercial"


def _restamp_building_cells(twin: Twin) -> None:
    """Re-derive the land-cover 'building' class after replacing footprints."""
    lc = twin.landcover
    cell = twin.terrain.cell_size
    ny, nx = twin.terrain.shape
    lc.classes[lc.classes == 2] = 3
    for b in twin.buildings:
        xs = [p[0] for p in b.footprint]
        ys = [p[1] for p in b.footprint]
        j0, j1 = int(min(xs) / cell), int(np.ceil(max(xs) / cell))
        i0, i1 = int(min(ys) / cell), int(np.ceil(max(ys) / cell))
        lc.classes[max(i0, 0):min(i1, ny), max(j0, 0):min(j1, nx)] = 2
    lc.manning_n = np.choose(lc.classes, [0.03, 0.013, 0.02, 0.05, 0.10, 0.035]).astype(np.float32)
    lc.imperviousness = np.choose(lc.classes, [1.0, 0.95, 0.98, 0.05, 0.02, 0.15]).astype(np.float32)
    lc.infiltration = np.choose(lc.classes, [0.0, 0.5, 0.0, 9.0, 14.0, 6.0]).astype(np.float32)
