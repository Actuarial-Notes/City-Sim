# CitySim — Environmental Digital Twin

A web application over an extensible, open-data-driven 3D digital-twin platform for
Monte-Carlo simulation of extreme-weather impacts. **Flood** (extreme rainfall →
sewer surcharge / basement backup) is the first hazard module; the twin and the app
are designed so other hazards (wind, heat, ice storm, snow load) plug in later.

Reference geography: **Hamilton, Ontario** — a flood-prone mix of separated *and*
combined sewer systems below the Niagara Escarpment, with unusually good open data.

## Live demo

**https://actuarial-notes.github.io/City-Sim/**

A prebaked deployment: the flood solver runs in CI (`.github/workflows/
pages.yml`), and the twin, four scenario ensembles and their 3D replays ship as
static data — so the hosted demo is the real physics, it just can't launch
*new* ensembles. Pick a scenario (present day, backwater valves, 2050 climate,
2050 + valves), read the dashboard, then scrub a storm in the 3D viewer. Every
model assumption is on display there too; it just can't be changed, because
there is no solver behind it to re-run.

To build twins for other places, change assumptions and run your own ensembles,
run it yourself.

## Running it yourself

```bash
pip install -e ".[dev]"

# CLI demo: twin → Monte-Carlo flood ensemble → $ distribution → mitigation delta
python scripts/demo.py --runs 48

# The web application (full interactive version)
uvicorn citysim.server.app:app --port 8000
# then open http://localhost:8000
```

Or run the container: `docker build -t citysim . && docker run -p 8000:8000
citysim`. Deploying either version — Pages, Fly.io, Render — is
[DEPLOY.md](DEPLOY.md).

Tests: `python -m pytest` (physics, calibration, determinism, assumption
plumbing, API, static export).

## The three-step workflow

```
1  SETUP       build the twin, and see — and change — every model assumption
2  SIMULATE    run the ensemble, then watch a storm play out in the twin
3  RESULTS     the damage distribution, and a record of what was assumed
```

### 1 · Setup — the model is not a black box

Eight tabs, each pairing controls with the actual curve or table they move:

| Tab | What it holds |
|---|---|
| **Region** | The real Hamilton extent, terrain profile from the escarpment to the harbour, land cover. Shown in full, not adjustable — this is geography, not a modelling choice. |
| **Building stock** | Era, material, storey and basement priors, with live histograms of the stock that comes out. |
| **Sewer network** | Combined-sewer extent, pipe capacity, invert depth, inlet capacity, and an annotated section of the backup pathway. |
| **Storm & IDF** | The Hamilton IDF curves plotted from the table the solver interpolates, plus a live Chicago design-storm preview. |
| **Vulnerability** | All five depth-damage curves, the probabilistic-DDC uncertainty band, and the three damage mechanisms. |
| **Economics** | Replacement cost per m² by use, contents ratios, and a running total-exposure figure. |
| **Monte-Carlo** | Run count, seed, and the seven sampled dimensions as range strips. Mitigation and solver settings live here too. |
| **Sources** | Every citation, what it informs, how faithfully it is implemented, and the honest limitations. |

Controls are not hand-written. `citysim/hazards/flood/assumptions.py` and
`citysim/twin/params.py` declare each knob's range, units, description, what it
affects and where it came from; the UI renders itself from that spec, and
`GET /api/hazards/flood/assumptions` serves it. Declaring a new assumption in
Python is all it takes to make it visible and adjustable — and a test fails if a
knob is declared but never actually read by the solver, because a slider the
model ignores is worse than no slider.

Overrides are clamped server-side and travel with each scenario, so
`GET /api/results/{job}/assumptions` reports what the solver *ran* with rather
than what the form submitted.

### 2 · Simulate — shown through replay

The ensemble runs, then a representative storm loads and plays automatically:
rain intensity drives the sky, the rain overlay and the water surface; manholes
flash as they surcharge; hovering a building gives its full physical record plus
the depth standing against it *at that moment*; clicking it gives its loss
distribution across every run.

The camera is **orthographic with a fixed heading** — isometric or straight-down
2D, with pan and zoom only, and deliberately no rotate. Wheel and pinch zoom
anchor on the cursor; drag, arrows/WASD pan; `R` resets, `2`/`3` switch view.

### 3 · Results

Damage distribution, exceedance curve, mechanism mix, the worst-run table — plus
an **Assumptions used** card that highlights anything moved off default and
records the seed the ensemble is reproducible from.

## Three tiers

The central design decision — three tiers, kept strictly separate:

