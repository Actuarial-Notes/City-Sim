"""Live open-data connectors (Tier 2 ingest).

Each connector wraps one open-data source from the build plan's inventory.
All are best-effort: any network / dependency failure degrades gracefully and
the builder falls back to the procedural twin so the platform always works.
"""

from .osm import geocode, fetch_osm_buildings
from .ontario_geohub import OntarioGeoHubDTM
from .hamilton_opendata import HamiltonOpenData

__all__ = ["geocode", "fetch_osm_buildings", "OntarioGeoHubDTM", "HamiltonOpenData"]
