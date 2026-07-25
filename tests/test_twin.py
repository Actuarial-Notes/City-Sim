import numpy as np
import pytest

from citysim.twin import build_twin
from citysim.twin.store import TwinStore


@pytest.fixture(scope="session")
def twin():
    return build_twin()


def test_twin_determinism(twin):
    again = build_twin()
    assert again.id == twin.id
    assert len(again.buildings) == len(twin.buildings)
    assert np.allclose(again.terrain.dtm, twin.terrain.dtm)
    assert again.buildings[0].structure_value == twin.buildings[0].structure_value


def test_terrain_shape_and_slope(twin):
    ny, nx = twin.terrain.shape
    assert (ny, nx) == (110, 150)
    # escarpment: south edge (row 0) sits well above the harbour edge (last row)
    assert twin.terrain.dtm[0].mean() > twin.terrain.dtm[-1].mean() + 15


def test_buildings_physical_attributes_only(twin):
    b = twin.buildings[0]
    # the twin stores physical facts, not hazard interpretations
    assert b.material in ("masonry", "wood_frame", "concrete", "steel")
    assert b.foundation in ("basement", "crawlspace", "slab")
    assert not hasattr(b, "flood_vulnerability")
    assert b.structure_value > 0
    res = [x for x in twin.buildings if x.use == "residential"]
    assert sum(1 for x in res if x.has_basement) > len(res) * 0.5


def test_sewer_network_topology(twin):
    ids = {n.id for n in twin.sewer.nodes}
    for c in twin.sewer.conduits:
        assert c.from_node in ids and c.to_node in ids
        assert c.diameter > 0 and c.slope > 0
    assert any(n.kind == "outfall" for n in twin.sewer.nodes)
    systems = {n.system for n in twin.sewer.nodes}
    assert "combined" in systems     # the backup-prone areas exist


def test_landcover_parameter_grids(twin):
    lc = twin.landcover
    assert lc.manning_n.shape == twin.terrain.shape
    assert float(lc.imperviousness.min()) >= 0 and float(lc.imperviousness.max()) <= 1
    assert (lc.infiltration >= 0).all()


def test_store_roundtrip(tmp_path, twin):
    store = TwinStore(tmp_path)
    store.save(twin)
    loaded = store.load(twin.id)
    assert loaded.id == twin.id
    assert len(loaded.buildings) == len(twin.buildings)
    assert np.allclose(loaded.terrain.dtm, twin.terrain.dtm)
    assert loaded.buildings[3].footprint == twin.buildings[3].footprint
    assert loaded.sewer.conduits[0].diameter == twin.sewer.conduits[0].diameter
    assert store.list()[0]["id"] == twin.id
