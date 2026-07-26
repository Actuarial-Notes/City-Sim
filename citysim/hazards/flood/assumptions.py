"""Every flood-model assumption, in one declarative place.

Before V2 these constants were scattered as literals across ``rainfall.py``,
``damage.py``, ``module.py``, ``coupled.py``, ``sewer1d.py`` and
``surface2d.py``, with their provenance living only in module docstrings. That
made the model a black box: a user could read the dollar figures but not what
produced them.

This module is the single source of truth. It holds

* ``KNOBS``     — the adjustable scalars, each with range, unit, description
                  and the source it came from. The Setup UI renders itself
                  from this list, so a new assumption needs no UI code.
* ``CURVES``    — the tabulated IDF and depth-damage curves, in a plottable
                  shape, so the front end charts *the same numbers the solver
                  uses* rather than a copy.
* ``SOURCES``   — machine-readable citations, each declaring what it informs.
* ``LIMITATIONS`` — the honest caveats, surfaced in-app instead of only in the
                  README.
* ``resolve()`` — defaults overlaid with validated user overrides, producing
                  the flat dict the solver reads.

The resolved dict is stashed into every Monte-Carlo scenario's ``options``
(see ``FloodModule.sample_scenarios``), so it reaches worker processes through
the existing pickle path with no new plumbing.
"""

from __future__ import annotations

# ---------------------------------------------------------------- knob specs
# group      — Setup tab the knob belongs to
# advanced   — hidden behind "show advanced" (solver internals, not judgement calls)
# affects    — human-readable note on what moves when this moves
# source     — id into SOURCES