```
TIER 1  Web app         FastAPI + job queue + SPA + WebGL 3D replay viewer
        (interface)     knows nothing about any specific hazard: the hazard
                        picker, the Setup tabs, dashboards and viewer are all
                        driven by registries and declarative specs. The SPA
                        talks to a backend interface (static/api.js), so the
                        same front end runs against the live API or a prebaked
                        static export.
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

**Mitigation scenarios**: `backwater_valves` severs the backup pathway (Hamilton's
Protective Plumbing Program), modelled as an *adoption rate* times an
*effectiveness fraction* rather than a switch — uptake on a subsidy program is
never universal and valves are not perfect. Which homes have one is fixed by a
stable hash of the building id, so runs stay reproducible across worker processes.
`climate_factor` scales storm intensity for 2050-style IDF shifts.

**Every constant above is declared, ranged and cited** in
`citysim/hazards/flood/assumptions.py` — the IDF table, all five depth-damage
curves, the seven sampling ranges, the basement-entry constants, the land-cover
hydrology table, the solver settings — and adjustable from the Setup screen.

### Replay without storing terabytes

Every run is deterministic given its stored scenario, so replay frames are
*derived data*: the API re-simulates a requested run on demand (~seconds) and
caches the frame stack (`uint16` millimetre depth grids). The viewer plays
back cached frames — same UX as storing every grid of every run at a small
fraction of the disk.

## Data modes (Tier 2 ingest)

| Mode | What happens |
|---|---|
| `hamilton` (default) | The real lower city: actual street centrelines and names, the Niagara Escarpment brow, the harbour shoreline, parks, rail corridors, and the combined-sewer core. Always works offline. |
| `synthetic` | Deterministic procedural Hamilton-*like* region: escarpment, harbour-sloping plain, buried-creek ponding corridor, combined-sewer old core, era-realistic building stock. Fully offline, and much smaller/faster than the real extent. |
| `auto` | Geocodes any place (Nominatim) and pulls real OSM building footprints (Overpass) over procedural terrain — a *hybrid* twin for any city name. Falls back to synthetic on any network failure. |

### The Hamilton twin

`scripts/bake_hamilton.py` fetches streets, footprints, water, parks, land use and
the escarpment from OpenStreetMap into
`citysim/twin/data/hamilton_lower_city.json.gz`. The Pages workflow runs it before
baking the site, so the hosted demo is on surveyed geometry.

Because that needs network access, a **hand-digitized fallback** ships committed in
`citysim/twin/data/hamilton_base.py`: real bbox, real named arterials at the grid's
real orientation, the escarpment brow, the harbour shoreline, parks, rail and
landmark labels, with minor residential streets filled in to Hamilton's actual
~90 × 100 m block spacing. It is an approximation, labelled as one in the Sources
tab, and it makes the offline default recognisably Hamilton.

What is real either way: street topology and names, shoreline, escarpment, parks,
the industrial north end, the combined-sewer core, and — with the bake — footprints,
storeys and use. What is still generated: terrain elevations (conditioned on the
real escarpment and shoreline; the LiDAR connector needs GDAL), the sewer network
laid along the real streets, and per-building age, material, foundation and value.

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
| `GET /api/hazards/{name}/assumptions` | every knob, curve, citation and caveat (drives Setup) |
| `GET /api/twin/params` | twin-generation parameter spec |
| `GET /api/results/{job}/assumptions` | the set this ensemble actually ran under |
| `POST /api/twin` | build a twin for a place (returns a job) |
| `GET /api/twin/{id}/scene` | 3D scene payload for the viewer |
| `POST /api/simulate` | launch a Monte-Carlo batch (returns a job) |
| `GET /api/jobs/{id}` / `WS /ws/jobs/{id}` | job progress (poll / stream) |
| `GET /api/results/{job}` | distributions, exceedance, per-building $ |
| `GET /api/results/{job}/buildings/{id}` | one building's loss distribution |
| `GET /api/results/{job}/runs/{n}/frames` | binary replay frames |

The static export mirrors these payloads as flat files — same JSON shapes, same
binary frame format — so the front end has one decode path for both
deployments. See [DEPLOY.md](DEPLOY.md).

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

These are also served from the code (`assumptions.LIMITATIONS`) and shown on the
Setup screen's Sources tab, so they cannot drift away from this file.

- Terrain and the sewer network are generated, though conditioned on the real
  escarpment, shoreline and street grid (the LiDAR and municipal sewer connectors
  are stubs pending GDAL). Absolute dollar figures are illustrative; the per-claim
  magnitudes and mechanism mix are calibrated to published Ontario benchmarks.
- The committed Hamilton base map is hand-digitized, not surveyed. Run
  `scripts/bake_hamilton.py` for real OpenStreetMap geometry.
- The 1D sewer model is a capacity/storage surrogate for SWMM dynamic-wave
  routing; the 2D solver is CPU NumPy at ~10 m resolution on the Hamilton twin
  (city-block scale, not lot scale). The 1D and 2D sides exchange water on a
  coupling timestep coarser than the 2D CFL step.
- Building age, material, foundation and basement depth come from era priors, not
  assessment or permit records.
- Depth-damage curves are shaped after the southern-Ontario literature and
  calibrated, not digitised from source curves — which is what the severity knob
  and its uncertainty band exist to expose.
- Household aggregation reports the residential mean per household; multi-unit
  buildings split losses evenly across units.
- The hosted demo is prebaked. Its numbers come from the real solver, but the
  scenarios and the runs you can replay are the ones baked at build time —
  arbitrary places, ensemble sizes, assumption changes and mitigation
  combinations need the full app.
