"""Twin-generation parameters (Tier 2) — the "character creation" knobs.

The counterpart to ``citysim.hazards.flood.assumptions``: that module holds
what the *hazard* believes, this one holds what the *twin* is made of. The
split matters because these two things are adjusted at different moments —
twin parameters are baked in when the twin is built and cached, hazard
assumptions are applied per ensemble against an existing twin.

Same contract: ``PARAMS`` is a declarative spec the Setup UI renders itself
from, ``resolve()`` produces the validated flat dict the generators read, and
``fingerprint()`` folds the resolved values into the twin id so two different
parameter sets can never collide in the twin cache.

Region and terrain parameters are marked ``readonly``: they describe the
geography being modelled rather than a modelling choice, so the UI shows them
in full but does not offer to change them.
"""

from __future__ import annotations

import hashlib
import json

PARAMS: list[dict] = [
    # --------------------------------------------------------------- region
    {"id": "nx", "group": "region", "label": "Grid columns", "type": "int",
     "default": 150, "min": 60, "max": 400, "step": 10, "unit": "cells",
     "readonly": True, "description": "East–west extent of the simulation grid."},
    {"id": "ny", "group": "region", "label": "Grid rows", "type": "int",
     "default": 110, "min": 60, "max": 400, "step": 10, "unit": "cells",
     "readonly": True, "description": "South–north extent, escarpment toe to harbour."},
    {"id": "cell_size", "group": "region", "label": "Cell size", "type": "float",
     "default": 6.0, "min": 2.0, "max": 20.0, "step": 0.5, "unit": "m",
     "readonly": True,
     "description": "Resolution of the terrain, land-cover and flow grids. Six metres is "
                    "city-block scale — fine enough to route water down a street, too "
                    "coarse to resolve an individual driveway."},
    {"id": "base_elev", "group": "region", "label": "Lake-plain datum", "type": "float",
     "default": 78.0, "min": 60.0, "max": 100.0, "step": 0.5, "unit": "m ASL",
     "readonly": True,
     "description": "Baseline ground elevation of the lower city above sea level. Lake "
                    "Ontario sits near 75 m."},
    {"id": "slope_to_bay", "group": "region", "label": "Slope to the harbour", "type": "float",
     "default": 0.006, "min": 0.0, "max": 0.05, "step": 0.001, "unit": "m/m",
     "readonly": True,
     "description": "Gentle northward fall from the escarpment toe to Hamilton Harbour."},
    {"id": "escarpment_height", "group": "region", "label": "Escarpment rise", "type": "float",
     "default": 35.0, "min": 0.0, "max": 100.0, "step": 1.0, "unit": "m",
     "readonly": True,
     "description": "Height of the Niagara Escarpment above the lower city. Runoff from "
                    "the brow arrives fast and concentrated."},
    {"id": "valley_depth", "group": "region", "label": "Buried-creek valley depth",
     "type": "float", "default": 1.8, "min": 0.0, "max": 8.0, "step": 0.1, "unit": "m",
     "readonly": True,
     "description": "Depth of the filled creek corridor running north. Buried watercourses "
                    "are the classic urban ponding corridor — the water remembers."},

    # ------------------------------------------------------- building stock
    {"id": "basement_rate_pre1990", "group": "building_stock",
     "label": "Basement rate — built before 1990", "type": "float",
     "default": 0.90, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
     "description": "Share of older homes with a full basement. Basements are where "
                    "sewer-backup damage happens, so this is one of the highest-leverage "
                    "numbers in the whole model.",
     "source": "housing_stock"},
    {"id": "basement_rate_post1990", "group": "building_stock",
     "label": "Basement rate — built 1990 or later", "type": "float",
     "default": 0.75, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
     "description": "Newer stock includes more slab-on-grade construction.",
     "source": "housing_stock"},
    {"id": "basement_depth_min", "group": "building_stock", "label": "Basement depth — shallowest",
     "type": "float", "default": 1.8, "min": 1.0, "max": 3.0, "step": 0.1, "unit": "m",
     "description": "Depth of the basement floor below grade. Deeper basements sit further "
                    "below the sewer hydraulic grade line and flood sooner.",
     "pair": "basement_depth_max", "source": "housing_stock"},
    {"id": "basement_depth_max", "group": "building_stock", "label": "Basement depth — deepest",
     "type": "float", "default": 2.4, "min": 1.0, "max": 4.0, "step": 0.1, "unit": "m",
     "description": "Upper end of the sampled basement depth.", "source": "housing_stock"},
    {"id": "first_floor_height_min", "group": "building_stock",
     "label": "First-floor height — lowest", "type": "float",
     "default": 0.15, "min": 0.0, "max": 1.5, "step": 0.05, "unit": "m",
     "description": "Height of the main floor above grade. Overland water has to exceed "
                    "this before it reaches the occupied storey.",
     "pair": "first_floor_height_max", "source": "housing_stock"},
    {"id": "first_floor_height_max", "group": "building_stock",
     "label": "First-floor height — highest", "type": "float",
     "default": 0.60, "min": 0.0, "max": 2.0, "step": 0.05, "unit": "m",
     "description": "Upper end of the sampled first-floor height.", "source": "housing_stock"},
    {"id": "apartment_share", "group": "building_stock", "label": "Multi-unit share",
     "type": "float", "default": 0.05, "min": 0.0, "max": 0.40, "step": 0.01, "unit": "frac",
     "description": "Fraction of residential buildings that are 3–5 storey walk-ups with "
                    "6–20 units. They concentrate households, so they move the "
                    "per-household figure.",
     "source": "housing_stock"},
    {"id": "masonry_share_prewar", "group": "building_stock",
     "label": "Masonry share — pre-1950", "type": "float",
     "default": 0.65, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
     "description": "Pre-war stock skews masonry on rubble or block basements, which take "
                    "slightly less damage per metre of water than wood frame.",
     "source": "housing_stock"},
    {"id": "masonry_share_midcentury", "group": "building_stock",
     "label": "Masonry share — 1950 to 1980", "type": "float",
     "default": 0.40, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
     "description": "Post-war construction shifts toward wood frame.",
     "source": "housing_stock"},
    {"id": "masonry_share_modern", "group": "building_stock",
     "label": "Masonry share — after 1980", "type": "float",
     "default": 0.20, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
     "description": "Modern stock is predominantly wood frame on poured concrete.",
     "source": "housing_stock"},
    {"id": "storey_weight_1", "group": "building_stock", "label": "Single-storey weight",
     "type": "float", "default": 0.30, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "",
     "description": "Relative frequency of 1-storey homes. The three storey weights are "
                    "normalised, so only their ratio matters.",
     "source": "housing_stock"},
    {"id": "storey_weight_2", "group": "building_stock", "label": "Two-storey weight",
     "type": "float", "default": 0.60, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "",
     "description": "Relative frequency of 2-storey homes.", "source": "housing_stock"},
    {"id": "storey_weight_3", "group": "building_stock", "label": "Three-storey weight",
     "type": "float", "default": 0.10, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "",
     "description": "Relative frequency of 3-storey homes.", "source": "housing_stock"},
    {"id": "commercial_block_share", "group": "building_stock", "label": "Commercial blocks",
     "type": "float", "default": 0.08, "min": 0.0, "max": 0.5, "step": 0.01, "unit": "frac",
     "description": "Fraction of city blocks given over to commercial structures.",
     "source": "housing_stock"},
    {"id": "industrial_block_share", "group": "building_stock", "label": "Industrial blocks",
     "type": "float", "default": 0.08, "min": 0.0, "max": 0.5, "step": 0.01, "unit": "frac",
     "description": "Fraction of blocks given over to industrial structures. Hamilton's "
                    "north end is heavily industrial.",
     "source": "housing_stock"},
    {"id": "park_block_share", "group": "building_stock", "label": "Park blocks",
     "type": "float", "default": 0.06, "min": 0.0, "max": 0.5, "step": 0.01, "unit": "frac",
     "description": "Fraction of blocks left undeveloped. Parks are pervious, so they both "
                    "absorb rain and provide somewhere for it to pond harmlessly.",
     "source": "housing_stock"},

    # ---------------------------------------------------------------- sewer
    {"id": "combined_extent", "group": "sewer", "label": "Combined-sewer extent",
     "type": "float", "default": 1.0, "min": 0.0, "max": 1.6, "step": 0.05, "unit": "×",
     "description": "Scales the old combined-sewer core. Only combined areas can produce "
                    "sewer backup, so this decides how much of the city is exposed to the "
                    "dominant damage mechanism. 0 makes the network fully separated.",
     "source": "hamilton_sewer"},
    {"id": "pipe_diameter_scale", "group": "sewer", "label": "Pipe capacity scaling",
     "type": "float", "default": 1.0, "min": 0.5, "max": 2.0, "step": 0.05, "unit": "×",
     "description": "Scales every conduit diameter. Capacity grows faster than diameter "
                    "(roughly D^8/3), so a 1.2× diameter is closer to 1.6× flow.",
     "source": "hamilton_sewer"},
    {"id": "min_design_slope", "group": "sewer", "label": "Minimum design slope",
     "type": "float", "default": 0.0015, "min": 0.0002, "max": 0.01, "step": 0.0001,
     "unit": "m/m",
     "description": "Floor on conduit slope where the ground is flat — the standard "
                    "self-cleansing minimum.",
     "source": "hamilton_sewer"},
    {"id": "invert_depth", "group": "sewer", "label": "Pipe invert depth",
     "type": "float", "default": 2.6, "min": 1.5, "max": 5.0, "step": 0.1, "unit": "m",
     "description": "Depth of the sewer invert below the rim. Together with basement depth "
                    "this sets how much head has to build before backup reaches a floor.",
     "source": "hamilton_sewer"},
    {"id": "catchbasin_capacity_min", "group": "sewer", "label": "Inlet capacity — lowest",
     "type": "float", "default": 0.12, "min": 0.01, "max": 1.0, "step": 0.01, "unit": "m³/s",
     "description": "How fast street inlets can take water off the surface. One model node "
                    "stands in for the several inlets draining to it.",
     "pair": "catchbasin_capacity_max", "source": "hamilton_sewer"},
    {"id": "catchbasin_capacity_max", "group": "sewer", "label": "Inlet capacity — highest",
     "type": "float", "default": 0.22, "min": 0.01, "max": 2.0, "step": 0.01, "unit": "m³/s",
     "description": "Upper end of the sampled inlet capacity.", "source": "hamilton_sewer"},

    # ------------------------------------------------------------ economics
    {"id": "cost_index", "group": "economics", "label": "Construction cost index",
     "type": "float", "default": 1.0, "min": 0.5, "max": 2.0, "step": 0.05, "unit": "×",
     "description": "Scales every replacement cost at once — the quickest way to test how "
                    "much the headline number depends on construction pricing.",
     "source": "replacement_cost"},
    {"id": "cost_residential", "group": "economics", "label": "Residential replacement cost",
     "type": "float", "default": 2400.0, "min": 800.0, "max": 6000.0, "step": 50.0,
     "unit": "$/m²", "description": "Replacement cost per square metre of floor area.",
     "source": "replacement_cost"},
    {"id": "cost_commercial", "group": "economics", "label": "Commercial replacement cost",
     "type": "float", "default": 2000.0, "min": 500.0, "max": 6000.0, "step": 50.0,
     "unit": "$/m²", "description": "Replacement cost per square metre of floor area.",
     "source": "replacement_cost"},
    {"id": "cost_industrial", "group": "economics", "label": "Industrial replacement cost",
     "type": "float", "default": 1500.0, "min": 300.0, "max": 5000.0, "step": 50.0,
     "unit": "$/m²", "description": "Replacement cost per square metre of floor area.",
     "source": "replacement_cost"},
    {"id": "cost_institutional", "group": "economics", "label": "Institutional replacement cost",
     "type": "float", "default": 2800.0, "min": 800.0, "max": 8000.0, "step": 50.0,
     "unit": "$/m²", "description": "Replacement cost per square metre of floor area.",
     "source": "replacement_cost"},
    {"id": "contents_ratio_residential", "group": "economics", "label": "Contents ratio — homes",
     "type": "float", "default": 0.35, "min": 0.0, "max": 1.5, "step": 0.05, "unit": "×",
     "description": "Contents value as a multiple of structure value. Contents drive most "
                    "of the loss in a shallow basement flood — the furnace, the water "
                    "heater, and everything stored on the floor.",
     "source": "replacement_cost"},
    {"id": "contents_ratio_other", "group": "economics",
     "label": "Contents ratio — non-residential", "type": "float",
     "default": 0.50, "min": 0.0, "max": 3.0, "step": 0.05, "unit": "×",
     "description": "Commercial and industrial buildings carry proportionally more contents "
                    "and stock.",
     "source": "replacement_cost"},
    {"id": "value_dispersion", "group": "economics", "label": "Value dispersion",
     "type": "float", "default": 0.15, "min": 0.0, "max": 0.6, "step": 0.01, "unit": "±frac",
     "description": "Random spread applied to each building's value, so identical footprints "
                    "are not identically valued.",
     "source": "replacement_cost"},
]