KNOBS: list[dict] = [
    # ------------------------------------------------------------ storm / IDF
    {
        "id": "climate_factor", "group": "storm", "label": "Climate intensity scaling",
        "type": "float", "default": 1.0, "min": 0.8, "max": 1.6, "step": 0.05, "unit": "×",
        "description": "Multiplies rainfall depth to represent a future IDF curve. "
                       "1.15 ≈ 2050, 1.30 ≈ 2080 under IDF_CC-style scaling.",
        "affects": "storm volume in every run", "source": "idf_cc",
        "presets": [{"label": "present day", "value": 1.0},
                    {"label": "2050 (+15%)", "value": 1.15},
                    {"label": "2080 (+30%)", "value": 1.30}],
    },
    {
        "id": "idf_scale", "group": "storm", "label": "IDF table scaling",
        "type": "float", "default": 1.0, "min": 0.5, "max": 2.0, "step": 0.05, "unit": "×",
        "description": "Scales the whole Hamilton IDF table. Use it to test how much "
                       "the answer depends on the rainfall statistics themselves — "
                       "separately from the climate-change scenario.",
        "affects": "storm volume in every run", "source": "eccc_idf",
    },
    {
        "id": "idf_decay_c", "group": "storm", "label": "Duration decay exponent (c)",
        "type": "float", "default": 0.72, "min": 0.50, "max": 0.95, "step": 0.01, "unit": "",
        "description": "Intensity falls with duration as i = a·t⁻ᶜ. Higher c means long "
                       "storms are relatively weaker, so short intense bursts dominate.",
        "affects": "how storm depth grows with duration", "source": "eccc_idf",
    },
    {
        "id": "return_period_cap", "group": "storm", "label": "Return-period truncation",
        "type": "float", "default": 500.0, "min": 50.0, "max": 1000.0, "step": 10.0, "unit": "yr",
        "description": "The annual-exceedance draw T = 1/U is truncated here. The IDF "
                       "table only extends to 100 yr, so anything beyond is extrapolation.",
        "affects": "the extreme tail of the loss distribution", "source": "eccc_idf",
    },
    {
        "id": "duration_min_h", "group": "storm", "label": "Storm duration — minimum",
        "type": "float", "default": 1.0, "min": 0.5, "max": 6.0, "step": 0.25, "unit": "h",
        "description": "Lower bound of the sampled storm duration.",
        "affects": "sampled storm duration", "source": "eccc_idf", "pair": "duration_max_h",
    },
    {
        "id": "duration_max_h", "group": "storm", "label": "Storm duration — maximum",
        "type": "float", "default": 6.0, "min": 1.0, "max": 24.0, "step": 0.25, "unit": "h",
        "description": "Upper bound of the sampled storm duration. Convective summer "
                       "cells (the backup-driving events) sit at the short end.",
        "affects": "sampled storm duration", "source": "eccc_idf",
    },
    {
        "id": "peak_frac_min", "group": "storm", "label": "Hyetograph peak — earliest",
        "type": "float", "default": 0.15, "min": 0.05, "max": 0.50, "step": 0.05, "unit": "",
        "description": "Where the Chicago design storm peaks, as a fraction of duration. "
                       "0.15 is strongly front-loaded.",
        "affects": "storm shape", "source": "chicago_storm", "pair": "peak_frac_max",
    },
    {
        "id": "peak_frac_max", "group": "storm", "label": "Hyetograph peak — latest",
        "type": "float", "default": 0.85, "min": 0.50, "max": 0.95, "step": 0.05, "unit": "",
        "description": "0.85 is strongly back-loaded — the worst case for a catchment "
                       "that is already wet when the peak arrives.",
        "affects": "storm shape", "source": "chicago_storm",
    },

    # ------------------------------------------------------- vulnerability
    {
        "id": "ddc_severity", "group": "vulnerability", "label": "Depth-damage severity",
        "type": "float", "default": 1.0, "min": 0.4, "max": 2.0, "step": 0.05, "unit": "×",
        "description": "Scales every depth-damage curve. The curves are shaped after the "
                       "southern-Ontario literature but calibrated, not measured — this "
                       "is the honest uncertainty on that calibration.",
        "affects": "all damage, proportionally", "source": "ddc_ontario",
    },
    {
        "id": "ddc_sigma", "group": "vulnerability", "label": "Depth-damage uncertainty (σ)",
        "type": "float", "default": 0.9, "min": 0.0, "max": 1.8, "step": 0.05, "unit": "",
        "description": "Width of the per-run lognormal draw on the curves. σ = 0.9 gives "
                       "roughly a 0.64×–1.57× band; σ = 0 makes every run use the median "
                       "curve and collapses cost uncertainty out of the distribution.",
        "affects": "spread of the loss distribution", "source": "pddc",
    },
    {
        "id": "sill_depth", "group": "vulnerability", "label": "Basement entry threshold",
        "type": "float", "default": 0.25, "min": 0.0, "max": 1.0, "step": 0.01, "unit": "m",
        "description": "Standing water beside a building must exceed this before it enters "
                       "the basement through window wells and low sills. Below it, "
                       "foundation drainage is assumed to keep up.",
        "affects": "how often surface water reaches basements", "source": "ddc_ontario",
    },
    {
        "id": "backup_freeboard", "group": "vulnerability", "label": "Backup freeboard",
        "type": "float", "default": 0.20, "min": 0.0, "max": 1.0, "step": 0.01, "unit": "m",
        "description": "Static head loss through the trap and lateral before sewer backup "
                       "reaches the basement floor.",
        "affects": "how often sewer backup causes damage", "source": "ddc_ontario",
    },
    {
        "id": "lateral_factor", "group": "vulnerability", "label": "Lateral attenuation",
        "type": "float", "default": 0.6, "min": 0.1, "max": 1.0, "step": 0.05, "unit": "",
        "description": "Friction losses along the service lateral and the finite duration "
                       "of surcharge keep the basement below full hydraulic-grade-line "
                       "equilibrium. 1.0 would mean the basement fills to the street HGL.",
        "affects": "severity of sewer-backup damage", "source": "ddc_ontario",
    },
    {
        "id": "lateral_reach_m", "group": "vulnerability", "label": "Service-lateral reach",
        "type": "float", "default": 90.0, "min": 20.0, "max": 250.0, "step": 5.0, "unit": "m",
        "description": "A building is exposed to sewer backup only if a combined-sewer node "
                       "lies within this distance of it.",
        "affects": "how many buildings are exposed to backup at all", "source": "hamilton_sewer",
    },

    # ------------------------------------------------------------- sampling
    {
        "id": "infil_min", "group": "sampling", "label": "Antecedent moisture — wettest",
        "type": "float", "default": 0.30, "min": 0.0, "max": 1.5, "step": 0.05, "unit": "×",
        "description": "Multiplier on soil infiltration capacity. Low = catchment already "
                       "saturated from previous rain, so almost everything runs off.",
        "affects": "runoff volume", "source": "antecedent", "pair": "infil_max",
    },
    {
        "id": "infil_max", "group": "sampling", "label": "Antecedent moisture — driest",
        "type": "float", "default": 1.30, "min": 0.1, "max": 2.0, "step": 0.05, "unit": "×",
        "description": "High = dry soil with full infiltration capacity available.",
        "affects": "runoff volume", "source": "antecedent",
    },
    {
        "id": "manning_min", "group": "sampling", "label": "Surface roughness — lowest",
        "type": "float", "default": 0.80, "min": 0.5, "max": 1.5, "step": 0.05, "unit": "×",
        "description": "Multiplier on the land-cover Manning n grid.",
        "affects": "how fast water moves overland", "source": "landcover", "pair": "manning_max",
    },
    {
        "id": "manning_max", "group": "sampling", "label": "Surface roughness — highest",
        "type": "float", "default": 1.25, "min": 0.5, "max": 2.0, "step": 0.05, "unit": "×",
        "description": "Rougher surfaces slow the flow, deepening local ponding.",
        "affects": "how fast water moves overland", "source": "landcover",
    },
    {
        "id": "blockage_min", "group": "sampling", "label": "Pipe capacity — most derated",
        "type": "float", "default": 0.60, "min": 0.2, "max": 1.0, "step": 0.05, "unit": "×",
        "description": "Sediment, debris, roots and aging cut real pipe capacity below the "
                       "clean-pipe Manning value.",
        "affects": "how early the sewer surcharges", "source": "hamilton_sewer",
        "pair": "blockage_max",
    },
    {
        "id": "blockage_max", "group": "sampling", "label": "Pipe capacity — least derated",
        "type": "float", "default": 1.00, "min": 0.2, "max": 1.0, "step": 0.05, "unit": "×",
        "description": "1.0 = clean pipe at full design capacity.",
        "affects": "how early the sewer surcharges", "source": "hamilton_sewer",
    },

    # ----------------------------------------------------------- mitigation
    {
        "id": "backwater_valves", "group": "mitigation", "label": "Backwater valves installed",
        "type": "bool", "default": False,
        "description": "Hamilton's Protective Plumbing Program subsidises backwater valves, "
                       "which sever the sewer-backup pathway into the basement.",
        "affects": "the sewer-backup mechanism", "source": "hamilton_ppp",
    },
    {
        "id": "valve_adoption", "group": "mitigation", "label": "Valve adoption rate",
        "type": "float", "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
        "description": "Fraction of residential buildings that actually have a valve. "
                       "Uptake on a subsidy program is never universal; 1.0 is the "
                       "upper-bound 'everyone installs one' scenario.",
        "affects": "how many homes are protected", "source": "hamilton_ppp",
    },
    {
        "id": "valve_effectiveness", "group": "mitigation", "label": "Valve effectiveness",
        "type": "float", "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05, "unit": "frac",
        "description": "Fraction of backup head a working valve blocks. Below 1.0 covers "
                       "maintenance failure, debris fouling and unprotected floor drains.",
        "affects": "residual backup damage in protected homes", "source": "hamilton_ppp",
    },

    # --------------------------------------------------------------- solver
    {
        "id": "drain_time_s", "group": "solver", "label": "Post-storm drain-down",
        "type": "float", "default": 2400.0, "min": 600.0, "max": 7200.0, "step": 100.0, "unit": "s",
        "description": "How long the solver keeps running after rainfall stops, so ponded "
                       "water can drain and peak depths are not cut off early.",
        "affects": "run time and peak depths", "source": "lisflood", "advanced": True,
    },
    {
        "id": "frame_dt_s", "group": "solver", "label": "Replay frame cadence",
        "type": "float", "default": 120.0, "min": 30.0, "max": 600.0, "step": 10.0, "unit": "s",
        "description": "Simulated seconds between recorded replay frames. Finer gives a "
                       "smoother replay and a larger payload.",
        "affects": "replay smoothness and download size", "source": "lisflood", "advanced": True,
    },
    {
        "id": "cfl_alpha", "group": "solver", "label": "CFL coefficient",
        "type": "float", "default": 0.7, "min": 0.2, "max": 0.95, "step": 0.05, "unit": "",
        "description": "Adaptive-timestep safety factor for the local-inertial scheme. "
                       "Lower is more stable and slower.",
        "affects": "numerical stability and run time", "source": "lisflood", "advanced": True,
    },
    {
        "id": "coupling_dt_s", "group": "solver", "label": "1D/2D coupling timestep",
        "type": "float", "default": 10.0, "min": 1.0, "max": 60.0, "step": 1.0, "unit": "s",
        "description": "How often the sewer network and the surface exchange water. The "
                       "surface timestep is CFL-limited and can drop below a second; the "
                       "sewer is a storage model that does not need to run that fast. "
                       "Captured volume accumulates in between, so nothing is lost.",
        "affects": "run time, and slightly the timing of surcharge onset",
        "source": "swmm", "advanced": True,
    },
    {
        "id": "chamber_area", "group": "solver", "label": "Manhole chamber area",
        "type": "float", "default": 1.5, "min": 0.5, "max": 5.0, "step": 0.1, "unit": "m²",
        "description": "Effective storage cross-section at each node. Larger chambers "
                       "absorb more volume before surcharging.",
        "affects": "how quickly nodes surcharge", "source": "swmm", "advanced": True,
    },
    {
        "id": "dwf_per_node", "group": "solver", "label": "Dry-weather flow per node",
        "type": "float", "default": 0.0008, "min": 0.0, "max": 0.005, "step": 0.0001,
        "unit": "m³/s",
        "description": "Sanitary base flow already occupying capacity in a combined sewer "
                       "before the storm arrives.",
        "affects": "combined-sewer headroom", "source": "swmm", "advanced": True,
    },
    {
        "id": "building_raise", "group": "solver", "label": "Building DEM raise",
        "type": "float", "default": 8.0, "min": 2.0, "max": 20.0, "step": 0.5, "unit": "m",
        "description": "Buildings are lifted out of the DEM so overland flow routes around "
                       "them rather than through them.",
        "affects": "flow paths between buildings", "source": "lisflood", "advanced": True,
    },
]

