# CitySim — Environmental Digital Twin

A web application over an extensible, open-data-driven 3D digital-twin platform for
Monte-Carlo simulation of extreme-weather impacts. **Flood** (extreme rainfall →
sewer surcharge / basement backup) is the first hazard module; the twin and the app
are designed so other hazards (wind, heat, ice storm, snow load) plug in later.

Reference geography: **Hamilton, Ontario** — a flood-prone mix of separated *and*
combined sewer systems below the Niagara Escarpment, with unusually good open data.

## Quick start

```bash
pip install -e ".[dev]"

# CLI demo: twin → Monte-Carlo flood ensemble → $ distribution → mitigation delta
python scripts/demo.py --runs 48

# The web application
uvicorn citysim.server.app:app --port 8000
# then open http://localhost:8000
```

In the browser: pick a location and hazard, hit **Build twin & run simulation**,
watch the ensemble progress, then explore the **Results** dashboard (damage
distribution, exceedance curve, damage mechanisms) and the **3D Replay** viewer —
scrub any storm through time, watch manholes surcharge, colour buildings by
mean $ risk, x-ray the sewer network, and click any building for its personal
damage distribution across all runs.

Tests: `python -m pytest` (30 tests: physics, calibration, determinism, API).

## Three tiers

The central design decision — three tiers, kept strictly separate:

```
TIER 1  Web app         FastAPI + job queue + SPA + WebGL 3D replay viewer
        (interface)     knows nothing about any specific hazard: the hazard
                        picker, dashboards and viewer are driven by the registry
─────────────────────────────────────────────────────────────────────────────
TIER 2  Twin core       citysim/twin — terrain (DTM), buildings (footprint,
        (hazard-        height, material, age, value), sewer/storm network,
        agnostic)       land cover → roughness/infiltration grids.
                        Stores PHYSICAL attributes only, never hazard
                        interpretations ("masonry, 2 storeys, built 1935" —
                        not "flood class 4").
─────────────────────────────────────────────────────────────────────────────
TIER 3  Hazard modules  citysim/hazards — each module obeys one contract:
        (pluggable)     consume twin → Monte-Carlo sample a scenario → run a
                        physics solver → emit (a) per-timestep state frames
                        for replay + (b) per-building/household $ losses.
                        FLOOD is built; wind/heat/ice/snow surface in the UI
                        as roadmap entries.
```

Shared scaffolding: `citysim/montecarlo` (Latin-Hypercube sampling + parallel
seeded batch runner + distribution/VaR/CVaR/exceedance reduction) and
`citysim/results` (job store + replay-frame cache) are hazard-agnostic and
reused unchanged by every future module.

## The flood module (Tier 3, first hazard)

Urban rain flooding — especially basement backup — is modelled as a coupled
1D/2D system, following the standard academic approach:

- **Rainfall driver** (`flood/rainfall.py`) — Hamilton-area IDF statistics
  (ECCC-shaped table), continuous return-period sampling (`T = 1/U`), Chicago
  design-storm hyetographs with front/centre/back-loaded peaks, and a climate
  scaling factor for future-storm scenarios (IDF_CC-style).
- **1D sewer network** (`flood/sewer1d.py`) — SWMM-surrogate storage routing:
  Manning full-flow conduit capacities, chamber storage, dry-weather flow, and
  *surcharge* back to the surface when capacity is exceeded. The node hydraulic
  grade line drives the basement-backup mechanism in combined-sewer areas.
- **2D overland flow** (`flood/surface2d.py`) — raster shallow-water solver
  using the *local inertial* formulation (Bates, Horritt & Fewtrell 2010 — the
  scheme in LISFLOOD-FP/SynxFlow), with adaptive CFL timestep, positivity-
  preserving flux limiter, per-cell Manning roughness and infiltration, and
  buildings raised out of the DEM so water routes around them.
- **Coupling** (`flood/coupled.py`) — bidirectional exchange at manholes/
  catchbasins each step: inlet capture off the streets, surcharge injection
  back onto the grid. Emits replay frames at a fixed cadence.
