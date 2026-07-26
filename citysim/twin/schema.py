"""The digital-twin schema (Tier 2).

Design guardrail (plan §9): the twin stores *physical attributes*, never hazard
interpretations. A building records "masonry, 2 storeys, built 1935, basement
2.1 m deep" — it does not record "flood vulnerability class 4". Each hazard
module derives its own interpretation from these attributes.

All geometry lives in a local metric CRS: x metres east / y metres north of the
twin origin. Raster layers share one grid (``Terrain.dtm`` shape, ``cell_size``
metres per cell, row 0 = southernmost row).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np


@dataclass
class Terrain:
    """Ground surface — the substrate every hazard runs on."""

    dtm: np.ndarray            # (ny, nx) bare-earth elevation, metres
    cell_size: float           # metres per cell
    origin: tuple[float, float] = (0.0, 0.0)   # world coords of cell (0, 0) centre
    dsm: Optional[np.ndarray] = None           # surface model (buildings + canopy), if known

    @property
    def shape(self) -> tuple[int, int]:
        return self.dtm.shape

    def elevation_at(self, x: float, y: float) -> float:
        """Sample the DTM at a metric coordinate (nearest cell)."""
        j = int(round((x - self.origin[0]) / self.cell_size))
        i = int(round((y - self.origin[1]) / self.cell_size))
        ny, nx = self.dtm.shape
        return float(self.dtm[min(max(i, 0), ny - 1), min(max(j, 0), nx - 1)])


@dataclass
class Building:
    id: str
    footprint: list[tuple[float, float]]   # polygon vertices, metric coords
    centroid: tuple[float, float]
    height: float                          # metres, roof above ground (DSM−DTM or levels×3)
    storeys: int
    year_built: int
    use: str                               # residential | commercial | industrial | institutional
    material: str                          # masonry | wood_frame | concrete | steel
    foundation: str                        # basement | crawlspace | slab
    basement_depth: float                  # metres below grade (0 if no basement)
    ground_elev: float                     # DTM at centroid, metres
    first_floor_height: float = 0.3        # metres above grade
    dwelling_units: int = 1
    structure_value: float = 0.0           # replacement cost, CAD
    contents_value: float = 0.0            # CAD

    @property
    def has_basement(self) -> bool:
        return self.foundation == "basement" and self.basement_depth > 0

    @property
    def basement_floor_elev(self) -> float:
        return self.ground_elev - self.basement_depth


@dataclass
class SewerNode:
    id: str
    x: float
    y: float
    kind: str                  # manhole | catchbasin | outfall
    system: str                # combined | storm | sanitary
    rim_elev: float            # ground/rim elevation (sampled from DTM)
    invert_elev: float         # pipe invert at node
    max_inflow: float = 0.05   # m³/s inlet capacity (catchbasin grate limit)


@dataclass
class SewerConduit:
    id: str
    from_node: str
    to_node: str
    diameter: float            # metres
    length: float              # metres
    slope: float               # m/m (gap-filled from DTM where unknown)
    roughness: float = 0.013   # Manning n, concrete default
    system: str = "combined"


@dataclass
class SewerNetwork:
    nodes: list[SewerNode] = field(default_factory=list)
    conduits: list[SewerConduit] = field(default_factory=list)

    def node_by_id(self) -> dict[str, SewerNode]:
        return {n.id: n for n in self.nodes}


@dataclass
class LandCover:
    """Per-cell surface properties on the terrain grid."""

    classes: np.ndarray        # (ny, nx) int class codes
    manning_n: np.ndarray      # (ny, nx) surface roughness
    imperviousness: np.ndarray # (ny, nx) 0..1 fraction
    infiltration: np.ndarray   # (ny, nx) steady infiltration capacity, mm/h

    CLASS_NAMES = {0: "water", 1: "paved", 2: "building", 3: "grass", 4: "trees", 5: "bare_soil"}


@dataclass
class Twin:
    """One versioned, reusable digital twin of a region."""

    id: str
    name: str
    region: dict               # {"place": ..., "bbox": [w, s, e, n] (lon/lat)} — provenance
    terrain: Terrain
    buildings: list[Building]
    sewer: SewerNetwork
    landcover: LandCover
    version: int = 1
    sources: list[str] = field(default_factory=list)   # data-source provenance strings
    # resolved twin-generation parameters (citysim.twin.params) — recorded so
    # the UI can show what this twin was actually built from, not just what was
    # requested
    params: dict = field(default_factory=dict)
    # optional real-world geometry (streets, water, parks, landmarks) when the
    # twin was built from a baked extract rather than generated
    features: dict = field(default_factory=dict)

    @property
    def n_households(self) -> int:
        return sum(b.dwelling_units for b in self.buildings if b.use == "residential")

    def exposure(self) -> dict:
        """Total value at risk, by use — what the economics tab reports back."""
        by_use: dict[str, dict] = {}
        for b in self.buildings:
            e = by_use.setdefault(b.use, {"count": 0, "structure": 0.0, "contents": 0.0})
            e["count"] += 1
            e["structure"] += b.structure_value
            e["contents"] += b.contents_value
        for e in by_use.values():
            e["structure"] = round(e["structure"], 2)
            e["contents"] = round(e["contents"], 2)
        return by_use

    def stock_profile(self) -> dict:
        """Distributions of the building stock — what the Setup histograms plot.

        Computed from the twin itself rather than from the parameters, so the
        Setup screen shows what was actually generated instead of what was asked
        for. The two differ whenever a knob interacts with the geometry.
        """
        decades: dict[str, int] = {}
        materials: dict[str, int] = {}
        storeys: dict[str, int] = {}
        uses: dict[str, int] = {}
        basements = 0
        for b in self.buildings:
            decades[str(b.year_built // 10 * 10)] = decades.get(str(b.year_built // 10 * 10), 0) + 1
            materials[b.material] = materials.get(b.material, 0) + 1
            storeys[str(b.storeys)] = storeys.get(str(b.storeys), 0) + 1
            uses[b.use] = uses.get(b.use, 0) + 1
            if b.has_basement:
                basements += 1
        n = max(len(self.buildings), 1)
        return {
            "decades": dict(sorted(decades.items())),
            "materials": materials,
            "storeys": dict(sorted(storeys.items(), key=lambda kv: int(kv[0]))),
            "uses": uses,
            "basement_share": round(basements / n, 3),
            "median_year": int(sorted(b.year_built for b in self.buildings)[len(self.buildings) // 2])
            if self.buildings else 0,
        }

    def summary(self) -> dict:
        ny, nx = self.terrain.shape
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "grid": {"nx": nx, "ny": ny, "cell_size": self.terrain.cell_size,
                     "extent_m": [nx * self.terrain.cell_size, ny * self.terrain.cell_size]},
            "buildings": len(self.buildings),
            "households": self.n_households,
            "sewer_nodes": len(self.sewer.nodes),
            "sewer_conduits": len(self.sewer.conduits),
            "combined_sewer_nodes": sum(1 for n in self.sewer.nodes if n.system == "combined"),
            "elevation_range": [round(float(self.terrain.dtm.min()), 1),
                                round(float(self.terrain.dtm.max()), 1)],
            "exposure": self.exposure(),
            "stock": self.stock_profile(),
            "params": self.params,
            "mode": (self.region or {}).get("mode", "synthetic"),
            "sources": self.sources,
        }


def building_to_dict(b: Building) -> dict:
    return asdict(b)


def building_from_dict(d: dict) -> Building:
    d = dict(d)
    d["footprint"] = [tuple(p) for p in d["footprint"]]
    d["centroid"] = tuple(d["centroid"])
    return Building(**d)