DEFAULTS: dict = {k["id"]: k["default"] for k in KNOBS}
_BY_ID: dict = {k["id"]: k for k in KNOBS}


# ------------------------------------------------------------------- curves
# The tabulated data the solver actually interpolates. Shipped to the front end
# so the Setup charts plot these numbers rather than a hand-copied duplicate.

IDF_1H_MM: dict[int, float] = {2: 22.0, 5: 28.0, 10: 32.0, 25: 37.0, 50: 41.0, 100: 45.0}

BASEMENT_STRUCT: dict[str, list[tuple[float, float]]] = {
    "masonry":    [(0.0, 0.0), (0.05, 0.020), (0.3, 0.045), (0.6, 0.060),
                   (1.0, 0.075), (1.5, 0.090), (2.5, 0.110)],
    "wood_frame": [(0.0, 0.0), (0.05, 0.024), (0.3, 0.052), (0.6, 0.070),
                   (1.0, 0.088), (1.5, 0.105), (2.5, 0.128)],
}
MAIN_STRUCT: list[tuple[float, float]] = [
    (0.0, 0.0), (0.1, 0.05), (0.3, 0.12), (0.6, 0.20),
    (1.0, 0.27), (1.5, 0.33), (2.0, 0.40), (3.0, 0.48)]
