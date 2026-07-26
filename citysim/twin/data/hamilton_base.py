"""Hand-digitized georeferenced base map of Hamilton's lower city.

This is the *fallback* geometry: it ships in the repository so the default twin
is recognizably Hamilton with no network access at all. It is approximate —
street centrelines, the escarpment brow and the harbour shoreline were placed
from map knowledge, not surveyed, and there are no individual building
footprints.

``scripts/bake_hamilton.py`` fetches the real thing from OpenStreetMap and
writes ``hamilton_lower_city.json.gz`` beside this file; when that asset is
present, ``citysim.twin.hamilton`` prefers it and this module is unused. The
GitHub Pages workflow runs the bake, so the hosted demo is on surveyed
geometry.

Coordinates are WGS84 lon/lat. Everything else in the twin is metric-local; the
projection happens once in ``citysim.twin.hamilton``.

Geography being represented, north to south: Hamilton Harbour (Burlington Bay),
the industrial north end along Burlington Street, the old lower-city grid
(Barton, Cannon, King, Main), and the Niagara Escarpment rising along the
southern edge. Water drains north to the harbour, and the pre-amalgamation core
between roughly Bay Street and Sherman Avenue is on combined sewers — which is
exactly the configuration that produces basement backup in heavy rain.
"""

from __future__ import annotations

# ------------------------------------------------------------------- extents
# Wide extent: everything shipped to the viewer for context, well beyond the
# simulated window, so the surrounding city still reads as Hamilton.
DATA_BBOX = [-79.9000, 43.2380, -79.7980, 43.2780]

# Simulated window: downtown, Beasley and Landsdale — the combined-sewer core,
# from the escarpment toe north to the Burlington Street industrial flats.
SIM_BBOX = [-79.8720, 43.2452, -79.8500, 43.2668]
CELL_SIZE = 10.0

# Hamilton's lower-city grid parallels the escarpment, trending slightly north
# of east. Applied to the straight street definitions below so the grid sits at
# the right angle instead of true cardinal.
_TREND = 0.035          # degrees latitude gained per degree of longitude east
_TREND_ORIGIN = -79.868  # James Street, where the anchor latitudes are measured


def _ew(lat_at_james: float, lon0: float, lon1: float, n: int = 9):
    """An east–west street as a polyline, with the grid's trend applied."""
    pts = []
    for k in range(n):
        lon = lon0 + (lon1 - lon0) * k / (n - 1)
        pts.append([round(lon, 6),
                    round(lat_at_james + (lon - _TREND_ORIGIN) * _TREND, 6)])
    return pts


def _ns(lon_at_king: float, lat0: float, lat1: float, n: int = 7):
    """A north–south street, perpendicular to the trended grid."""
    pts = []
    for k in range(n):
        lat = lat0 + (lat1 - lat0) * k / (n - 1)
        # perpendicular to a +_TREND slope, scaled by the lat/lon metre ratio
        pts.append([round(lon_at_king - (lat - 43.2538) * _TREND * 1.88, 6),
                    round(lat, 6)])
    return pts


# ------------------------------------------------------------------- streets
# (name, class, anchor, west/south end, east/north end)
# class drives rendered width and, for the sewer layout, which streets carry
# trunk sewers.
_EW_STREETS = [
    ("Burlington Street", "primary",     43.2660, -79.8800, -79.7990),
    ("Barton Street",     "secondary",   43.2600, -79.8960, -79.8020),
    ("Cannon Street",     "secondary",   43.2568, -79.8930, -79.8050),
    ("Wilson Street",     "residential", 43.2556, -79.8700, -79.8300),
    ("King William Street", "residential", 43.2548, -79.8720, -79.8380),
    ("King Street",       "primary",     43.2538, -79.8960, -79.8030),
    ("Main Street",       "primary",     43.2512, -79.8990, -79.8040),
    ("Jackson Street",    "residential", 43.2496, -79.8790, -79.8560),
    ("Hunter Street",     "secondary",   43.2479, -79.8850, -79.8480),
    ("Forest Avenue",     "residential", 43.2464, -79.8800, -79.8500),
    ("Charlton Avenue",   "secondary",   43.2447, -79.8900, -79.8300),
    ("Aberdeen Avenue",   "secondary",   43.2432, -79.8990, -79.8720),
    ("Herkimer Street",   "residential", 43.2441, -79.8930, -79.8730),
    ("Stuart Street",     "residential", 43.2668, -79.8830, -79.8600),
    ("Strachan Street",   "residential", 43.2690, -79.8790, -79.8580),
]

