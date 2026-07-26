"""Flood impact/cost engine (plan §5.8): depth → depth-damage curve → $.

The pattern is hazard-agnostic (intensity → vulnerability curve → % loss ×
value); only the intensity variable (water depth) and the curve library are
flood-specific.

The curves themselves live in ``assumptions.py`` alongside every other model
constant, so the Setup screen can plot exactly the numbers this file
interpolates rather than a hand-copied duplicate.

Curves are piecewise-linear fractions of value vs depth, shaped after the
southern-Ontario depth-damage-curve literature (Paragon/EC/OMNR base set and
the IBI/Hatch synthetic curves): separate basement and main-floor curves,
masonry vs wood-frame foundations, structure vs contents. Magnitudes are
calibrated so a typical basement-backup event lands in the tens of thousands
of dollars — the published Ontario average (~$40–45k/event).

Probabilistic DDCs (plan §3.6): each Monte-Carlo run draws a lognormal scale
factor applied to the curves, so cost uncertainty propagates into the output
distribution instead of collapsing to a point estimate.

Three damage mechanisms per building:
  1. sewer backup   — combined-sewer node HGL above the basement floor
  2. basement inundation — surface water deep enough to enter window wells/sills
  3. main-floor inundation — surface depth above the first-floor height
"""

from __future__ import annotations

import numpy as np

from .assumptions import (
    BASEMENT_CONTENTS,
    BASEMENT_STRUCT,
    DEFAULTS,
    MAIN_CONTENTS,
    MAIN_STRUCT,
)

# Constants re-exported for readers who arrive here first; the values (and the
# ranges a user may move them over) are declared in assumptions.py.
#
# depth of standing water at a building needed before it enters the basement
# (window wells / low sills; below this, foundation drainage keeps up)
SILL_DEPTH = DEFAULTS["sill_depth"]
# static (trap/lateral) head loss before backup reaches the basement floor
BACKUP_FREEBOARD = DEFAULTS["backup_freeboard"]
# head-to-depth attenuation along the service lateral: friction losses and the
# finite surcharge duration keep the basement below full HGL equilibrium
LATERAL_FACTOR = DEFAULTS["lateral_factor"]


def _interp(curve: list[tuple[float, float]], depth: float) -> float:
    if depth <= 0.0:
        return 0.0
    xs, ys = zip(*curve)
    return float(np.interp(depth, xs, ys))


def building_loss(b, surface_depth: float, backup_head: float | None,
                  ddc_factor: float = 1.0, params: dict | None = None) -> dict:
    """Loss for one building in one run.

    surface_depth : max overland water depth beside the building (m)
    backup_head   : peak sewer HGL elevation at its combined node (m ASL), or
                    None if not on a combined system
    ddc_factor    : probabilistic DDC scale draw
    params        : resolved assumption set (``assumptions.resolve``); defaults
                    when omitted, so every existing caller keeps working
    """
    p = params or DEFAULTS
    sill = p.get("sill_depth", SILL_DEPTH)
    freeboard = p.get("backup_freeboard", BACKUP_FREEBOARD)
    lateral = p.get("lateral_factor", LATERAL_FACTOR)
    severity = p.get("ddc_severity", 1.0)

    structure = 0.0
    contents = 0.0
    mechanisms = []

    mat = b.material if b.material in BASEMENT_STRUCT else "masonry"

    if b.has_basement:
        basement_h = b.basement_depth
        backup_depth = 0.0
        if backup_head is not None:
            backup_depth = np.clip(
                (backup_head - freeboard - b.basement_floor_elev) * lateral,
                0.0, basement_h + 0.3)
        inund_depth = 0.0
        if surface_depth > sill:
            inund_depth = min(surface_depth, basement_h)
        depth_b = float(max(backup_depth, inund_depth))
        if depth_b > 0.0:
            structure += _interp(BASEMENT_STRUCT[mat], depth_b) * b.structure_value
            contents += _interp(BASEMENT_CONTENTS, depth_b) * b.contents_value
            mechanisms.append("sewer_backup" if backup_depth >= inund_depth else "basement_inundation")

    over_floor = surface_depth - b.first_floor_height
    if over_floor > 0.0:
        structure += _interp(MAIN_STRUCT, over_floor) * b.structure_value
        contents += _interp(MAIN_CONTENTS, over_floor) * b.contents_value
        mechanisms.append("overland_flooding")

    scale = ddc_factor * severity
    structure *= scale
    contents *= scale
    return {
        "structure_loss": round(structure, 2),
        "contents_loss": round(contents, 2),
        "total": round(structure + contents, 2),
        "mechanisms": mechanisms,
    }
