"""The hazard-module contract (plan §4/§9) — the seam that keeps the platform
extensible.

Every hazard module obeys the same shape:

    consume the twin
      → sample a hazard scenario (Monte-Carlo parameter vector)
      → run a physics solver
      → emit (a) a per-timestep physical-state time-series for replay and
             (b) a per-building / per-household $ impact.

The Monte-Carlo runner, results store, web app, and 3D viewer only ever talk
to this interface. Adding a hazard (wind, heat, ice, snow load) means one new
subclass + one registry entry — nothing above Tier 3 changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from citysim.twin.schema import Twin


@dataclass
class SimResult:
    """Physical outcome of one simulated scenario."""

    max_intensity: np.ndarray            # (ny, nx) peak hazard intensity per cell
                                         # (flood: max water depth in metres)
    building_intensity: dict[str, dict]  # building id → hazard-specific intensity measures
    node_state: dict[str, dict] = field(default_factory=dict)   # e.g. manhole peak head
    frames: list[dict] | None = None     # replay frames: [{"t": s, "grid": (ny,nx) f16, ...}]
    series: dict = field(default_factory=dict)                  # scalar time-series (driver etc.)
    meta: dict = field(default_factory=dict)


@dataclass
class ImpactResult:
    per_building: dict[str, dict]        # id → {structure_loss, contents_loss, total, mechanism}
    total_loss: float
    n_households: int
    per_household_mean: float
    meta: dict = field(default_factory=dict)


class HazardModule(ABC):
    """Base class every hazard implements."""

    name: str = "abstract"
    display_name: str = "Abstract hazard"
    #: declared sampled-parameter names, in LHS column order
    param_names: list[str] = []

    @abstractmethod
    def sample_scenarios(self, twin: Twin, n: int, seed: int,
                         options: dict | None = None) -> list[dict]:
        """Draw N Monte-Carlo scenario parameter vectors (Latin Hypercube)."""

    @abstractmethod
    def simulate(self, twin: Twin, scenario: dict,
                 record_frames: bool = False) -> SimResult:
        """Run the physics solver for one scenario. Must be deterministic given
        the scenario dict (which embeds its RNG seed) so any run can be
        re-simulated exactly for replay."""

    @abstractmethod
    def assess_impact(self, twin: Twin, sim: SimResult, scenario: dict) -> ImpactResult:
        """intensity at building → vulnerability curve → % loss → × value → $."""
