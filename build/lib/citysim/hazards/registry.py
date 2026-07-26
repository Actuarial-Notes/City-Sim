"""Hazard-module registry — the hazard picker's source of truth.

Future modules (wind, heat, ice storm, snow load) appear here as they are
built; the web app lists them from this registry, so adding a hazard never
touches the UI code.
"""

from __future__ import annotations

from .contract import HazardModule

_REGISTRY: dict[str, type[HazardModule]] = {}

# Hazards on the roadmap, surfaced (disabled) in the picker per plan §5.1.
PLANNED = [
    {"name": "wind", "display_name": "Extreme wind", "available": False},
    {"name": "heat", "display_name": "Extreme heat", "available": False},
    {"name": "ice", "display_name": "Ice storm", "available": False},
    {"name": "snow", "display_name": "Snow load", "available": False},
]


def register(cls: type[HazardModule]) -> type[HazardModule]:
    _REGISTRY[cls.name] = cls
    return cls


def get_module(name: str) -> HazardModule:
    _ensure_builtin()
    return _REGISTRY[name]()


def list_modules() -> list[dict]:
    _ensure_builtin()
    out = [{"name": c.name, "display_name": c.display_name, "available": True}
           for c in _REGISTRY.values()]
    out.extend(p for p in PLANNED if p["name"] not in _REGISTRY)
    return out


def _ensure_builtin() -> None:
    if "flood" not in _REGISTRY:
        from .flood.module import FloodModule  # noqa: F401  (self-registers)
