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
# Defaults live in flood/assumptions.py, which is the single source of truth for
# every adjustable model constant; these mirror it so the class stays usable on
# its own (tests, notebooks) without threading a params dict through.
BUILDING_RAISE = 8.0  # m, added to DEM under buildings so water routes around
CFL_ALPHA = 0.7


class Surface2D:
    def __init__(self, dem: np.ndarray, cell_size: float, manning_n: np.ndarray,
                 infiltration_mmh: np.ndarray, imperviousness: np.ndarray,
                 building_mask: np.ndarray | None = None,
                 alpha: float = CFL_ALPHA,
                 building_raise: float = BUILDING_RAISE):
        self.dx = float(cell_size)
        self.alpha = alpha
        self.z = dem.astype(np.float64).copy()
        if building_mask is not None:
            self.z[building_mask] += building_raise
        self.n2 = (manning_n.astype(np.float64)) ** 2
        # effective infiltration capacity (m/s): pervious fraction only
        self.infil = (infiltration_mmh.astype(np.float64) / 1000.0 / 3600.0
                      * (1.0 - imperviousness.astype(np.float64)))
        ny, nx = dem.shape
        self.h = np.zeros((ny, nx))
        self.qx = np.zeros((ny, nx - 1))   # flux between (i, j) and (i, j+1), m²/s
        self.qy = np.zeros((ny - 1, nx))   # flux between (i, j) and (i+1, j)
        self.infiltrated = 0.0             # m³, mass-balance accounting

        # Face geometry does not change between steps, and neither does the
        # face-averaged roughness. Recomputing them inside the loop was costing
        # four full-grid passes per step on a city-sized domain.
        z = self.z
        self._zmax_x = np.maximum(z[:, :-1], z[:, 1:])
        self._zmax_y = np.maximum(z[:-1, :], z[1:, :])
        self._gn2f_x = G * 0.5 * (self.n2[:, :-1] + self.n2[:, 1:])
        self._gn2f_y = G * 0.5 * (self.n2[:-1, :] + self.n2[1:, :])

        # Scratch buffers, so a step allocates nothing. Explicit and a little
        # tedious, but this is the innermost loop of the whole product.
        self._wse = np.empty((ny, nx))
        self._div = np.empty((ny, nx))
        self._fx = [np.empty((ny, nx - 1)) for _ in range(4)]
        self._fy = [np.empty((ny - 1, nx)) for _ in range(4)]
        self._wet_x = np.empty((ny, nx - 1), dtype=bool)
        self._wet_y = np.empty((ny - 1, nx), dtype=bool)
        self._pos_x = np.empty((ny, nx - 1), dtype=bool)
        self._pos_y = np.empty((ny - 1, nx), dtype=bool)

    def _faces(self, q, wse, dt, lo, hi, zmax, gn2f, bufs, wet, pos, h_lo, h_hi):
        """Local-inertial flux update for one axis, in place.

        hflow is the depth over the higher of the two bed elevations; the update
        is the Bates/Horritt/Fewtrell (2010) inertial form, and the limiter caps
        a face at a quarter of the upwind cell's water per step so the explicit
        scheme stays positivity-preserving.
        """
        hflow, slope, qn, work = bufs

        np.maximum(wse[lo], wse[hi], out=hflow)
        hflow -= zmax
        np.greater(hflow, H_MIN, out=wet)

        np.subtract(wse[hi], wse[lo], out=slope)
        slope /= self.dx

        np.multiply(hflow, slope, out=qn)
        qn *= -G * dt
        qn += q

        np.maximum(hflow, H_MIN, out=work)
        np.power(work, 7.0 / 3.0, out=work)
        np.abs(q, out=slope)                 # slope is free now — reuse it
        slope *= dt
        slope *= gn2f
        slope /= work
        slope += 1.0
        qn /= slope

        # upwind depth decides the cap
        np.greater_equal(qn, 0.0, out=pos)
        np.copyto(work, h_hi)
        np.copyto(work, h_lo, where=pos)
        work *= 0.25 * self.dx / dt
        np.negative(work, out=slope)
        np.clip(qn, slope, work, out=qn)

        q.fill(0.0)
        np.copyto(q, qn, where=wet)

    @property
    def wse(self) -> np.ndarray:
        return self.z + self.h

    def stable_dt(self, dt_max: float = 15.0) -> float:
        hmax = float(self.h.max())
        if hmax < H_MIN:
            return dt_max
        return float(np.clip(self.alpha * self.dx / np.sqrt(G * hmax), 0.25, dt_max))

    def step(self, dt: float, rain_ms: float = 0.0,
             point_sources: list[tuple[int, int, float]] | None = None,
             source_idx: tuple[np.ndarray, np.ndarray] | None = None,
             source_q: np.ndarray | None = None) -> None:
        """Advance one explicit step.

        rain_ms       : rainfall rate (m/s) applied to every cell
        point_sources : [(i, j, m³/s)] — manhole surcharge inflow (+) or
                        catchbasin drainage (−) from the 1D coupling
        source_idx /
        source_q      : the same thing as arrays. The coupling has one entry per
                        sewer node every step, and on a city-sized network the
                        per-tuple Python loop costs more than the solver — this
                        path applies them with a single scatter-add.
        """
        h, dx = self.h, self.dx
        wse = self._wse
        np.add(self.z, h, out=wse)

        cx, cy = (slice(None), slice(0, -1)), (slice(None), slice(1, None))
        ry, sy = (slice(0, -1), slice(None)), (slice(1, None), slice(None))

        with np.errstate(divide="ignore", invalid="ignore"):
            self._faces(self.qx, wse, dt, cx, cy, self._zmax_x, self._gn2f_x,
                        self._fx, self._wet_x, self._pos_x, h[cx], h[cy])
            self._faces(self.qy, wse, dt, ry, sy, self._zmax_y, self._gn2f_y,
                        self._fy, self._wet_y, self._pos_y, h[ry], h[sy])

        # ---- continuity
        div = self._div
        div.fill(0.0)
        div[:, :-1] -= self.qx
        div[:, 1:] += self.qx
        div[:-1, :] -= self.qy
        div[1:, :] += self.qy
        div *= dt / dx                          # q is m²/s per unit width
        h += div
        np.maximum(h, 0.0, out=h)

        if rain_ms > 0.0:
            h += rain_ms * dt

        if source_idx is not None and source_q is not None and len(source_q):
            # np.add.at rather than fancy-index assignment: two nodes can share a
            # cell, and their inflows have to sum instead of overwriting
            np.add.at(h, source_idx, source_q * dt / (dx * dx))
            np.maximum(h, 0.0, out=h)
        elif point_sources:
            cell_area = dx * dx
            for i, j, q in point_sources:
                h[i, j] = max(h[i, j] + q * dt / cell_area, 0.0)

        # infiltration losses on wet pervious cells
        f = self._div                            # reuse: div is finished with
        np.multiply(self.infil, dt, out=f)
        np.minimum(h, f, out=f)
        self.infiltrated += float(f.sum()) * dx * dx
        h -= f
        np.maximum(h, 0.0, out=h)

    def volume(self) -> float:
        return float(self.h.sum()) * self.dx * self.dx
