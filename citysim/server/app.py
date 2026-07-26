"""Tier 1 — the web application's FastAPI back end.

Endpoints (all hazard-agnostic; the hazard registry drives the picker):

  GET  /api/hazards                       available + planned hazard modules
  GET  /api/hazards/{name}/assumptions    adjustable knobs, curves, sources
  GET  /api/twin/params                   twin-generation parameter spec
  POST /api/twin                          build a twin for a place (job)
  GET  /api/twins                         stored twins
  GET  /api/twin/{twin_id}/scene          3D scene payload for the viewer
  POST /api/simulate                      launch a Monte-Carlo batch (job)
  GET  /api/jobs/{job_id}                 job status/progress (poll)
  WS   /ws/jobs/{job_id}                  job progress stream
  GET  /api/results/{job_id}              distributions, exceedance, per-building $
  GET  /api/results/{job_id}/assumptions  the set this ensemble actually ran under
  GET  /api/results/{job_id}/runs/{run}/frames   replay frames (binary)

Replay frames are re-simulated deterministically from the run's stored
scenario on first request and cached — plan §5.9's "playback of stored
frames", implemented with derived-data caching instead of storing every grid
of every run.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from citysim.hazards import get_module
from citysim.hazards.registry import list_modules
from citysim.montecarlo import MonteCarloRunner
from citysim.results import ResultsStore, pack_frames
from citysim.twin import build_twin, scene_payload
from citysim.twin import params as twin_params
from citysim.twin.store import TwinStore
from .jobs import JobManager

# Twins/results live outside the package so a container can mount a volume
# over them (`CITYSIM_DATA_DIR=/data`); defaults to ./data beside the repo.
DATA_ROOT = Path(os.environ.get("CITYSIM_DATA_DIR")
                 or Path(__file__).resolve().parents[2] / "data")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="CitySim — environmental digital twin")
app.add_middleware(GZipMiddleware, minimum_size=1024)

twin_store = TwinStore(DATA_ROOT / "twins")
results_store = ResultsStore(DATA_ROOT / "results")
jobs = JobManager()
_twin_cache: dict[str, object] = {}


def _load_twin(twin_id: str):
    twin = _twin_cache.get(twin_id)
    if twin is None:
        if not twin_store.exists(twin_id):
            raise HTTPException(404, f"twin {twin_id} not found")
        twin = twin_store.load(twin_id)
        _twin_cache[twin_id] = twin
    return twin


# ------------------------------------------------------------------ hazards
@app.get("/api/hazards")
def hazards() -> list[dict]:
    return list_modules()


@app.get("/api/hazards/{name}/assumptions")
def hazard_assumptions(name: str) -> dict:
    """Every adjustable assumption, curve, citation and caveat for a hazard.

    The Setup screen renders itself from this, so a new model constant becomes
    visible and adjustable by being declared — no front-end change.
    """
    if name != "flood":
        raise HTTPException(404, f"no assumption spec for hazard {name}")
    from citysim.hazards.flood import assumptions
    return assumptions.spec()


# ------------------------------------------------------------------ twins
@app.get("/api/twin/params")
def twin_param_spec() -> dict:
    """The twin-generation parameter spec — the 'character creation' knobs."""
    return twin_params.spec()


class TwinRequest(BaseModel):
    place: str = "Hamilton (lower city)"
    # hamilton (real geometry) | synthetic (procedural) | auto (live OSM overlay)
    mode: str = "hamilton"
    params: dict = {}              # twin-generation overrides (citysim.twin.params)


@app.post("/api/twin")
def create_twin(req: TwinRequest) -> dict:
    def _build(handle):
        handle.progress(0.1, "ingesting open data / generating twin")
        twin = build_twin(place=req.place, mode=req.mode, params=req.params)
        handle.progress(0.8, "persisting twin")
        twin_store.save(twin)
        _twin_cache[twin.id] = twin
        return twin.summary()

    job_id = jobs.submit("twin", _build, meta={"place": req.place, "mode": req.mode})
    return {"job_id": job_id}


@app.get("/api/twins")
def twins() -> list[dict]:
    return twin_store.list()


@app.get("/api/twin/{twin_id}/scene")
def twin_scene(twin_id: str) -> dict:
    return scene_payload(_load_twin(twin_id))


# ------------------------------------------------------------------ simulate
class SimulateRequest(BaseModel):
    twin_id: str
    hazard: str = "flood"
    n_runs: int = 150
    seed: int = 1234
    workers: int = 4
    options: dict = {}             # e.g. {"backwater_valves": true, "climate_factor": 1.25}


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict:
    twin = _load_twin(req.twin_id)
    module = get_module(req.hazard)

    def _run(handle):
        runner = MonteCarloRunner(twin, module, workers=req.workers)
        handle.progress(0.02, f"sampling {req.n_runs} scenarios")

        def cb(done: int, n: int) -> None:
            handle.progress(0.02 + 0.93 * done / n, f"run {done}/{n}")

        output = runner.run(n=req.n_runs, seed=req.seed,
                            options=req.options, progress_cb=cb)
        handle.progress(0.97, "reducing distributions")
        results_store.save_job(handle.job_id, twin.id, output)
        return {"job_id": handle.job_id, "stats": output["stats"]}

    job_id = jobs.submit("simulate", _run,
                         meta={"twin_id": req.twin_id, "hazard": req.hazard,
                               "n_runs": req.n_runs, "options": req.options})
    return {"job_id": job_id}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.public(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.websocket("/ws/jobs/{job_id}")
async def job_ws(ws: WebSocket, job_id: str) -> None:
    import asyncio
    await ws.accept()
    try:
        last = None
        while True:
            job = jobs.public(job_id)
            if job is None:
                await ws.send_json({"error": "job not found"})
                break
            snap = (job["status"], job["progress"], job["message"])
            if snap != last:
                await ws.send_json(job)
                last = snap
            if job["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------------------------------------------------ results
@app.get("/api/results/{job_id}")
def results(job_id: str) -> dict:
    if not results_store.exists(job_id):
        raise HTTPException(404, "results not found")
    payload = results_store.load_job(job_id)
    matrix = payload.pop("building_matrix")
    payload["building_mean_loss"] = np.round(matrix.mean(axis=0), 2).tolist()
    payload["building_p95_loss"] = np.round(
        np.percentile(matrix, 95, axis=0), 2).tolist()
    # compact run index for the run picker
    payload["run_index"] = [{
        "run": r["run"],
        "total_loss": r["total_loss"],
        "per_household_mean": r["per_household_mean"],
        "return_period": s["return_period"],
        "total_mm": s["total_mm"],
        "duration_h": s["duration_h"],
    } for r, s in zip(payload["runs"], payload["scenarios"])]
    return payload


@app.get("/api/results/{job_id}/assumptions")
def results_assumptions(job_id: str) -> dict:
    """The assumption set this ensemble actually ran under.

    Read back from the stored scenarios rather than from the request, so what
    Results reports is what the solver used — including any value the API
    clamped on the way in.
    """
    if not results_store.exists(job_id):
        raise HTTPException(404, "results not found")
    payload = results_store.load_job(job_id)
    from citysim.hazards.flood import assumptions
    from citysim.hazards.flood.module import FloodModule

    scenarios = payload.get("scenarios") or []
    resolved = FloodModule.resolved(scenarios[0]) if scenarios else assumptions.DEFAULTS
    twin_p = {}
    if twin_store.exists(payload.get("twin_id", "")):
        twin_p = _load_twin(payload["twin_id"]).params or {}
    return {
        "resolved": resolved,
        "deviations": assumptions.deviations(resolved),
        "twin_params": twin_p,
        "twin_deviations": twin_params.deviations(twin_p) if twin_p else [],
        "seed": payload.get("stats", {}).get("seed"),
        "n_runs": payload.get("stats", {}).get("n_runs"),
    }


@app.get("/api/results/{job_id}/buildings/{building_id}")
def building_distribution(job_id: str, building_id: str) -> dict:
    if not results_store.exists(job_id):
        raise HTTPException(404, "results not found")
    payload = results_store.load_job(job_id)
    try:
        k = payload["building_ids"].index(building_id)
    except ValueError:
        raise HTTPException(404, "building not found")
    losses = payload["building_matrix"][:, k]
    return {
        "building_id": building_id,
        "losses": np.round(losses, 2).tolist(),
        "mean": round(float(losses.mean()), 2),
        "p95": round(float(np.percentile(losses, 95)), 2),
        "max": round(float(losses.max()), 2),
        "prob_any_damage": round(float((losses > 0).mean()), 3),
    }


@app.get("/api/results/{job_id}/runs/{run}/frames")
def run_frames(job_id: str, run: int) -> Response:
    """Binary replay payload — see `citysim.results.pack.pack_frames`."""
    if not results_store.exists(job_id):
        raise HTTPException(404, "results not found")
    payload = results_store.load_job(job_id)
    if not (0 <= run < len(payload["scenarios"])):
        raise HTTPException(404, "run not found")

    cached = results_store.load_frames(job_id, run)
    if cached is None:
        twin = _load_twin(payload["twin_id"])
        module = get_module(payload["stats"]["hazard"])
        scenario = payload["scenarios"][run]
        sim = module.simulate(twin, scenario, record_frames=True)
        results_store.save_frames(job_id, run, sim.frames,
                                  extra={"series": sim.series,
                                         "meta": sim.meta})
        cached = results_store.load_frames(job_id, run)

    blob = pack_frames(
        depth=cached["depth"], t=cached["t"], rain_mmh=cached["rain"],
        surcharging=cached["surcharging"],
        scenario=payload["scenarios"][run], run_summary=payload["runs"][run],
    )
    return Response(content=blob, media_type="application/octet-stream")


# ------------------------------------------------------------------ static SPA
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
