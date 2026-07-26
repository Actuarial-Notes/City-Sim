"""Coupled 1D/2D flood engine (plan §5.6): SWMM-style network ⇅ 2D surface.

The coupling points are manholes/catchbasins: the surface drains into the
network through inlet capacity, and surcharged volume is injected back onto
the grid at the manhole cell — bidirectional exchange each step, which is the
standard loose-coupling scheme in the urban-flood literature.

Emits replay frames (depth grids + node states) at a fixed cadence so the
viewer can scrub the storm without re-simulating.
"""

from __future__ import annotations

import numpy as np

from citysim.twin.schema import Twin
from .assumptions import DEFAULTS as ASSUMPTION_DEFAULTS
from .surface2d import Surface2D
from .sewer1d import Sewer1D


def prepare_masks(twin: Twin, lateral_reach_m: float = 90.0) -> dict:
    """Static grid-side precomputation shared by every run on a twin.

    ``lateral_reach_m`` is how far a building can be from a combined-sewer node
    and still be on the backup pathway. It changes the mask, so masks are cached
    per (twin, reach) rather than per twin.
    """
    ny, nx = twin.terrain.shape
    cell = twin.terrain.cell_size

    building_mask = twin.landcover.classes == 2

    node_cells = []
    for n in twin.sewer.nodes:
        j = int(np.clip(round(n.x / cell), 0, nx - 1))
        i = int(np.clip(round(n.y / cell), 0, ny - 1))
        node_cells.append((i, j))

    # sampling ring around each footprint (building cells themselves are raised)
    bcells: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for b in twin.buildings:
        xs = [p[0] for p in b.footprint]
        ys = [p[1] for p in b.footprint]
        j0 = int(np.clip(min(xs) / cell - 1, 0, nx - 1))
        j1 = int(np.clip(np.ceil(max(xs) / cell) + 1, 1, nx))
        i0 = int(np.clip(min(ys) / cell - 1, 0, ny - 1))
        i1 = int(np.clip(np.ceil(max(ys) / cell) + 1, 1, ny))
        ring = np.zeros((ny, nx), dtype=bool)
        ring[i0:i1, j0:j1] = True
        ring &= ~building_mask
        bcells[b.id] = np.where(ring)

    # nearest combined-sewer node within reach of each building (backup pathway)
    combined = [(k, n) for k, n in enumerate(twin.sewer.nodes) if n.system == "combined"]
    backup_node: dict[str, int | None] = {}
    for b in twin.buildings:
        best, best_d = None, float(lateral_reach_m)   # service-lateral reach, metres
        for k, n in combined:
            d = float(np.hypot(n.x - b.centroid[0], n.y - b.centroid[1]))
            if d < best_d:
                best, best_d = k, d
        backup_node[b.id] = best

    return {"building_mask": building_mask, "node_cells": node_cells,
            "building_cells": bcells, "backup_node": backup_node}