DEFAULTS: dict = {p["id"]: p["default"] for p in PARAMS}
_BY_ID: dict = {p["id"]: p for p in PARAMS}

GROUPS: list[dict] = [
    {"id": "region", "label": "Region & terrain", "readonly": True,
     "blurb": "The geography being modelled: the escarpment to the south, the harbour to "
              "the north, and a buried creek in between. Shown in full, not adjustable — "
              "this is what Hamilton is, not a modelling choice."},
    {"id": "building_stock", "label": "Building stock",
     "blurb": "What the city is built of, and when. Age drives material, material drives "
              "the damage curve, and basements decide whether sewer backup can reach you."},
    {"id": "sewer", "label": "Sewer network",
     "blurb": "The buried half of the problem. Combined sewers carry storm and sanitary "
              "flow in one pipe, which is why heavy rain can push water back into a "
              "basement."},
    {"id": "economics", "label": "Economics",
     "blurb": "What it costs to put things back. Every dollar figure the model reports "
              "traces to these numbers."},
]

SOURCES: list[dict] = [
    {"id": "housing_stock", "title": "Ontario housing-stock age, material and foundation "
                                     "distributions",
     "publisher": "Statistics Canada Census / CMHC housing profiles", "year": "2021",
     "url": "https://www.statcan.gc.ca/", "fidelity": "shaped-after",
     "informs": "era, material, storey and basement priors",
     "note": "Applied as priors over generated stock, not matched to individual parcels."},
    {"id": "hamilton_sewer", "title": "City of Hamilton Open Data — sewer and manhole layers",
     "publisher": "City of Hamilton", "year": "2024", "url": "https://open.hamilton.ca/",
     "fidelity": "approximated", "informs": "network layout, pipe sizing and inlet capacity"},
    {"id": "replacement_cost",
     "title": "Residential and commercial replacement cost benchmarks (Ontario, 2025)",
     "publisher": "construction cost guides", "year": "2025", "url": "",
     "fidelity": "approximated", "informs": "replacement cost per m² and contents ratios"},
]


