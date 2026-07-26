"""The 3D scene payload for the viewer.

Shared by the live API (`GET /api/twin/{id}/scene`) and the static export, for
the same reason as `citysim.results.pack`: the viewer has one decode path, so
there must be one encoder. Physical attributes only — no hazard
interpretation — per the Tier-2 contract.
"""

from __future__ import annotations

import numpy as np

from .schema import Twin


def scene_payload(twin: Twin) -> dict:
    t = twin.terrain
    return {
        "summary": twin.summary(),
        "cell_size": t.cell_size,
        "shape": list(t.shape),
        "dtm": np.round(t.dtm, 2).flatten().tolist(),
        "landcover": twin.landcover.classes.flatten().astype(int).tolist(),
        # Every physical attribute the twin holds, not the subset the old viewer
        # happened to display. The map inspector shows foundation, basement
        # depth and first-floor height alongside the rest, and the renderer needs
        # centroid and footprint area for roof geometry and label placement.
        "buildings": [{
            "id": b.id, "footprint": b.footprint, "height": round(b.height, 1),
            "ground_elev": round(b.ground_elev, 2), "use": b.use,
            "material": b.material, "year_built": b.year_built,
            "storeys": b.storeys, "has_basement": b.has_basement,
            "foundation": b.foundation,
            "basement_depth": round(b.basement_depth, 2),
            "first_floor_height": round(b.first_floor_height, 2),
            "dwelling_units": b.dwelling_units,
            "structure_value": b.structure_value,
            "contents_value": b.contents_value,
            "centroid": [round(b.centroid[0], 1), round(b.centroid[1], 1)],
        } for b in twin.buildings],
        "sewer": {
            "nodes": [{"id": n.id, "x": n.x, "y": n.y, "kind": n.kind,
                       "system": n.system, "rim_elev": round(n.rim_elev, 2),
                       "invert_elev": round(n.invert_elev, 2)}
                      for n in twin.sewer.nodes],
            "conduits": [{"from": c.from_node, "to": c.to_node,
                          "diameter": round(c.diameter, 3), "system": c.system}
                         for c in twin.sewer.conduits],
        },
        # Real-world context — streets, water, parks, rail, landmark labels —
        # when the twin was built from a geographic extract. Empty for the
        # procedural twin, and the viewer simply draws nothing.
        "features": twin.features or {},
        "landcover_classes": {str(k): v for k, v in
                              twin.landcover.CLASS_NAMES.items()},
    }
