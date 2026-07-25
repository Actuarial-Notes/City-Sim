"""CitySim — an extensible environmental digital twin platform.

Three tiers:
  1. ``citysim.server``     — web application (FastAPI + browser SPA)
  2. ``citysim.twin``       — hazard-agnostic digital-twin core
  3. ``citysim.hazards``    — pluggable hazard simulation modules (flood first)

Shared scaffolding: ``citysim.montecarlo`` (sampling + batch runner) and
``citysim.results`` (run/frame storage the viewer replays from).
"""

__version__ = "0.1.0"