BASEMENT_CONTENTS: list[tuple[float, float]] = [
    (0.0, 0.0), (0.1, 0.05), (0.5, 0.16), (1.0, 0.25), (2.0, 0.33)]
MAIN_CONTENTS: list[tuple[float, float]] = [
    (0.0, 0.0), (0.3, 0.30), (1.0, 0.60), (2.0, 0.80)]

# Land cover → hydrology. Indexed by LandCover class code
# (0 water, 1 paved, 2 building, 3 grass, 4 trees, 5 bare soil). This used to be
# duplicated in synthetic.py and builder.py, where the two copies could silently
# diverge; both now import it from here.
LANDCOVER_CLASSES = ["water", "paved", "building", "grass", "trees", "bare soil"]
LANDCOVER_MANNING = [0.03, 0.013, 0.02, 0.05, 0.10, 0.035]
LANDCOVER_IMPERVIOUSNESS = [1.0, 0.95, 0.98, 0.05, 0.02, 0.15]
LANDCOVER_INFILTRATION = [0.0, 0.5, 0.0, 9.0, 14.0, 6.0]      # mm/h

CURVES: dict = {
    "idf": {
        "label": "Hamilton-area IDF — 1-hour rainfall depth by return period",
        "x_label": "return period (yr)", "y_label": "1-h depth (mm)",
        "points": [{"T": T, "mm": mm} for T, mm in sorted(IDF_1H_MM.items())],
        "decay_c": _BY_ID["idf_decay_c"]["default"],
        "note": "Depth at other durations follows depth = depth₁ₕ · t^(1−c). "
                "Between tabulated return periods the model interpolates linearly "
                "in log T, which is what a Gumbel fit implies.",
        "source": "eccc_idf",
    },
    "depth_damage": {
        "label": "Depth-damage curves — fraction of value lost vs water depth",
        "x_label": "water depth (m)", "y_label": "fraction of value",
        "series": [
            {"id": "basement_struct_masonry", "label": "Basement structure — masonry",
             "points": BASEMENT_STRUCT["masonry"], "applies_to": "structure_value"},
            {"id": "basement_struct_wood", "label": "Basement structure — wood frame",
             "points": BASEMENT_STRUCT["wood_frame"], "applies_to": "structure_value"},
            {"id": "main_struct", "label": "Main-floor structure",
             "points": MAIN_STRUCT, "applies_to": "structure_value"},
            {"id": "basement_contents", "label": "Basement contents",
             "points": BASEMENT_CONTENTS, "applies_to": "contents_value"},
            {"id": "main_contents", "label": "Main-floor contents",
             "points": MAIN_CONTENTS, "applies_to": "contents_value"},
        ],
        "note": "Only masonry and wood-frame have distinct basement curves; concrete and "
                "steel buildings fall back to the masonry curve (they are generated with "
                "slab foundations, so in practice the fallback is never reached).",
        "source": "ddc_ontario",
    },
    "landcover": {
        "label": "Land cover → surface hydrology",
        "columns": ["class", "Manning n", "imperviousness", "infiltration (mm/h)"],
        "rows": [[LANDCOVER_CLASSES[i], LANDCOVER_MANNING[i],
                  LANDCOVER_IMPERVIOUSNESS[i], LANDCOVER_INFILTRATION[i]]
                 for i in range(len(LANDCOVER_CLASSES))],
        "note": "Hamilton lowland soils are clay-heavy, so infiltration capacity is modest "
                "even on grass.",
        "source": "landcover",
    },
    "mechanisms": {
        "label": "Damage mechanisms",
        "items": [
            {"id": "sewer_backup", "label": "Sewer backup",
             "description": "The combined-sewer hydraulic grade line rises above the "
                            "basement floor and pushes back up the service lateral. "
                            "Requires a combined node within the lateral reach."},
            {"id": "basement_inundation", "label": "Basement inundation",
             "description": "Overland water beside the building exceeds the sill depth "
                            "and enters through window wells and low openings."},
            {"id": "overland_flooding", "label": "Main-floor overland flooding",
             "description": "Surface water rises above the first-floor height and enters "
                            "the occupied storey. Rare, and by far the most expensive."},
        ],
    },
}