_NS_STREETS = [
    ("Dundurn Street",   "secondary",   -79.8945, 43.2430, 43.2640),
    ("Locke Street",     "residential", -79.8880, 43.2420, 43.2620),
    ("Queen Street",     "secondary",   -79.8815, 43.2420, 43.2660),
    ("Hess Street",      "residential", -79.8790, 43.2440, 43.2640),
    ("Caroline Street",  "residential", -79.8760, 43.2440, 43.2650),
    ("Bay Street",       "secondary",   -79.8735, 43.2440, 43.2690),
    ("MacNab Street",    "residential", -79.8705, 43.2450, 43.2700),
    ("James Street",     "primary",     -79.8685, 43.2400, 43.2720),
    ("John Street",      "secondary",   -79.8655, 43.2410, 43.2700),
    ("Catharine Street", "residential", -79.8630, 43.2460, 43.2660),
    ("Mary Street",      "residential", -79.8610, 43.2470, 43.2670),
    ("Ferguson Avenue",  "residential", -79.8590, 43.2450, 43.2670),
    ("Wellington Street", "secondary",  -79.8555, 43.2430, 43.2660),
    ("Victoria Avenue",  "secondary",   -79.8520, 43.2430, 43.2660),
    ("Wentworth Street", "secondary",   -79.8470, 43.2430, 43.2650),
    ("Sherman Avenue",   "secondary",   -79.8390, 43.2420, 43.2650),
    ("Gage Avenue",      "secondary",   -79.8290, 43.2410, 43.2640),
    ("Ottawa Street",    "secondary",   -79.8190, 43.2400, 43.2630),
    ("Kenilworth Avenue", "secondary",  -79.8090, 43.2400, 43.2620),
]

# Streets that do not follow the grid.
_DIAGONALS = [
    ("York Boulevard", "primary", [
        [-79.8690, 43.2585], [-79.8760, 43.2618], [-79.8850, 43.2645],
        [-79.8940, 43.2660], [-79.9000, 43.2668]]),
    ("Cannon Street West", "secondary", [
        [-79.8700, 43.2570], [-79.8800, 43.2578], [-79.8900, 43.2588]]),
    ("Claremont Access", "primary", [
        [-79.8600, 43.2445], [-79.8570, 43.2415], [-79.8545, 43.2388]]),
    ("Jolley Cut", "secondary", [
        [-79.8690, 43.2440], [-79.8665, 43.2412], [-79.8650, 43.2385]]),
    ("Highway 403", "motorway", [
        [-79.8990, 43.2450], [-79.9000, 43.2530], [-79.8960, 43.2610],
        [-79.8890, 43.2660]]),
    ("Nikola Tesla Boulevard", "motorway", [
        [-79.8300, 43.2690], [-79.8150, 43.2705], [-79.8000, 43.2710]]),
]

def _fill_minor(anchors: list[float], target_m: float, per_deg: float) -> list[float]:
    """Positions for the unnamed residential streets between two arterials.

    Only the arterials above are real. A city is mostly the minor streets in
    between, and without them the generated blocks come out three times too big,
    which would put a third of the real building density on the map. These fill
    each gap back to Hamilton's actual block spacing. They carry no names — the
    OSM bake replaces the whole set with surveyed geometry, names included.
    """
    out: list[float] = []
    for lo, hi in zip(anchors[:-1], anchors[1:]):
        gap_m = abs(hi - lo) * per_deg
        n_insert = int(round(gap_m / target_m)) - 1
        for k in range(1, max(n_insert, 0) + 1):
            out.append(lo + (hi - lo) * k / (n_insert + 1))
    return out


_LON_PER_DEG = 81_050.0      # metres per degree longitude at 43.26° N
_LAT_PER_DEG = 111_320.0

# Hamilton's lower-city blocks run roughly 90 m north–south by 100 m east–west.
_MINOR_EW = _fill_minor(sorted(lat for _, _, lat, _, _ in _EW_STREETS
                               if 43.2440 <= lat <= 43.2610), 90.0, _LAT_PER_DEG)
_MINOR_NS = _fill_minor(sorted(lon for _, _, lon, _, _ in _NS_STREETS
                               if -79.8800 <= lon <= -79.8460), 100.0, _LON_PER_DEG)

