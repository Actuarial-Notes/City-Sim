"""1D sewer network model (plan §5.5): the below-ground half of the coupling.

A storage-routing surrogate for EPA SWMM: nodes store water, conduits pass it
downstream at their Manning full-flow capacity, and volume that cannot fit
*surcharges* back out of manholes to the 2D surface — the mechanism behind
street flooding — while node hydraulic head above a basement floor drives the
sewer-backup mechanism in combined-sewer areas.

The class exposes the same conceptual interface a PySWMM adapter would
(inflows in, surcharge + heads out per step), so swapping in real SWMM later
touches only this file.
"""

from __future__ import annotations

import numpy as np

from citysim.twin.schema import SewerNetwork

CHAMBER_AREA = 1.5      # m², effective manhole chamber storage area
DWF_PER_NODE = 0.0008   # m³/s dry-weather flow entering each combined node


class Sewer1D:
    def __init__(self, network: SewerNetwork, blockage_factor: float = 1.0):
        """blockage_factor < 1 derates pipe capacity (Monte-Carlo uncertainty
        on sediment/blockage/aging — plan §5.7)."""
        self.net = network
        nodes = network.nodes
        self.node_ids = [n.id for n in nodes]
        self.idx = {nid: k for k, nid in enumerate(self.node_ids)}
        self.rim = np.array([n.rim_elev for n in nodes])
        self.invert = np.array([n.invert_elev for n in nodes])
        self.max_inflow = np.array([n.max_inflow for n in nodes])
        self.is_outfall = np.array([n.kind == "outfall" for n in nodes])
        self.is_combined = np.array([n.system == "combined" for n in nodes])
        self.capacity = np.maximum(self.rim - self.invert, 0.5) * CHAMBER_AREA
        self.storage = np.zeros(len(nodes))
        self.max_head = self.invert.copy()

        # conduit full-flow capacity (Manning), derated by blockage
        self.c_from = np.array([self.idx[c.from_node] for c in network.conduits])
        self.c_to = np.array([self.idx[c.to_node] for c in network.conduits])
        q = []
        for c in network.conduits:
            area = np.pi * c.diameter ** 2 / 4.0
            rh = c.diameter / 4.0
            q.append((1.0 / c.roughness) * area * rh ** (2.0 / 3.0) * np.sqrt(max(c.slope, 1e-4)))
        self.q_full = np.array(q) * blockage_factor

        # process nodes upstream→downstream (by invert elevation)
        self.order = np.argsort(-self.invert)
        self.out_by_node: list[list[int]] = [[] for _ in nodes]
        for ci, fi in enumerate(self.c_from):
            self.out_by_node[fi].append(ci)
        self.outfall_volume = 0.0

    def head(self) -> np.ndarray:
        """Hydraulic grade line estimate per node."""
        return self.invert + self.storage / CHAMBER_AREA

    def step(self, dt: float, inflow: np.ndarray) -> np.ndarray:
        """Advance one step.

        inflow  : m³/s per node captured from the surface (catchbasin intake).
        returns : surcharge volume (m³) per node pushed back to the surface.
        """
        self.storage += inflow * dt
        self.storage[self.is_combined] += DWF_PER_NODE * dt

        # route downstream, upstream nodes first
        for ni in self.order:
            if self.is_outfall[ni]:
                self.outfall_volume += self.storage[ni]
                self.storage[ni] = 0.0
                continue
            avail = self.storage[ni]
            if avail <= 0.0:
                continue
            for ci in self.out_by_node[ni]:
                if avail <= 0.0:
                    break
                q = min(self.q_full[ci] * dt, avail)
                self.storage[self.c_to[ci]] += q
                avail -= q
            self.storage[ni] = avail

        # surcharge: whatever exceeds chamber capacity exits at the rim
        excess = np.maximum(self.storage - self.capacity, 0.0)
        excess[self.is_outfall] = 0.0
        self.storage -= excess

        head = self.head()
        # while actively surcharging, the HGL sits at/above the rim
        head = np.where(excess > 0.0, np.maximum(head, self.rim), head)
        np.maximum(self.max_head, head, out=self.max_head)
        return excess

    def total_storage(self) -> float:
        return float(self.storage.sum())