- **Impact engine** (`flood/damage.py`) — southern-Ontario-shaped depth-damage
  curves (basement vs main floor, masonry vs wood-frame, structure vs
  contents), three mechanisms per building (sewer backup, basement inundation,
  overland flooding), probabilistic DDC scale draws per run. Calibrated so a
  full-surcharge backup claim lands in the Ontario-reported $40–45k range.
- **Monte-Carlo sampled per run**: return period, storm duration, hyetograph
  shape, antecedent moisture (infiltration factor), Manning factor, pipe
  blockage/derating, DDC uncertainty. Latin Hypercube over 7 dims; every run
  carries its own seed and is **exactly reproducible**.

**Mitigation scenarios**: `{"backwater_valves": true}` severs the backup
pathway (Hamilton's Protective Plumbing Program); `{"climate_factor": 1.15}`
scales storm intensity for 2050-style IDF shifts. Both are exposed in the UI.

### Replay without storing terabytes

Every run is deterministic given its stored scenario, so replay frames are
*derived data*: the API re-simulates a requested run on demand (~seconds) and
caches the frame stack (`uint16` millimetre depth grids). The viewer plays
back cached frames — same UX as storing every grid of every run at a small
fraction of the disk.

## Data modes (Tier 2 ingest)

| Mode | What happens |
|---|---|
| `synthetic` (default) | Deterministic procedural Hamilton-like region: escarpment, harbour-sloping plain, buried-creek ponding corridor, combined-sewer old core, era-realistic building stock. Always works, fully offline. |
| `auto` | Geocodes the place (Nominatim) and pulls real OSM building footprints (Overpass) over procedural terrain — a *hybrid* twin for any city name. Falls back to synthetic on any network failure. |

`citysim/twin/connectors/` also documents the full live-data path from the
build plan — Ontario GeoHub LiDAR DTM/DSM (ArcGIS ImageServer), City of
Hamilton Open Data sewer/manhole layers (ArcGIS FeatureServer GeoJSON) — with
the exact endpoints; wiring them fully requires rasterio/GDAL
(`pip install citysim[geo] rasterio`) and is the designed upgrade path, as are
PySWMM (drop-in behind `Sewer1D`'s interface) and GPU SynxFlow (behind
`Surface2D.step`).

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/hazards` | available + roadmap hazard modules (drives the picker) |
| `POST /api/twin` | build a twin for a place (returns a job) |
| `GET /api/twin/{id}/scene` | 3D scene payload for the viewer |
| `POST /api/simulate` | launch a Monte-Carlo batch (returns a job) |
| `GET /api/jobs/{id}` / `WS /ws/jobs/{id}` | job progress (poll / stream) |
| `GET /api/results/{job}` | distributions, exceedance, per-building $ |
| `GET /api/results/{job}/buildings/{id}` | one building's loss distribution |
| `GET /api/results/{job}/runs/{n}/frames` | binary replay frames |

## Adding the next hazard

1. Implement `HazardModule` (`sample_scenarios` / `simulate` / `assess_impact`)
   in `citysim/hazards/<name>/`, decorated with `@register`.
2. That's the whole change: the Monte-Carlo runner, results store, REST API,
   dashboard, and 3D viewer shell all operate on the contract; the hazard
   picker lists modules from the registry.

The guardrails that keep this true: nothing flood-specific exists above
Tier 3, and the twin stores physical attributes only — each module derives its
own vulnerability interpretation.

## Honest limitations

- The demo terrain/sewer are procedural (LiDAR + sewer-layer connectors are
  stubs pending GDAL); absolute dollar figures are illustrative, though the
  per-claim magnitudes and mechanism mix are calibrated to published Ontario
  benchmarks.
- The 1D sewer model is a capacity/storage surrogate for SWMM dynamic-wave
  routing; the 2D solver is CPU NumPy at ~6 m resolution (city-block scale,
  not lot scale).
- Household aggregation reports the residential mean per household;
  multi-unit buildings split losses evenly across units.