# ------------------------------------------------------------------ sources
SOURCES: list[dict] = [
    {"id": "eccc_idf",
     "title": "Engineering Climate Datasets — IDF Files (Hamilton A / Hamilton RBG)",
     "publisher": "Environment and Climate Change Canada", "year": "2022",
     "url": "https://climate.weather.gc.ca/prods_servs/engineering_e.html",
     "informs": "the IDF table, duration decay and return-period range",
     "fidelity": "approximated",
     "note": "The shipped table is ECCC-shaped, not a parsed station file: it reproduces "
             "the familiar southern-Ontario 24-h totals (2-yr ≈ 50 mm, 100-yr ≈ 125 mm) "
             "to within a few percent. Swapping in an exact station IDF replaces only "
             "this table."},
    {"id": "idf_cc", "title": "IDF_CC Tool — climate-adjusted IDF curves",
     "publisher": "Western University, Facility for Intelligent Decision Support",
     "year": "2023", "url": "https://www.idf-cc-uwo.ca/",
     "informs": "the climate intensity scaling factor", "fidelity": "approximated",
     "note": "Represented here as a single scalar multiplier on depth, not a re-derived "
             "curve family."},
    {"id": "chicago_storm", "title": "Keifer & Chu, 'Synthetic storm pattern for drainage design'",
     "publisher": "Journal of the Hydraulics Division, ASCE", "year": "1957",
     "url": "https://doi.org/10.1061/JYCEAJ.0000104",
     "informs": "the Chicago design-storm hyetograph shape", "fidelity": "implemented"},
    {"id": "ddc_ontario",
     "title": "Southern-Ontario depth-damage curve literature (Paragon / EC / OMNR base "
              "set; IBI and Hatch synthetic curves)",
     "publisher": "various", "year": "1990–2019", "url": "",
     "informs": "all five depth-damage curves and the basement-entry constants",
     "fidelity": "shaped-after",
     "note": "Curve shapes follow the published families; magnitudes are calibrated so a "
             "full-surcharge basement backup lands in the Ontario-reported $40–45k range. "
             "These are not digitised source curves."},
    {"id": "pddc", "title": "Probabilistic depth-damage functions in flood risk assessment",
     "publisher": "flood-risk literature (e.g. Merz et al., NHESS)", "year": "2013",
     "url": "https://doi.org/10.5194/nhess-13-53-2013",
     "informs": "the per-run lognormal draw on the damage curves", "fidelity": "implemented"},
    {"id": "lisflood",
     "title": "Bates, Horritt & Fewtrell, 'A simple inertial formulation of the shallow "
              "water equations'",
     "publisher": "Journal of Hydrology 387(1–2)", "year": "2010",
     "url": "https://doi.org/10.1016/j.jhydrol.2010.03.027",
     "informs": "the 2D overland-flow solver (the LISFLOOD-FP / SynxFlow scheme)",
     "fidelity": "implemented",
     "note": "Implemented on CPU NumPy at ~6 m resolution — city-block scale, not lot scale."},
    {"id": "swmm", "title": "EPA Storm Water Management Model (SWMM) reference manual",
     "publisher": "US Environmental Protection Agency", "year": "2016",
     "url": "https://www.epa.gov/water-research/storm-water-management-model-swmm",
     "informs": "the 1D sewer storage-routing surrogate and its node constants",
     "fidelity": "surrogate",
     "note": "A capacity/storage surrogate, not dynamic-wave routing. PySWMM drops in "
             "behind the Sewer1D interface unchanged."},
    {"id": "hamilton_sewer", "title": "City of Hamilton Open Data — sewer and manhole layers",
     "publisher": "City of Hamilton", "year": "2024",
     "url": "https://open.hamilton.ca/",
     "informs": "combined-sewer extent, pipe derating and service-lateral reach",
     "fidelity": "approximated",
     "note": "The connector exists (citysim/twin/connectors/hamilton_opendata.py) but the "
             "demo network is procedural, laid on the real street grid."},
    {"id": "hamilton_ppp", "title": "Protective Plumbing Program (backwater valve subsidy)",
     "publisher": "City of Hamilton", "year": "2024",
     "url": "https://www.hamilton.ca/home-neighbourhood/water-sewer/protective-plumbing-program",
     "informs": "the backwater-valve mitigation scenario", "fidelity": "approximated"},
    {"id": "antecedent", "title": "Antecedent moisture condition (SCS curve-number method)",
     "publisher": "USDA NRCS National Engineering Handbook", "year": "2004", "url": "",
     "informs": "the sampled infiltration-capacity multiplier", "fidelity": "shaped-after"},
    {"id": "landcover", "title": "Manning's n and infiltration tables for urban surfaces",
     "publisher": "standard hydraulic references (Chow; ASCE)", "year": "1959–",
     "url": "", "informs": "per-cell roughness, imperviousness and infiltration",
     "fidelity": "standard-values"},
    {"id": "osm", "title": "OpenStreetMap", "publisher": "OpenStreetMap contributors",
     "year": "2025", "url": "https://www.openstreetmap.org/copyright",
     "informs": "building footprints, streets, water and parks in the Hamilton twin",
     "fidelity": "live-data", "note": "Licensed ODbL."},
    {"id": "replacement_cost",
     "title": "Residential and commercial replacement cost benchmarks (Ontario, 2025)",
     "publisher": "construction cost guides", "year": "2025", "url": "",
     "informs": "replacement cost per m² and contents ratios", "fidelity": "approximated"},
]