ROADS: list[dict] = (
    [{"name": n, "class": c, "pts": _ew(lat, w, e)} for n, c, lat, w, e in _EW_STREETS]
    + [{"name": n, "class": c, "pts": _ns(lon, s, nn)} for n, c, lon, s, nn in _NS_STREETS]
    + [{"name": n, "class": c, "pts": pts} for n, c, pts in _DIAGONALS]
    + [{"name": "", "class": "residential", "pts": _ew(lat, -79.8830, -79.8420)}
       for lat in _MINOR_EW]
    + [{"name": "", "class": "residential", "pts": _ns(lon, 43.2440, 43.2680)}
       for lon in _MINOR_NS]
)

# -------------------------------------------------------------------- rail
RAIL: list[dict] = [
    {"name": "CN Grimsby Subdivision", "pts": [
        [-79.8900, 43.2672], [-79.8750, 43.2668], [-79.8600, 43.2672],
        [-79.8400, 43.2685], [-79.8150, 43.2700], [-79.7990, 43.2706]]},
    {"name": "CP Hamilton Subdivision", "pts": [
        [-79.8960, 43.2612], [-79.8700, 43.2625], [-79.8400, 43.2645],
        [-79.8100, 43.2662]]},
]

# -------------------------------------------------------------------- water
# Hamilton Harbour (Burlington Bay) to the north — the outlet every storm sewer
# in the lower city eventually drains to.
WATER: list[dict] = [
    {"name": "Hamilton Harbour", "kind": "bay", "poly": [
        [-79.9000, 43.2740], [-79.8880, 43.2722], [-79.8790, 43.2712],
        [-79.8700, 43.2716], [-79.8600, 43.2726], [-79.8480, 43.2734],
        [-79.8330, 43.2726], [-79.8180, 43.2730], [-79.8060, 43.2742],
        [-79.7980, 43.2752], [-79.7980, 43.2780], [-79.9000, 43.2780]]},
    {"name": "Chedoke Creek", "kind": "creek", "pts": [
        [-79.8960, 43.2436], [-79.8975, 43.2500], [-79.8960, 43.2570],
        [-79.8920, 43.2630], [-79.8880, 43.2690], [-79.8860, 43.2720]]},
    {"name": "Red Hill Creek", "kind": "creek", "pts": [
        [-79.8020, 43.2400], [-79.8035, 43.2480], [-79.8060, 43.2560],
        [-79.8080, 43.2640], [-79.8085, 43.2712]]},
    {"name": "Cootes Paradise", "kind": "bay", "poly": [
        [-79.9000, 43.2700], [-79.8950, 43.2712], [-79.8930, 43.2740],
        [-79.9000, 43.2748]]},
]

# --------------------------------------------------------------------- parks
PARKS: list[dict] = [
    {"name": "Gage Park", "poly": [
        [-79.8320, 43.2400], [-79.8230, 43.2404], [-79.8235, 43.2452],
        [-79.8325, 43.2448]]},
    {"name": "Bayfront Park", "poly": [
        [-79.8840, 43.2690], [-79.8760, 43.2696], [-79.8756, 43.2722],
        [-79.8846, 43.2718]]},
    {"name": "Pier 4 Park", "poly": [
        [-79.8740, 43.2706], [-79.8690, 43.2710], [-79.8688, 43.2728],
        [-79.8738, 43.2726]]},
    {"name": "Dundurn Park", "poly": [
        [-79.8960, 43.2668], [-79.8890, 43.2672], [-79.8886, 43.2704],
        [-79.8956, 43.2700]]},
    {"name": "Victoria Park", "poly": [
        [-79.8800, 43.2568], [-79.8752, 43.2572], [-79.8749, 43.2596],
        [-79.8797, 43.2592]]},
    {"name": "Woodlands Park", "poly": [
        [-79.8480, 43.2596], [-79.8432, 43.2600], [-79.8429, 43.2624],
        [-79.8477, 43.2620]]},
    {"name": "Beasley Park", "poly": [
        [-79.8646, 43.2580], [-79.8614, 43.2583], [-79.8612, 43.2599],
        [-79.8644, 43.2596]]},
    {"name": "Central Park", "poly": [
        [-79.8760, 43.2500], [-79.8726, 43.2503], [-79.8724, 43.2518],
        [-79.8758, 43.2515]]},
    {"name": "Scott Park", "poly": [
        [-79.8290, 43.2530], [-79.8240, 43.2534], [-79.8237, 43.2558],
        [-79.8287, 43.2554]]},
]

