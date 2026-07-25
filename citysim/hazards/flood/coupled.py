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
from .surface2d import Surface2D
from .sewer1d import Sewer1D


def prepare_masks(twin: Twin) -> dict:
    """Static grid-side precomputation shared by every run on a twin."""
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
        best, best_d = None, 90.0        # service-lateral reach, metres
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
                drain_time_s: float = 2400.0, record_frames: bool = False,
                frame_dt_s: float = 120.0) -> dict:
    """One deterministic coupled simulation.

    infil_factor scales infiltration capacity (antecedent-moisture draw);
    manning_factor scales surface roughness; blockage_factor derates pipes.
    """
    if masks is None:
        masks = prepare_masks(twin)

    surface = Surface2D(
        dem=twin.terrain.dtm,
        cell_size=twin.terrain.cell_size,
        manning_n=twin.landcover.manning_n * manning_factor,
        infiltration_mmh=twin.landcover.infiltration * infil_factor,
        imperviousness=twin.landcover.imperviousness,
        building_mask=masks["building_mask"],
    )
    sewer = Sewer1D(twin.sewer, blockage_factor=blockage_factor)

    node_cells = masks["node_cells"]
    n_nodes = len(node_cells)
    cell_area = twin.terrain.cell_size ** 2

    t_storm = len(hyetograph_mmh) * hyeto_dt_s
    t_end = t_storm + drain_time_s
    t = 0.0
    max_depth = np.zeros_like(surface.h)
    frames: list[dict] = []
    next_frame = 0.0
    surcharging_prev = np.zeros(n_nodes, dtype=bool)

    while t < t_end:
        dt = min(surface.stable_dt(), t_end - t)
        k = min(int(t / hyeto_dt_s), len(hyetograph_mmh) - 1)
        rain_ms = (hyetograph_mmh[k] / 1000.0 / 3600.0) if t < t_storm else 0.0

        # surface → sewer: catchbasin intake limited by grate capacity & water present
        inflow = np.zeros(n_nodes)
        sinks: list[tuple[int, int, float]] = []
        for ni, (i, j) in enumerate(node_cells):
            if sewer.is_outfall[ni]:
                continue
            h = surface.h[i, j]
            if h > 1e-3:
                q = min(sewer.max_inflow[ni], h * cell_area / dt)
                inflow[ni] = q
                sinks.append((i, j, -q))

        # sewer → surface: surcharge volume injected at manhole cells
        excess = sewer.step(dt, inflow)
        sources = sinks
        surcharging = excess > 1e-6
        for ni in np.where(surcharging)[0]:
            i, j = node_cells[ni]
            sources.append((i, j, excess[ni] / dt))

        surface.step(dt, rain_ms=rain_ms, point_sources=sources)
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