LIMITATIONS: list[dict] = [
    {"title": "Terrain and sewer are procedural",
     "detail": "The LiDAR DTM and municipal sewer connectors are stubs pending GDAL-class "
               "dependencies. Elevations follow the real escarpment/harbour geometry but "
               "are generated, not surveyed, so absolute dollar figures are illustrative. "
               "Per-claim magnitudes and the mechanism mix are calibrated to published "
               "Ontario benchmarks."},
    {"title": "Sewer hydraulics are a surrogate",
     "detail": "Storage routing with Manning full-flow capacities stands in for SWMM "
               "dynamic-wave routing. It captures when and where surcharge happens, not "
               "the detailed pressure wave."},
    {"title": "Resolution is city-block scale",
     "detail": "The 2D solver runs on CPU NumPy at roughly 6 m cells. Lot-scale features — "
               "individual driveways, window wells, grading — are below the grid."},
    {"title": "Building attributes are inferred, not assessed",
     "detail": "Age, material, foundation type and basement depth come from era priors, "
               "not assessment or permit records. Footprints, storeys and use are real "
               "where OpenStreetMap supplies them."},
    {"title": "Household aggregation is even",
     "detail": "Multi-unit buildings split losses evenly across dwelling units, and the "
               "headline figure is the residential mean per household."},
    {"title": "Mitigation is modelled coarsely",
     "detail": "Backwater valves are an adoption rate times an effectiveness fraction, "
               "applied to residential buildings. Real programs vary by lot, plumbing "
               "configuration and maintenance."},
]


