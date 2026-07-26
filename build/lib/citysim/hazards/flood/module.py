"""The flood hazard module — first concrete implementation of the contract."""

from __future__ import annotations

import numpy as np

from citysim.montecarlo.lhs import latin_hypercube
from citysim.twin.schema import Twin
from ..contract import HazardModule, ImpactResult, SimResult
from ..registry import register
from . import rainfall
from .coupled import prepare_masks, run_coupled


@register
class FloodModule(HazardModule):
    name = "flood"
    display_name = "Flood (extreme rainfall / sewer backup)"
    param_names = [
        "u_return", "duration_h", "peak_frac", "infil_factor",
        "manning_factor", "blockage_factor", "ddc_factor",
    ]

    def __init__(self) -> None:
        self._mask_cache: dict[str, dict] = {}

    # ------------------------------------------------------------ sampling
    def sample_scenarios(self, twin: Twin, n: int, seed: int,
                         options: dict | None = None) -> list[dict]:
        options = options or {}
        climate_factor = float(options.get("climate_factor", 1.0))
        u = latin_hypercube(n, len(self.param_names), seed)

        scenarios = []
        for k in range(n):
            uk = u[k]
            T = rainfall.sample_return_period(uk[0])
            duration_h = float(1.0 + uk[1] * 5.0)                    # 1–6 h storms
            peak_frac = float(0.15 + uk[2] * 0.70)                   # front→back loaded
            total_mm = rainfall.storm_depth(T, duration_h, climate_factor)
            # antecedent moisture: wet catchment → little infiltration capacity left
            infil_factor = float(0.30 + uk[3] * 1.00)                # 0.3–1.3
            manning_factor = float(0.80 + uk[4] * 0.45)              # 0.8–1.25
            blockage_factor = float(0.60 + uk[5] * 0.40)             # 0.6–1.0
            # probabilistic DDC draw: symmetric lognormal-style envelope,
            # median 1, roughly 0.64×–1.57× — the PDDC uncertainty band
            ddc_factor = float(np.exp((uk[6] - 0.5) * 0.9))
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
                "options": {k_: v for k_, v in options.items()},
            })
        return scenarios

    # ------------------------------------------------------------ physics
    def simulate(self, twin: Twin, scenario: dict,
                 record_frames: bool = False) -> SimResult:
        masks = self._mask_cache.get(twin.id)
        if masks is None:
            masks = prepare_masks(twin)
            self._mask_cache[twin.id] = masks

        hyeto = rainfall.chicago_hyetograph(
            scenario["total_mm"], scenario["duration_h"], scenario["peak_frac"])
        out = run_coupled(
            twin, hyeto, hyeto_dt_s=60.0, masks=masks,
            blockage_factor=scenario["blockage_factor"],
            infil_factor=scenario["infil_factor"],
            manning_factor=scenario["manning_factor"],
            record_frames=record_frames,
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

        opts = scenario.get("options", {})
        backwater_valves = bool(opts.get("backwater_valves", False))
        ddc = scenario["ddc_factor"]

        per_building: dict[str, dict] = {}
        total = 0.0
        residential_loss = 0.0
        for b in twin.buildings:
            inten = sim.building_intensity[b.id]
            backup_head = inten["backup_head"]
            # mitigation scenario: backwater valves cut off the backup pathway
            if backwater_valves and b.use == "residential":
                backup_head = None
            loss = building_loss(b, inten["surface_depth"], backup_head, ddc_factor=ddc)
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
