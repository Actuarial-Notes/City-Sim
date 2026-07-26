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

    @property
    def n_households(self) -> int:
        return sum(b.dwelling_units for b in self.buildings if b.use == "residential")

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
            "sources": self.sources,
        }


def building_to_dict(b: Building) -> dict:
    return asdict(b)


def building_from_dict(d: dict) -> Building:
    d = dict(d)
    d["footprint"] = [tuple(p) for p in d["footprint"]]
    d["centroid"] = tuple(d["centroid"])
    return Building(**d)