def run_coupled(twin: Twin, hyetograph_mmh: np.ndarray, hyeto_dt_s: float,
                masks: dict | None = None, blockage_factor: float = 1.0,
                infil_factor: float = 1.0, manning_factor: float = 1.0,
                drain_time_s: float | None = None, record_frames: bool = False,
                frame_dt_s: float | None = None, params: dict | None = None) -> dict:
    """One deterministic coupled simulation.

    infil_factor scales infiltration capacity (antecedent-moisture draw);
    manning_factor scales surface roughness; blockage_factor derates pipes.

    ``params`` is a resolved assumption set (``flood.assumptions.resolve``);
    the solver-group knobs are read from it. Explicit ``drain_time_s`` /
    ``frame_dt_s`` arguments still win, so existing callers are unaffected.
    """
    p = params or ASSUMPTION_DEFAULTS
    if drain_time_s is None:
        drain_time_s = p.get("drain_time_s", ASSUMPTION_DEFAULTS["drain_time_s"])
    if frame_dt_s is None:
        frame_dt_s = p.get("frame_dt_s", ASSUMPTION_DEFAULTS["frame_dt_s"])

    if masks is None:
        masks = prepare_masks(
            twin, lateral_reach_m=p.get("lateral_reach_m",
                                        ASSUMPTION_DEFAULTS["lateral_reach_m"]))

    surface = Surface2D(
        dem=twin.terrain.dtm,
        cell_size=twin.terrain.cell_size,
        manning_n=twin.landcover.manning_n * manning_factor,
        infiltration_mmh=twin.landcover.infiltration * infil_factor,
        imperviousness=twin.landcover.imperviousness,
        building_mask=masks["building_mask"],
        alpha=p.get("cfl_alpha", ASSUMPTION_DEFAULTS["cfl_alpha"]),
        building_raise=p.get("building_raise", ASSUMPTION_DEFAULTS["building_raise"]),
    )
    sewer = Sewer1D(
        twin.sewer, blockage_factor=blockage_factor,
        chamber_area=p.get("chamber_area", ASSUMPTION_DEFAULTS["chamber_area"]),
        dwf_per_node=p.get("dwf_per_node", ASSUMPTION_DEFAULTS["dwf_per_node"]),
    )

    node_cells = masks["node_cells"]
    n_nodes = len(node_cells)
    cell_area = twin.terrain.cell_size ** 2
    # node → grid cell as index arrays, so the exchange is two scatter/gathers
    # rather than a Python loop over every node on every step
    node_i = np.array([c[0] for c in node_cells], dtype=np.intp)
    node_j = np.array([c[1] for c in node_cells], dtype=np.intp)
    node_idx = (node_i, node_j)
    capturing = ~sewer.is_outfall

    # The 1D network is stepped on its own, coarser clock. The surface timestep
    # is CFL-limited and can fall below a second, but the sewer is a storage
    # model with no such constraint — running it at every surface step is pure
    # overhead. Inflow accumulates between sewer steps so no volume is lost.
    coupling_dt = float(p.get("coupling_dt_s", ASSUMPTION_DEFAULTS["coupling_dt_s"]))

    t_storm = len(hyetograph_mmh) * hyeto_dt_s
    t_end = t_storm + drain_time_s
    t = 0.0
    max_depth = np.zeros_like(surface.h)
    frames: list[dict] = []
    next_frame = 0.0
    surcharging = np.zeros(n_nodes, dtype=bool)
    surcharging_prev = np.zeros(n_nodes, dtype=bool)
    pending_volume = np.zeros(n_nodes)     # m³ captured since the last sewer step
    pending_dt = 0.0
    surcharge_q = np.zeros(n_nodes)        # m³/s still being pushed back out

    while t < t_end:
        dt = min(surface.stable_dt(), t_end - t)
        k = min(int(t / hyeto_dt_s), len(hyetograph_mmh) - 1)
        rain_ms = (hyetograph_mmh[k] / 1000.0 / 3600.0) if t < t_storm else 0.0

        # surface → sewer: catchbasin intake limited by grate capacity & water present
        h_at_node = surface.h[node_idx]
        capture = np.where(capturing & (h_at_node > 1e-3),
                           np.minimum(sewer.max_inflow, h_at_node * cell_area / dt),
                           0.0)
        pending_volume += capture * dt
        pending_dt += dt

        # sewer → surface: surcharge volume injected at manhole cells
        if pending_dt >= coupling_dt or t + dt >= t_end:
            excess = sewer.step(pending_dt, pending_volume / pending_dt)
            surcharging = excess > 1e-6
            surcharge_q = excess / pending_dt
            pending_volume[:] = 0.0
            pending_dt = 0.0

        net_q = surcharge_q - capture
        surface.step(dt, rain_ms=rain_ms, source_idx=node_idx, source_q=net_q)
        # open boundary at the north edge (the harbour): water leaves the domain
        surface.h[-1, :] = 0.0

        np.maximum(max_depth, surface.h, out=max_depth)

        if record_frames and t >= next_frame:
            frames.append({
                "t": round(t, 1),
                "depth": surface.h.astype(np.float16),
                "rain_mmh": float(hyetograph_mmh[k]) if t < t_storm else 0.0,
                "surcharging": np.where(surcharging | surcharging_prev)[0].tolist(),
                "node_head": np.round(sewer.head() - sewer.invert, 2).astype(np.float32),
            })
            next_frame += frame_dt_s
        surcharging_prev = surcharging
        t += dt

    if record_frames:
        frames.append({
            "t": round(t, 1), "depth": surface.h.astype(np.float16),
            "rain_mmh": 0.0, "surcharging": [],
            "node_head": np.round(sewer.head() - sewer.invert, 2).astype(np.float32),
        })

    return {
        "max_depth": max_depth,
        "node_max_head": sewer.max_head,
        "frames": frames if record_frames else None,
        "outfall_volume": sewer.outfall_volume,
        "infiltrated_volume": surface.infiltrated,
    }