# -------------------------------------------------------------- escarpment
# The brow of the Niagara Escarpment. Runoff from above arrives fast and
# concentrated at the toe, which is why the southern streets flood the way they
# do.
ESCARPMENT: list[dict] = [
    {"name": "Niagara Escarpment", "pts": [
        [-79.9000, 43.2470], [-79.8940, 43.2440], [-79.8860, 43.2420],
        [-79.8760, 43.2408], [-79.8660, 43.2400], [-79.8540, 43.2396],
        [-79.8400, 43.2394], [-79.8240, 43.2392], [-79.8080, 43.2388],
        [-79.7980, 43.2384]]},
]

# ------------------------------------------------------------------ landmarks
LANDMARKS: list[dict] = [
    {"name": "Hamilton Harbour", "lon": -79.8640, "lat": 43.2752, "kind": "water"},
    {"name": "Niagara Escarpment", "lon": -79.8600, "lat": 43.2392, "kind": "landform"},
    {"name": "Downtown", "lon": -79.8690, "lat": 43.2555, "kind": "district"},
    {"name": "Beasley", "lon": -79.8630, "lat": 43.2582, "kind": "district"},
    {"name": "Landsdale", "lon": -79.8520, "lat": 43.2585, "kind": "district"},
    {"name": "North End", "lon": -79.8700, "lat": 43.2685, "kind": "district"},
    {"name": "Corktown", "lon": -79.8640, "lat": 43.2478, "kind": "district"},
    {"name": "Gage Park", "lon": -79.8276, "lat": 43.2426, "kind": "park"},
    {"name": "Bayfront Park", "lon": -79.8800, "lat": 43.2706, "kind": "park"},
    {"name": "Dundurn Castle", "lon": -79.8922, "lat": 43.2686, "kind": "landmark"},
    {"name": "Hamilton GO Centre", "lon": -79.8690, "lat": 43.2482, "kind": "landmark"},
    {"name": "West Harbour", "lon": -79.8760, "lat": 43.2698, "kind": "landmark"},
    {"name": "Barton Village", "lon": -79.8470, "lat": 43.2602, "kind": "district"},
    {"name": "Industrial north end", "lon": -79.8200, "lat": 43.2678, "kind": "district"},
]

# Land use beyond the default residential fabric: the north-end industrial belt
# and the downtown commercial core, so generated stock lands in the right places.
LANDUSE: list[dict] = [
    {"kind": "industrial", "poly": [
        [-79.8800, 43.2618], [-79.7990, 43.2648], [-79.7990, 43.2726],
        [-79.8800, 43.2700]]},
    {"kind": "commercial", "poly": [
        [-79.8760, 43.2508], [-79.8600, 43.2516], [-79.8604, 43.2570],
        [-79.8764, 43.2562]]},
    {"kind": "commercial", "poly": [
        [-79.8560, 43.2588], [-79.8300, 43.2600], [-79.8302, 43.2616],
        [-79.8562, 43.2604]]},
]

# The pre-amalgamation core carried on combined sewers — one pipe for storm and
# sanitary flow, which is the mechanism behind basement backup.
COMBINED_SEWER_AREA: list[list[float]] = [
    [-79.8790, 43.2500], [-79.8420, 43.2520], [-79.8410, 43.2660],
    [-79.8780, 43.2648],
]

PROVENANCE = {
    "provenance": "hand-digitized",
    "note": "Street centrelines, the escarpment brow and the harbour shoreline were "
            "placed from map knowledge rather than surveyed data, and there are no "
            "individual building footprints — the stock is generated along the real "
            "street grid. Run scripts/bake_hamilton.py to replace this with "
            "OpenStreetMap geometry.",
    "sources": ["hand-digitized approximation of Hamilton, Ontario (lower city)"],
}


def base_asset() -> dict:
    """The fallback asset, in the same shape ``bake_hamilton.py`` writes."""
    return {
        "name": "Hamilton, Ontario — lower city",
        "bbox": DATA_BBOX,
        "sim_bbox": SIM_BBOX,
        "cell_size": CELL_SIZE,
        "roads": ROADS,
        "rail": RAIL,
        "water": WATER,
        "parks": PARKS,
        "escarpment": ESCARPMENT,
        "landmarks": LANDMARKS,
        "landuse": LANDUSE,
        "combined_sewer_area": COMBINED_SEWER_AREA,
        "buildings": [],
        **PROVENANCE,
    }