# ------------------------------------------------------------------ resolve
def _coerce(spec: dict, value):
    if spec["type"] == "bool":
        return bool(value)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return spec["default"]
    if v != v:                       # NaN
        return spec["default"]
    return min(max(v, spec["min"]), spec["max"])


def resolve(options: dict | None = None) -> dict:
    """Defaults overlaid with validated overrides.

    Unknown keys are ignored rather than rejected, so a mitigation flag or a
    preset id riding in the same ``options`` dict costs nothing. Values are
    clamped to their declared range — the API never trusts the client to have
    kept the slider honest.

    ``min``/``max`` pairs are ordered after clamping, so a user who drags the
    minimum above the maximum gets a valid (swapped) range instead of an empty
    one that would silently produce degenerate scenarios.
    """
    options = options or {}
    out = dict(DEFAULTS)
    for key, spec in _BY_ID.items():
        if key in options:
            out[key] = _coerce(spec, options[key])

    for spec in KNOBS:
        hi_key = spec.get("pair")
        if hi_key and out[spec["id"]] > out[hi_key]:
            out[spec["id"]], out[hi_key] = out[hi_key], out[spec["id"]]

    return out


def deviations(resolved: dict) -> list[dict]:
    """Knobs that differ from their default — what the Results screen highlights."""
    out = []
    for spec in KNOBS:
        v = resolved.get(spec["id"], spec["default"])
        if v != spec["default"]:
            out.append({"id": spec["id"], "label": spec["label"],
                        "group": spec["group"], "unit": spec.get("unit", ""),
                        "default": spec["default"], "value": v})
    return out


def spec() -> dict:
    """The full payload the Setup screen renders itself from."""
    return {
        "hazard": "flood",
        "knobs": KNOBS,
        "defaults": DEFAULTS,
        "curves": CURVES,
        "sources": SOURCES,
        "limitations": LIMITATIONS,
        "groups": [
            {"id": "storm", "label": "Storm & IDF",
             "blurb": "How much rain falls, over how long, and in what shape."},
            {"id": "vulnerability", "label": "Vulnerability",
             "blurb": "How water depth turns into dollars, and how it gets inside."},
            {"id": "sampling", "label": "Monte-Carlo",
             "blurb": "The uncertainty ranges sampled across the ensemble."},
            {"id": "mitigation", "label": "Mitigation",
             "blurb": "Interventions tested against the same storms."},
            {"id": "solver", "label": "Solver",
             "blurb": "Numerical settings. Changing these changes run time and stability, "
                      "not the physics being represented."},
        ],
    }
