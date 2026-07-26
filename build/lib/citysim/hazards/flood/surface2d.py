"""2D overland flow: raster shallow-water solver (plan §5.6).

Implements the *local inertial* approximation of the shallow-water equations
(Bates, Horritt & Fewtrell 2010) — the formulation used by LISFLOOD-FP and
SynxFlow. It captures street-scale routing and ponding on a coarse raster at
a fraction of full-SWE cost, which is exactly the plan's fidelity argument:
accuracy comes from the DEM, not from solver order.

Vectorised NumPy; a GPU engine (SynxFlow/CUDA) can replace this class behind
the same step() interface when available.
"""

from __future__ import annotations

import numpy as np

G = 9.81
H_MIN = 1e-4          # m, dry threshold
BUILDING_RAISE = 8.0  # m, added to DEM under buildings so water routes around


class Surface2D:
    def __init__(self, dem: np.ndarray, cell_size: float, manning_n: np.ndarray,
                 infiltration_mmh: np.ndarray, imperviousness: np.ndarray,
                 building_mask: np.ndarray | None = None,
                 alpha: float = 0.7):
        self.dx = float(cell_size)
        self.alpha = alpha
        self.z = dem.astype(np.float64).copy()
        if building_mask is not None:
            self.z[building_mask] += BUILDING_RAISE
        self.n2 = (manning_n.astype(np.float64)) ** 2
        # effective infiltration capacity (m/s): pervious fraction only
        self.infil = (infiltration_mmh.astype(np.float64) / 1000.0 / 3600.0
                      * (1.0 - imperviousness.astype(np.float64)))
        ny, nx = dem.shape
        self.h = np.zeros((ny, nx))
        self.qx = np.zeros((ny, nx - 1))   # flux between (i, j) and (i, j+1), m²/s
        self.qy = np.zeros((ny - 1, nx))   # flux between (i, j) and (i+1, j)
        self.infiltrated = 0.0             # m³, mass-balance accounting

    @property
    def wse(self) -> np.ndarray:
        return self.z + self.h

    def stable_dt(self, dt_max: float = 15.0) -> float:
        hmax = float(self.h.max())
        if hmax < H_MIN:
            return dt_max
        return float(np.clip(self.alpha * self.dx / np.sqrt(G * hmax), 0.25, dt_max))

    def step(self, dt: float, rain_ms: float = 0.0,
             point_sources: list[tuple[int, int, float]] | None = None) -> None:
        """Advance one explicit step.

        rain_ms       : rainfall rate (m/s) applied to every cell
        point_sources : [(i, j, m³/s)] — manhole surcharge inflow (+) or
                        catchbasin drainage (−) from the 1D coupling
        """
        z, h, dx = self.z, self.h, self.dx
        wse = z + h

        # ---- x-direction face fluxes (local inertial update)
        hflow = np.maximum(wse[:, :-1], wse[:, 1:]) - np.maximum(z[:, :-1], z[:, 1:])
        wet = hflow > H_MIN
        slope = (wse[:, 1:] - wse[:, :-1]) / dx
        n2f = 0.5 * (self.n2[:, :-1] + self.n2[:, 1:])
        with np.errstate(divide="ignore", invalid="ignore"):
            qn = (self.qx - G * hflow * dt * slope) / \
                 (1.0 + G * dt * n2f * np.abs(self.qx) / np.maximum(hflow, H_MIN) ** (7.0 / 3.0))
        # flux limiter: a face may not move more water than the upwind cell
        # holds in one step (keeps the explicit scheme positivity-preserving)
        h_up = np.where(qn >= 0.0, h[:, :-1], h[:, 1:])
        qcap = 0.25 * h_up * dx / dt
        self.qx = np.where(wet, np.clip(qn, -qcap, qcap), 0.0)

        # ---- y-direction face fluxes
        hflow = np.maximum(wse[:-1, :], wse[1:, :]) - np.maximum(z[:-1, :], z[1:, :])
        wet = hflow > H_MIN
        slope = (wse[1:, :] - wse[:-1, :]) / dx
        n2f = 0.5 * (self.n2[:-1, :] + self.n2[1:, :])
        with np.errstate(divide="ignore", invalid="ignore"):
            qn = (self.qy - G * hflow * dt * slope) / \
                 (1.0 + G * dt * n2f * np.abs(self.qy) / np.maximum(hflow, H_MIN) ** (7.0 / 3.0))
        h_up = np.where(qn >= 0.0, h[:-1, :], h[1:, :])
        qcap = 0.25 * h_up * dx / dt
        self.qy = np.where(wet, np.clip(qn, -qcap, qcap), 0.0)

        # ---- continuity
        div = np.zeros_like(h)
        div[:, :-1] -= self.qx
        div[:, 1:] += self.qx
        div[:-1, :] -= self.qy
        div[1:, :] += self.qy
        h += div * dt / dx                      # q is m²/s per unit width
        np.maximum(h, 0.0, out=h)

        if rain_ms > 0.0:
            h += rain_ms * dt

        if point_sources:
            cell_area = dx * dx
            for i, j, q in point_sources:
                h[i, j] = max(h[i, j] + q * dt / cell_area, 0.0)

        # infiltration losses on wet pervious cells
        f = np.minimum(h, self.infil * dt)
        self.infiltrated += float(f.sum()) * dx * dx
        h -= f
        np.maximum(h, 0.0, out=h)

    def volume(self) -> float:
        return float(self.h.sum()) * self.dx * self.dx
