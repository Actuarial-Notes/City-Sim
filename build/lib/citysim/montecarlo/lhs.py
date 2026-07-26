"""Latin Hypercube sampling (plan §5.7) — stratified uniform draws so a few
hundred runs cover the parameter space as evenly as thousands of naive draws.
No pyDOE dependency; the construction is a dozen lines of NumPy."""

from __future__ import annotations

import numpy as np


def latin_hypercube(n: int, dims: int, seed: int) -> np.ndarray:
    """(n, dims) array in [0, 1): each column is a permuted stratified sample."""
    rng = np.random.default_rng(seed)
    u = (rng.random((n, dims)) + np.arange(n)[:, None]) / n
    for d in range(dims):
        u[:, d] = u[rng.permutation(n), d]
    return u
