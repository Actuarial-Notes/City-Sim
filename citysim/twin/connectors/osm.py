"""OpenStreetMap connector: Nominatim geocoding + Overpass building footprints.

Used for footprints and use-type only (plan §3.2): OSM height/material tags are
too sparse to rely on, so heights come from DSM−DTM or era lookups downstream.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_UA = {"User-Agent": "citysim-digital-twin/0.1 (open-data research)"}


def _get(url: str, data: bytes | None = None, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, data=data, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def geocode(place: str, timeout: float = 15.0) -> dict | None:
    """Place name → {lat, lon, bbox [w, s, e, n], display_name} or None."""
    qs = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    try:
        rows = json.loads(_get(f"{NOMINATIM_URL}?{qs}", timeout=timeout))
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    bb = r["boundingbox"]  # [s, n, w, e]
    return {
        "lat": float(r["lat"]), "lon": float(r["lon"]),
        "bbox": [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])],
        "display_name": r["display_name"],
    }


def fetch_osm_buildings(bbox: list[float], timeout: float = 45.0,
                        max_buildings: int = 4000) -> list[dict] | None:
    """Overpass query for building footprints in bbox [w, s, e, n].

    Returns [{"footprint_lonlat": [(lon, lat), ...], "tags": {...}}, ...]
    or None on any failure (caller falls back).
    """
    w, s, e, n = bbox
    q = f'[out:json][timeout:{int(timeout)}];way["building"]({s},{w},{n},{e});out geom {max_buildings};'
    try:
        payload = _get(OVERPASS_URL, data=urllib.parse.urlencode({"data": q}).encode(),
                       timeout=timeout + 10)
        elements = json.loads(payload).get("elements", [])
    except Exception:
        return None
    out = []
    for el in elements:
        geom = el.get("geometry")
        if not geom or len(geom) < 4:
            continue
        out.append({
            "footprint_lonlat": [(p["lon"], p["lat"]) for p in geom],
            "tags": el.get("tags", {}),
        })
    return out


def lonlat_to_local(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    """Equirectangular projection to local metres — adequate at city scale."""
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(origin_lat))
    return ((lon - origin_lon) * m_per_deg_lon, (lat - origin_lat) * m_per_deg_lat)
