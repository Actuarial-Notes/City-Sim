"""The flood hazard module — first concrete implementation of the contract."""

from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np

from citysim.montecarlo.lhs import latin_hypercube
from citysim.twin.schema import Twin
from ..contract import HazardModule, ImpactResult, SimResult
from ..registry import register
from . import assumptions, rainfall
from .coupled import prepare_masks, run_coupled

# Key under which the resolved assumption set travels inside a scenario's
# ``options``. Scenarios are already pickled out to every Monte-Carlo worker, so
# riding along here means no new plumbing through the runner.
RESOLVED_KEY = "_resolved"


@lru_cache(maxsize=8192)
def _valve_draw(building_id: str) -> float:
    """A stable [0, 1) draw per building.

    ``hash()`` is salted per process, and these runs fan out over a process
    pool — so the draw has to come from a real digest to stay reproducible.
    """
    digest = hashlib.sha256(f"valve:{building_id}".encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF


def _has_valve(building_id: str, adoption: float) -> bool:
    if adoption >= 1.0:
        return True
    if adoption <= 0.0:
        return False
    return _valve_draw(building_id) < adoption


@register
class FloodModule(HazardModule):
    name = "flood"
    display_name = "Flood (extreme rainfall / sewer backup)"
    param_names = [
        "u_return", "duration_h", "peak_frac", "infil_factor",
        "manning_factor", "blockage_factor", "ddc_factor",
    ]

    def __init__(self) -> None:
        self._mask_cache: dict[tuple[str, float], dict] = {}

    # ------------------------------------------------------------ sampling
    def sample_scenarios(self, twin: Twin, n: int, seed: int,
                         options: dict | None = None) -> list[dict]:
        options = options or {}
        p = assumptions.resolve(options)
        climate_factor = p["climate_factor"]
        u = latin_hypercube(n, len(self.param_names), seed)

        dur_lo, dur_span = p["duration_min_h"], p["duration_max_h"] - p["duration_min_h"]
        peak_lo, peak_span = p["peak_frac_min"], p["peak_frac_max"] - p["peak_frac_min"]
        infil_lo, infil_span = p["infil_min"], p["infil_max"] - p["infil_min"]
        man_lo, man_span = p["manning_min"], p["manning_max"] - p["manning_min"]
        blk_lo, blk_span = p["blockage_min"], p["blockage_max"] - p["blockage_min"]
        t_max = p["return_period_cap"]

        # the resolved set is identical for every run — build the stored options
        # dict once rather than per scenario
        stored_options = {k_: v for k_, v in options.items() if k_ != RESOLVED_KEY}
        stored_options[RESOLVED_KEY] = p

        scenarios = []
        for k in range(n):
            uk = u[k]
            T = rainfall.sample_return_period(uk[0], t_max=t_max)
            duration_h = float(dur_lo + uk[1] * dur_span)
            peak_frac = float(peak_lo + uk[2] * peak_span)           # front→back loaded
            total_mm = rainfall.storm_depth(
                T, duration_h, climate_factor,
                idf_scale=p["idf_scale"], decay_c=p["idf_decay_c"], t_max=t_max)
            # antecedent moisture: wet catchment → little infiltration capacity left
            infil_factor = float(infil_lo + uk[3] * infil_span)
            manning_factor = float(man_lo + uk[4] * man_span)
            blockage_factor = float(blk_lo + uk[5] * blk_span)
            # probabilistic DDC draw: symmetric lognormal-style envelope,
            # median 1 — σ = 0.9 gives roughly 0.64×–1.57×, the PDDC band
            ddc_factor = float(np.exp((uk[6] - 0.5) * p["ddc_sigma"]))
            scenarios.append({
                "run": k,
                "seed": int(seed) * 100_000 + k,
                "return_period": round(T, 2),
                "duration_h": round(duration_h, 3),
                "peak_frac": round(peak_frac, 3),
                "total_mm": round(total_mm, 2),
                "climate_factor": climate_factor,
                "infil_factor": round(infil_factor, 3),
                "manning_factor": round(manning_factor, 3),
                "blockage_factor": round(blockage_factor, 3),
                "ddc_factor": round(ddc_factor, 3),
                "options": stored_options,
            })
        return scenarios

    @staticmethod
    def resolved(scenario: dict) -> dict:
        """The assumption set a scenario was sampled under.

        Scenarios stored before V2 have no resolved set; falling back to
        defaults keeps old jobs replayable instead of erroring on load.
        """
        opts = scenario.get("options") or {}
        return opts.get(RESOLVED_KEY) or assumptions.resolve(opts)

    # ------------------------------------------------------------ physics
    def simulate(self, twin: Twin, scenario: dict,
                 record_frames: bool = False) -> SimResult:
        p = self.resolved(scenario)
        # the backup-pathway mask depends on the lateral reach, so it is part of
        # the cache key — otherwise a second ensemble with a different reach
        # would silently reuse the first one's mask
        key = (twin.id, float(p["lateral_reach_m"]))
        masks = self._mask_cache.get(key)
        if masks is None:
            masks = prepare_masks(twin, lateral_reach_m=p["lateral_reach_m"])
            self._mask_cache[key] = masks

        hyeto = rainfall.chicago_hyetograph(
            scenario["total_mm"], scenario["duration_h"], scenario["peak_frac"],
            decay_c=p["idf_decay_c"])
        out = run_coupled(
            twin, hyeto, hyeto_dt_s=60.0, masks=masks,
            blockage_factor=scenario["blockage_factor"],
            infil_factor=scenario["infil_factor"],
            manning_factor=scenario["manning_factor"],
            record_frames=record_frames,
            params=p,
        )

        node_max_head = out["node_max_head"]
        building_intensity: dict[str, dict] = {}
        for b in twin.buildings:
            cells = masks["building_cells"][b.id]
            # mean over the footprint's perimeter ring: water standing *against*
            # the building, not the deepest puddle somewhere nearby
            surface_depth = float(out["max_depth"][cells].mean()) if len(cells[0]) else 0.0
            nb = masks["backup_node"][b.id]
            building_intensity[b.id] = {
                "surface_depth": round(surface_depth, 3),
                "backup_head": round(float(node_max_head[nb]), 3) if nb is not None else None,
            }

        node_state = {
            twin.sewer.nodes[i].id: {"max_head": round(float(h), 3),
                                     "surcharged": bool(h >= twin.sewer.nodes[i].rim_elev - 1e-6)}
            for i, h in enumerate(node_max_head)
        }
        return SimResult(
            max_intensity=out["max_depth"],
            building_intensity=building_intensity,
            node_state=node_state,
            frames=out["frames"],
            series={"hyetograph_mmh": np.round(hyeto, 3).tolist(), "hyeto_dt_s": 60.0},
            meta={"outfall_volume_m3": round(out["outfall_volume"], 1),
                  "infiltrated_volume_m3": round(out["infiltrated_volume"], 1)},
        )

    # ------------------------------------------------------------ impact
    def assess_impact(self, twin: Twin, sim: SimResult, scenario: dict) -> ImpactResult:
        from .damage import building_loss

        p = self.resolved(scenario)
        backwater_valves = bool(p.get("backwater_valves", False))
        adoption = float(p.get("valve_adoption", 1.0))
        effectiveness = float(p.get("valve_effectiveness", 1.0))
        ddc = scenario["ddc_factor"]

        per_building: dict[str, dict] = {}
        total = 0.0
        residential_loss = 0.0
        for b in twin.buildings:
            inten = sim.building_intensity[b.id]
            backup_head = inten["backup_head"]
            # Mitigation: a backwater valve severs the backup pathway. Adoption is
            # never universal and valves are not perfect, so it is a rate times an
            # effectiveness rather than a switch. Which buildings have one is
            # decided by a stable hash of the building id, so the choice is
            # identical across runs, processes and machines.
            if (backwater_valves and backup_head is not None
                    and b.use == "residential" and _has_valve(b.id, adoption)):
                if effectiveness >= 1.0:
                    backup_head = None
                else:
                    # a partly effective valve blocks that fraction of the head
                    # standing above the basement floor
                    floor = b.basement_floor_elev
                    backup_head = floor + (backup_head - floor) * (1.0 - effectiveness)
            loss = building_loss(b, inten["surface_depth"], backup_head,
                                 ddc_factor=ddc, params=p)
            per_building[b.id] = loss
            total += loss["total"]
            if b.use == "residential":
                residential_loss += loss["total"]

        hh = max(twin.n_households, 1)
        return ImpactResult(
            per_building=per_building,
            total_loss=round(total, 2),
            n_households=hh,
            per_household_mean=round(residential_loss / hh, 2),
            meta={"residential_loss": round(residential_loss, 2)},
        )