def _coerce(spec: dict, value):
    if spec["type"] == "bool":
        return bool(value)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return spec["default"]
    if v != v:
        return spec["default"]
    v = min(max(v, spec["min"]), spec["max"])
    return int(round(v)) if spec["type"] == "int" else v


def resolve(params: dict | None = None) -> dict:
    """Defaults overlaid with validated overrides.

    ``readonly`` parameters ignore any supplied value: they are published so the
    UI can display the geography, not so a client can redefine it.
    """
    params = params or {}
    out = dict(DEFAULTS)
    for key, spec in _BY_ID.items():
        if spec.get("readonly"):
            continue
        if key in params:
            out[key] = _coerce(spec, params[key])

    for spec in PARAMS:
        hi = spec.get("pair")
        if hi and out[spec["id"]] > out[hi]:
            out[spec["id"]], out[hi] = out[hi], out[spec["id"]]

    # storey weights are ratios; a user who zeroes all three would otherwise
    # hand numpy an invalid probability vector
    if out["storey_weight_1"] + out["storey_weight_2"] + out["storey_weight_3"] <= 0:
        for k in ("storey_weight_1", "storey_weight_2", "storey_weight_3"):
            out[k] = DEFAULTS[k]

    return out


def fingerprint(resolved: dict) -> str:
    """Short stable hash of the non-default parameters.

    Folded into the twin id so two parameter sets never share a cache entry.
    Defaults hash to an empty string, which keeps the default twin's id
    unchanged from V1 — existing stored twins stay addressable.
    """
    diff = {k: v for k, v in sorted(resolved.items()) if v != DEFAULTS.get(k)}
    if not diff:
        return ""
    blob = json.dumps(diff, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def deviations(resolved: dict) -> list[dict]:
    out = []
    for spec in PARAMS:
        v = resolved.get(spec["id"], spec["default"])
        if v != spec["default"]:
            out.append({"id": spec["id"], "label": spec["label"], "group": spec["group"],
                        "unit": spec.get("unit", ""), "default": spec["default"], "value": v})
    return out


def spec() -> dict:
    """The payload the Setup screen renders the twin tabs from."""
    return {"params": PARAMS, "defaults": DEFAULTS, "groups": GROUPS, "sources": SOURCES}
