"""Twin persistence: one directory per twin, rasters as compressed .npz,
vectors/attributes as JSON. Twins are versioned and reusable — every hazard
module loads the same stored twin."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .schema import (
    Building,
    LandCover,
    SewerConduit,
    SewerNetwork,
    SewerNode,
    Terrain,
    Twin,
    building_from_dict,
)


class TwinStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, twin_id: str) -> Path:
        return self.root / twin_id

    def save(self, twin: Twin) -> Path:
        d = self.path(twin.id)
        d.mkdir(parents=True, exist_ok=True)

        rasters = {
            "dtm": twin.terrain.dtm,
            "lc_classes": twin.landcover.classes,
            "lc_manning": twin.landcover.manning_n,
            "lc_imperv": twin.landcover.imperviousness,
            "lc_infil": twin.landcover.infiltration,
        }
        if twin.terrain.dsm is not None:
            rasters["dsm"] = twin.terrain.dsm
        np.savez_compressed(d / "rasters.npz", **rasters)

        meta = {
            "id": twin.id,
            "name": twin.name,
            "region": twin.region,
            "version": twin.version,
            "sources": twin.sources,
            "params": twin.params,
            "features": twin.features,
            "cell_size": twin.terrain.cell_size,
            "origin": list(twin.terrain.origin),
            "buildings": [asdict(b) for b in twin.buildings],
            "sewer": {
                "nodes": [asdict(n) for n in twin.sewer.nodes],
                "conduits": [asdict(c) for c in twin.sewer.conduits],
            },
        }
        (d / "twin.json").write_text(json.dumps(meta))
        return d

    def load(self, twin_id: str) -> Twin:
        d = self.path(twin_id)
        meta = json.loads((d / "twin.json").read_text())
        rasters = np.load(d / "rasters.npz")

        terrain = Terrain(
            dtm=rasters["dtm"],
            cell_size=float(meta["cell_size"]),
            origin=tuple(meta["origin"]),
            dsm=rasters["dsm"] if "dsm" in rasters.files else None,
        )
        landcover = LandCover(
            classes=rasters["lc_classes"],
            manning_n=rasters["lc_manning"],
            imperviousness=rasters["lc_imperv"],
            infiltration=rasters["lc_infil"],
        )
        sewer = SewerNetwork(
            nodes=[SewerNode(**n) for n in meta["sewer"]["nodes"]],
            conduits=[SewerConduit(**c) for c in meta["sewer"]["conduits"]],
        )
        buildings = [building_from_dict(b) for b in meta["buildings"]]
        return Twin(
            id=meta["id"], name=meta["name"], region=meta["region"],
            terrain=terrain, buildings=buildings, sewer=sewer,
            landcover=landcover, version=meta["version"], sources=meta["sources"],
            # twins stored before V2 have neither key
            params=meta.get("params", {}), features=meta.get("features", {}),
        )

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            f = d / "twin.json"
            if f.exists():
                meta = json.loads(f.read_text())
                out.append({"id": meta["id"], "name": meta["name"],
                            "version": meta["version"], "region": meta["region"],
                            "buildings": len(meta["buildings"])})
        return out

    def exists(self, twin_id: str) -> bool:
        return (self.path(twin_id) / "twin.json").exists()
