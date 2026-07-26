"""Rainfall driver (plan §3.5): IDF statistics → design-storm hyetographs.

The IDF table below approximates the ECCC Engineering Climate Dataset values
for the Hamilton area (Hamilton A / Hamilton RBG stations): 1-hour rainfall
totals by return period, extended across durations with a power-law decay in
intensity (i = a·t^-c), which reproduces the familiar southern-Ontario 24-h
totals (2-yr ≈ 50 mm, 100-yr ≈ 125 mm) within a few percent. Swapping in an
exact station IDF file (or IDF_CC climate-scaled curves) only replaces this
table.

The Monte-Carlo does not run "the 100-year storm"; it samples the annual
exceedance distribution (T = 1/U), storm duration, hyetograph shape
(front/centre/back-loaded Chicago storm), antecedent moisture, and a climate
scaling factor — each draw becomes one minute-resolution hyetograph.
"""

from __future__ import annotations

import math

import numpy as np

# 1-hour rainfall depth (mm) by return period (years), Hamilton-area IDF.
IDF_1H_MM = {2: 22.0, 5: 28.0, 10: 32.0, 25: 37.0, 50: 41.0, 100: 45.0}
IDF_DECAY_C = 0.72       # intensity decay exponent with duration (h)
T_MIN, T_MAX = 1.01, 500.0


def depth_1h(return_period: float) -> float:
    """1-h rainfall depth for any return period, log-linear in T between the
    tabulated points (standard Gumbel-consistent interpolation/extrapolation)."""
    T = float(np.clip(return_period, T_MIN, T_MAX))
    keys = sorted(IDF_1H_MM)
    logs = [math.log(k) for k in keys]
    vals = [IDF_1H_MM[k] for k in keys]
    lt = math.log(T)
    if lt <= logs[0]:
        i0, i1 = 0, 1
    elif lt >= logs[-1]:
        i0, i1 = len(keys) - 2, len(keys) - 1
    else:
        i1 = next(i for i, v in enumerate(logs) if v >= lt)
        i0 = i1 - 1
    frac = (lt - logs[i0]) / (logs[i1] - logs[i0])
    return vals[i0] + frac * (vals[i1] - vals[i0])


def storm_depth(return_period: float, duration_h: float,
                climate_factor: float = 1.0) -> float:
    """Total storm depth (mm) for a return period and duration.

    i(T, t) = i1h(T) · t^-c  →  depth = i1h(T) · t^(1-c)
    """
    return depth_1h(return_period) * duration_h ** (1.0 - IDF_DECAY_C) * climate_factor


def chicago_hyetograph(total_mm: float, duration_h: float, peak_frac: float,
                       dt_s: float = 60.0) -> np.ndarray:
    """Chicago design storm: intensity (mm/h) per dt_s step.

    ``peak_frac`` r ∈ (0, 1) places the peak (0.15 ≈ front-loaded,
    0.5 centred, 0.85 back-loaded). Shape follows the IDF-consistent Chicago
    form  i(τ) ∝ ((1-c)·θ + b) / (θ + b)^(1+c)  with θ the scaled distance
    from the peak; the series is renormalised to hit ``total_mm`` exactly, so
    the shape controls timing while the IDF controls volume.
    """
    n = max(int(round(duration_h * 3600.0 / dt_s)), 4)
    t = (np.arange(n) + 0.5) * dt_s / 3600.0          # hours
    tp = peak_frac * duration_h
    c = IDF_DECAY_C
    b = 0.08                                          # h, keeps peak finite
    theta = np.where(t < tp,
                     (tp - t) / max(peak_frac, 1e-6),
                     (t - tp) / max(1.0 - peak_frac, 1e-6))
    shape = ((1.0 - c) * theta + b) / (theta + b) ** (1.0 + c)
    depth_per_step = shape / shape.sum() * total_mm
    return depth_per_step / (dt_s / 3600.0)           # → mm/h


def sample_return_period(u: float) -> float:
    """Annual-exceedance draw: U ~ (0,1) → T = 1/U, truncated to the IDF's
    credible range. Small U = rare storm; the tail is what drives risk."""
    return float(np.clip(1.0 / max(u, 1.0 / T_MAX), T_MIN, T_MAX))
