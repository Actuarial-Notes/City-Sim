"""Tier 1 — the web application's FastAPI back end.

Endpoints (all hazard-agnostic; the hazard registry drives the picker):

  GET  /api/hazards                       available + planned hazard modules
  POST /api/twin                          build a twin for a place (job)
  GET  /api/twins                         stored twins
  GET  /api/twin/{twin_id}/scene          3D scene payload for the viewer
  POST /api/simulate                      launch a Monte-Carlo batch (job)
  GET  /api/jobs/{job_id}                 job status/progress (poll)
  WS   /ws/jobs/{job_id}                  job progress stream
  GET  /api/results/{job_id}              distributions, exceedance, per-building $
  GET  /api/results/{job_id}/runs/{run}/frames   replay frames (binary)

Replay frames are re-simulated deterministically from the run's stored
scenario on first request and cached — plan §5.9's "playback of stored
frames", implemented with derived-data caching instead of storing every grid
of every run.
"""

from __future__ import annotations

import json
import struct
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
from citysim.results import ResultsStore
from citysim.twin import build_twin
from citysim.twin.store import TwinStore
from .jobs import JobManager

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
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


# ------------------------------------------------------------------ twins
class TwinRequest(BaseModel):
    place: str = "Hamilton demo (lower city)"
    mode: str = "synthetic"        # synthetic | auto (auto tries live OSM data)


@app.post("/api/twin")
def create_twin(req: TwinRequest) -> dict:
    def _build(handle):
        handle.progress(0.1, "ingesting open data / generating twin")
        twin = build_twin(place=req.place, mode=req.mode)
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
    twin = _load_twin(twin_id)
    t = twin.terrain
    return {
        "summary": twin.summary(),
        "cell_size": t.cell_size,
        "shape": list(t.shape),
        "dtm": np.round(t.dtm, 2).flatten().tolist(),
        "landcover": twin.landcover.classes.flatten().astype(int).tolist(),
        "buildings": [{
            "id": b.id, "footprint": b.footprint, "height": round(b.height, 1),
            "ground_elev": round(b.ground_elev, 2), "use": b.use,
            "material": b.material, "year_built": b.year_built,
            "storeys": b.storeys, "has_basement": b.has_basement,
            "dwelling_units": b.dwelling_units,
            "structure_value": b.structure_value,
        } for b in twin.buildings],
        "sewer": {
            "nodes": [{"id": n.id, "x": n.x, "y": n.y, "kind": n.kind,
                       "system": n.system, "rim_elev": round(n.rim_elev, 2)}
                      for n in twin.sewer.nodes],
            "conduits": [{"from": c.from_node, "to": c.to_node,
                          "diameter": c.diameter, "system": c.system}
                         for c in twin.sewer.conduits],
        },
    }


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
    """Binary replay payload:  [uint32 header_len][header JSON][uint16 depth mm].

    Depth grids are quantised to millimetres (uint16) — visually lossless for
    a depth overlay and 4× smaller than float64 JSON before gzip.
    """
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

    depth = np.clip(cached["depth"].astype(np.float32) * 1000.0, 0, 65535).astype("<u2")
    nf, ny, nx = depth.shape
    header = json.dumps({
        "n_frames": int(nf), "ny": int(ny), "nx": int(nx),
        "t": np.asarray(cached["t"]).round(1).tolist(),
        "rain_mmh": np.asarray(cached["rain"]).round(2).tolist(),
        "surcharging": cached["surcharging"],
        "scenario": payload["scenarios"][run],
        "run_summary": payload["runs"][run],
    }).encode()
    blob = struct.pack("<I", len(header)) + header + depth.tobytes()
    return Response(content=blob, media_type="application/octet-stream")


# ------------------------------------------------------------------ static SPA
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
