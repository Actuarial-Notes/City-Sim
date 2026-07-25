"""City of Hamilton Open Data connector (plan §3.3, Tier A).

Hamilton serves its layers over ArcGIS Hub / FeatureServer REST — queryable as
GeoJSON without any key. The layers this platform cares about:

  * Sewer Main   — sanitary / storm / combined / forcemain pipe geometry+type,
                   the layer that identifies combined-sewer (backup-prone) areas
  * Manholes / catchbasins — the 1D↔2D coupling points
  * Building footprints / parcels / address points

The *Enhanced Water and Sewer Data* (pipe diameter, slope, invert, install
date) needs a McMaster Library Data Use Agreement and is the one gated
accuracy upgrade; where absent we gap-fill hydraulics per plan §3.3.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

HUB_SEARCH = "https://open.hamilton.ca/api/feed/dcat-us/1.1.json"


class HamiltonOpenData:
    """Generic ArcGIS FeatureServer GeoJSON query helper."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def query_layer(self, layer_url: str, bbox_lonlat: list[float] | None = None,
                    where: str = "1=1", max_records: int = 5000) -> list[dict] | None:
        """Query a FeatureServer layer → GeoJSON features, or None on failure."""
        params = {
            "where": where, "outFields": "*", "f": "geojson",
            "resultRecordCount": max_records, "outSR": 4326,
        }
        if bbox_lonlat:
            w, s, e, n = bbox_lonlat
            params.update({
                "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            })
        url = f"{layer_url}/query?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return json.loads(resp.read()).get("features", [])
        except Exception:
            return None
