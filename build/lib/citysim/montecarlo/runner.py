"""Monte-Carlo batch runner (plan §5.7) — hazard-agnostic.

Runs N scenario draws through whichever hazard module is selected, in
parallel worker processes (runs are embarrassingly parallel), and reduces the
results to the distribution outputs the plan asks for: per-household stats,
exceedance-probability curves, VaR/CVaR tail measures, and per-building
expected losses.

Storage strategy: per-run we keep the sampled parameter vector and losses —
*not* the full depth-grid time-series. Because every run is seeded and
deterministic, any run can be re-simulated exactly for replay in a second or
two; the web tier does that on demand and caches the frames. Same user-facing
behaviour as storing every frame of every run, at a small fraction of the disk.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from typing import Callable

import numpy as np

from citysim.hazards.contract import HazardModule
from citysim.twin.schema import Twin

_WORKER: dict = {}


def _init_worker(twin: Twin, module_name: str) -> None:
    from citysim.hazards import get_module
    _WORKER["twin"] = twin
    _WORKER["module"] = get_module(module_name)


def _run_one(scenario: dict) -> dict:
    twin: Twin = _WORKER["twin"]
    module: HazardModule = _WORKER["module"]
    sim = module.simulate(twin, scenario, record_frames=False)
    impact = module.assess_impact(twin, sim, scenario)
    b_ids = [b.id for b in twin.buildings]
    return {
        "run": scenario["run"],
        "total_loss": impact.total_loss,
        "residential_loss": impact.meta.get("residential_loss", impact.total_loss),
        "per_household_mean": impact.per_household_mean,
        "building_losses": [impact.per_building[bid]["total"] for bid in b_ids],
        "mechanism_counts": _mechanism_counts(impact),
        "surcharged_nodes": sum(1 for v in sim.node_state.values() if v.get("surcharged")),
        "peak_depth": round(float(sim.max_intensity.max()), 3),
        "flooded_area_m2": round(float((sim.max_intensity > 0.05).sum())
                                 * twin.terrain.cell_size ** 2, 1),
    }


def _mechanism_counts(impact) -> dict:
    counts: dict[str, int] = {}
    for loss in impact.per_building.values():
        for m in loss.get("mechanisms", []):
            counts[m] = counts.get(m, 0) + 1
    return counts


class MonteCarloRunner:
    def __init__(self, twin: Twin, module: HazardModule, workers: int = 4):
        self.twin = twin
        self.module = module
        self.workers = workers

    def run(self, n: int = 200, seed: int = 1234, options: dict | None = None,
            progress_cb: Callable[[int, int], None] | None = None) -> dict:
        scenarios = self.module.sample_scenarios(self.twin, n, seed, options)
        t0 = time.time()
        results: list[dict | None] = [None] * n

        ctx = get_context("fork")
        with ProcessPoolExecutor(max_workers=self.workers, mp_context=ctx,
                                 initializer=_init_worker,
                                 initargs=(self.twin, self.module.name)) as pool:
            futures = {pool.submit(_run_one, s): s["run"] for s in scenarios}
            done = 0
            for fut in as_completed(futures):
                r = fut.result()
                results[r["run"]] = r
                done += 1
                if progress_cb:
                    progress_cb(done, n)

        return self.reduce(scenarios, results, seed, options,
                           elapsed_s=time.time() - t0)

    # ---------------------------------------------------------------- reduce
    def reduce(self, scenarios: list[dict], results: list[dict], seed: int,
               options: dict | None, elapsed_s: float) -> dict:
        hh_loss = np.array([r["per_household_mean"] for r in results])
        totals = np.array([r["total_loss"] for r in results])
        building_matrix = np.array([r["building_losses"] for r in results])
        n = len(results)

        order = np.argsort(totals)

        def run_at_quantile(q: float) -> int:
            return int(order[min(int(q * (n - 1)), n - 1)])

        # empirical annual exceedance: each run samples the year's worst storm
        sorted_hh = np.sort(hh_loss)[::-1]
        exceed_prob = (np.arange(n) + 1) / (n + 1)
        exceedance = [{"loss": round(float(l), 2), "prob": round(float(p), 4)}
                      for l, p in zip(sorted_hh.tolist(), exceed_prob.tolist())]

        var95 = float(np.percentile(hh_loss, 95))
        tail = hh_loss[hh_loss >= var95]
        mech_totals: dict[str, int] = {}
        for r in results:
            for m, c in r["mechanism_counts"].items():
                mech_totals[m] = mech_totals.get(m, 0) + c

        stats = {
            "n_runs": n,
            "seed": seed,
            "options": options or {},
            "elapsed_s": round(elapsed_s, 1),
            "hazard": self.module.name,
            "per_household": {
                "mean": round(float(hh_loss.mean()), 2),
                "median": round(float(np.median(hh_loss)), 2),
                "p90": round(float(np.percentile(hh_loss, 90)), 2),
                "p95": round(var95, 2),
                "p99": round(float(np.percentile(hh_loss, 99)), 2),
                "max": round(float(hh_loss.max()), 2),
                "var95": round(var95, 2),
                "cvar95": round(float(tail.mean()) if len(tail) else var95, 2),
            },
            "total": {
                "mean": round(float(totals.mean()), 2),
                "median": round(float(np.median(totals)), 2),
                "p95": round(float(np.percentile(totals, 95)), 2),
                "max": round(float(totals.max()), 2),
            },
            "mechanism_counts": mech_totals,
            "representative_runs": {
                "median": run_at_quantile(0.50),
                "p90": run_at_quantile(0.90),
                "p95": run_at_quantile(0.95),
                "p99": run_at_quantile(0.99),
                "max": int(order[-1]),
            },
        }
        return {
            "stats": stats,
            "scenarios": scenarios,
            "runs": results,
            "exceedance": exceedance,
            "building_matrix": building_matrix,
            "building_ids": [b.id for b in self.twin.buildings],
        }
