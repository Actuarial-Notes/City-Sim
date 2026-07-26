#!/usr/bin/env python3
"""Fetch Hamilton's real geometry from OpenStreetMap into a committed asset.

``citysim/twin/hamilton.py`` prefers ``data/hamilton_lower_city.json.gz`` when it
exists and falls back to the hand-digitized base in ``data/hamilton_base.py``
otherwise. This script produces the former: real street centrelines and names,
building footprints with storeys and use, the harbour shoreline, parks, rail
corridors, land use and the escarpment.

    python scripts/bake_hamilton.py

Run it once and commit the result, or let CI run it — the GitHub Pages workflow
does, before baking the static site, so the hosted demo is on surveyed geometry.
Any network failure leaves the existing asset (or the fallback) in place rather
than writing a partial file: a twin built from half a street grid is worse than
one built from an honest approximation.

Overpass is a shared free service. This makes a handful of queries with a long
timeout and a descriptive User-Agent, which is what its usage policy asks for.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from citysim.twin.data.hamilton_base import (       # noqa: E402
    DATA_BBOX, SIM_BBOX, CELL_SIZE, LANDMARKS, COMBINED_SEWER_AREA,
)

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "CitySim/0.2 (environmental digital twin; +https://github.com/Actuarial-Notes/City-Sim)"

# Road classes we keep, mapped onto the twin's own vocabulary.
ROAD_CLASS = {
    "motorway": "motorway", "motorway_link": "motorway", "trunk": "motorway",
    "trunk_link": "motorway", "primary": "primary", "primary_link": "primary",
    "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "secondary", "tertiary_link": "secondary",
    "residential": "residential", "unclassified": "residential",
    "living_street": "residential", "service": "service",
}

BUILDING_USE = {
    "house": "residential", "residential": "residential", "apartments": "residential",
    "detached": "residential", "semidetached_house": "residential",
    "terrace": "residential", "bungalow": "residential", "dormitory": "residential",
    "industrial": "industrial", "warehouse": "industrial", "factory": "industrial",
    "school": "institutional", "hospital": "institutional", "church": "institutional",
    "civic": "institutional", "university": "institutional", "college": "institutional",
    "public": "institutional", "government": "institutional",
    "retail": "commercial", "commercial": "commercial", "office": "commercial",
    "supermarket": "commercial", "hotel": "commercial",
}


def query(ql: str, timeout: int = 180) -> dict:
    """POST an Overpass QL query, trying each mirror in turn."""
    body = urllib.parse.urlencode({"data": ql}).encode()
    last = None
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return json.loads(fh.read().decode())
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            last = exc
            print(f"   {url} failed ({exc}); trying next mirror")
            time.sleep(2)
    raise RuntimeError(f"every Overpass mirror failed: {last}")


def bbox_clause(bbox: list[float]) -> str:
    w, s, e, n = bbox
    return f"({s},{w},{n},{e})"


def _ways(elements: list[dict]) -> list[dict]:
    return [e for e in elements if e.get("type") == "way" and e.get("geometry")]


def _pts(el: dict, precision: int = 6) -> list[list[float]]:
    return [[round(g["lon"], precision), round(g["lat"], precision)]
            for g in el["geometry"]]


def simplify(pts: list[list[float]], tol_m: float) -> list[list[float]]:
    """Ramer–Douglas–Peucker, with the tolerance given in metres.

    Overpass returns full-resolution geometry; a street centreline with a vertex
    every two metres is far more than a 10 m grid or an isometric view can use,
    and the asset ships to the browser.
    """
    if len(pts) < 3:
        return pts
    lat0 = pts[0][1]
    mx = 111_320.0 * math.cos(math.radians(lat0))
    my = 111_320.0

    def rdp(seq: list[list[float]]) -> list[list[float]]:
        if len(seq) < 3:
            return seq
        (x1, y1), (x2, y2) = (seq[0][0] * mx, seq[0][1] * my), (seq[-1][0] * mx, seq[-1][1] * my)
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        worst, wi = -1.0, 0
        for i in range(1, len(seq) - 1):
            px, py = seq[i][0] * mx, seq[i][1] * my
            d = (abs(dy * px - dx * py + x2 * y1 - y2 * x1) / L if L > 1e-9
                 else math.hypot(px - x1, py - y1))
            if d > worst:
                worst, wi = d, i
        if worst <= tol_m:
            return [seq[0], seq[-1]]
        return rdp(seq[:wi + 1])[:-1] + rdp(seq[wi:])

    sys.setrecursionlimit(10000)
    return rdp(pts)


def fetch_roads(bbox: str) -> tuple[list[dict], list[dict]]:
    print("── streets and rail")
    data = query(f"""[out:json][timeout:170];
      (
        way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|service)$"]{bbox};
        way["railway"~"^(rail|light_rail)$"]{bbox};
      );
      out geom;""")
    roads, rail = [], []
    for el in _ways(data.get("elements", [])):
        tags = el.get("tags", {})
        pts = simplify(_pts(el), 6.0)
        if len(pts) < 2:
            continue
        if "railway" in tags:
            rail.append({"name": tags.get("name", ""), "pts": pts})
        else:
            cls = ROAD_CLASS.get(tags.get("highway"))
            if not cls:
                continue
            if cls == "service" and tags.get("service") in ("parking_aisle", "driveway"):
                continue
            roads.append({"name": tags.get("name", ""), "class": cls, "pts": pts})
    print(f"   {len(roads)} street segments, {len(rail)} rail")
    return roads, rail


def fetch_water(bbox: str) -> list[dict]:
    print("── water")
    data = query(f"""[out:json][timeout:170];
      (
        way["natural"="water"]{bbox};
        way["waterway"="riverbank"]{bbox};
        way["waterway"~"^(river|stream)$"]{bbox};
        relation["natural"="water"]{bbox};
      );
      out geom;""")
    out = []
    for el in _ways(data.get("elements", [])):
        tags = el.get("tags", {})
        pts = simplify(_pts(el), 10.0)
        if len(pts) < 2:
            continue
        name = tags.get("name", "")
        if tags.get("waterway") in ("river", "stream"):
            out.append({"name": name, "kind": "creek", "pts": pts})
        elif len(pts) >= 3:
            out.append({"name": name, "kind": "bay", "poly": pts})
    print(f"   {len(out)} water features")
    return out


def fetch_areas(bbox: str) -> tuple[list[dict], list[dict], list[dict]]:
    print("── parks, land use and the escarpment")
    data = query(f"""[out:json][timeout:170];
      (
        way["leisure"~"^(park|garden|recreation_ground)$"]{bbox};
        way["landuse"~"^(industrial|commercial|retail|railway)$"]{bbox};
        way["natural"="cliff"]{bbox};
      );
      out geom;""")
    parks, landuse, escarpment = [], [], []
    for el in _ways(data.get("elements", [])):
        tags = el.get("tags", {})
        pts = simplify(_pts(el), 8.0)
        if "leisure" in tags and len(pts) >= 3:
            parks.append({"name": tags.get("name", ""), "poly": pts})
        elif "landuse" in tags and len(pts) >= 3:
            lu = tags["landuse"]
            landuse.append({"kind": "industrial" if lu in ("industrial", "railway")
                            else "commercial", "poly": pts})
        elif tags.get("natural") == "cliff" and len(pts) >= 2:
            escarpment.append({"name": tags.get("name", "Niagara Escarpment"), "pts": pts})
    print(f"   {len(parks)} parks, {len(landuse)} land-use areas, "
          f"{len(escarpment)} cliff lines")
    return parks, landuse, escarpment


def fetch_buildings(bbox: str) -> list[dict]:
    """Footprints for the simulated window only — the wide extent would be tens
    of thousands of polygons, and the twin only models the window."""
    print("── building footprints (simulated window)")
    data = query(f"""[out:json][timeout:170];
      way["building"]{bbox};
      out geom;""")
    out = []
    for el in _ways(data.get("elements", [])):
        tags = el.get("tags", {})
        pts = simplify(_pts(el), 1.5)
        if len(pts) < 4:
            continue
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        try:
            storeys = int(float(tags.get("building:levels", 0) or 0))
        except ValueError:
            storeys = 0
        try:
            height = float(str(tags.get("height", "")).replace(" m", "") or 0)
        except ValueError:
            height = 0.0
        try:
            units = int(float(tags.get("building:flats", 0) or 0))
        except ValueError:
            units = 0
        try:
            year = int(str(tags.get("start_date", ""))[:4])
        except ValueError:
            year = 0
        out.append({
            "poly": pts,
            "use": BUILDING_USE.get(tags.get("building"), None)
                   or BUILDING_USE.get(tags.get("amenity"), None) or "residential",
            "storeys": storeys, "height": round(height, 1),
            "units": units, "year": year,
            "name": tags.get("name", ""),
        })
    print(f"   {len(out)} footprints")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1]
                                        / "citysim" / "twin" / "data"))
    ap.add_argument("--allow-failure", action="store_true",
                    help="exit 0 when the fetch fails (for CI, where the "
                         "committed fallback is an acceptable outcome)")
    args = ap.parse_args()

    wide = bbox_clause(DATA_BBOX)
    sim = bbox_clause(SIM_BBOX)

    t0 = time.time()
    try:
        roads, rail = fetch_roads(wide)
        water = fetch_water(wide)
        parks, landuse, escarpment = fetch_areas(wide)
        buildings = fetch_buildings(sim)
    except Exception as exc:                       # noqa: BLE001 — any failure is the same
        print(f"\n!! OSM fetch failed: {exc}")
        print("   leaving the existing asset in place; the twin falls back to "
              "citysim/twin/data/hamilton_base.py")
        raise SystemExit(0 if args.allow_failure else 1)

    if len(roads) < 50 or len(buildings) < 200:
        print(f"\n!! extract looks too sparse ({len(roads)} roads, "
              f"{len(buildings)} buildings) — refusing to overwrite the asset")
        raise SystemExit(0 if args.allow_failure else 1)

    asset = {
        "name": "Hamilton, Ontario — lower city",
        "bbox": DATA_BBOX,
        "sim_bbox": SIM_BBOX,
        "cell_size": CELL_SIZE,
        "provenance": "osm",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "roads": roads,
        "rail": rail,
        "water": water,
        "parks": parks,
        "landuse": landuse,
        "escarpment": escarpment,
        "buildings": buildings,
        # Landmark labels and the combined-sewer extent are not in OSM — the
        # first is editorial, the second is a municipal fact the sewer connector
        # would supply. Both carry over from the curated base.
        "landmarks": LANDMARKS,
        "combined_sewer_area": COMBINED_SEWER_AREA,
        "sources": [
            "OpenStreetMap streets, water, parks, land use and building "
            "footprints (© OpenStreetMap contributors, ODbL)",
        ],
        "note": "Building age, material, foundation, basement depth and value are "
                "inferred from era priors — OpenStreetMap does not carry them.",
    }

    out = Path(args.out) / "hamilton_lower_city.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(asset, separators=(",", ":")).encode()
    out.write_bytes(gzip.compress(blob, 9))
    print(f"\n── {out}  ({out.stat().st_size/1e6:.2f} MB gzipped, "
          f"{len(blob)/1e6:.1f} MB raw, {time.time()-t0:.0f}s)")
    print("   rebuild the twin to pick it up:  python scripts/demo.py --runs 8")


if __name__ == "__main__":
    main()
