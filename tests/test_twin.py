import numpy as np
import pytest

from citysim.twin import build_twin, params as twin_params
from citysim.twin.store import TwinStore


@pytest.fixture(scope="session")
def twin():
    return build_twin()


@pytest.fixture(scope="session")
def synthetic_twin():
    return build_twin(mode="synthetic")


def test_twin_determinism(twin):
    again = build_twin()
    assert again.id == twin.id
    assert len(again.buildings) == len(twin.buildings)
    assert np.allclose(again.terrain.dtm, twin.terrain.dtm)
    assert again.buildings[0].structure_value == twin.buildings[0].structure_value


def test_default_mode_is_hamilton(twin):
    assert twin.region["mode"] == "hamilton"
    assert "Hamilton" in twin.name
    # real-world context geometry rides along for the viewer
    assert twin.features["roads"]
    assert any(l["name"] == "Hamilton Harbour" for l in twin.features["labels"])


def test_terrain_shape_and_slope(twin):
    ny, nx = twin.terrain.shape
    assert twin.terrain.dtm.shape == (ny, nx)
    assert nx > 50 and ny > 50
    # the escarpment side (row 0, south) sits above the harbour side (last row)
    assert twin.terrain.dtm[0].mean() > twin.terrain.dtm[-1].mean() + 8


def test_synthetic_mode_still_works(synthetic_twin):
    ny, nx = synthetic_twin.terrain.shape
    assert (ny, nx) == (110, 150)          # the procedural default is unchanged
    assert synthetic_twin.region["mode"] == "synthetic"
    assert synthetic_twin.terrain.dtm[0].mean() > synthetic_twin.terrain.dtm[-1].mean() + 15


def test_twin_params_change_the_twin_and_its_id():
    a = build_twin(mode="synthetic")
    b = build_twin(mode="synthetic", params={"cost_index": 2.0})
    assert a.id != b.id, "different parameters must not share a twin cache entry"
    va = sum(x.structure_value for x in a.buildings)
    vb = sum(x.structure_value for x in b.buildings)
    # values are rounded to the nearest $100 per building, so the doubling is
    # exact only to within that rounding
    assert vb == pytest.approx(va * 2, rel=1e-4)


def test_twin_params_are_clamped_and_recorded():
    # a client asking for something outside the declared range gets the bound,
    # not an exception and not the raw value
    t = build_twin(mode="synthetic", params={"basement_rate_pre1990": 5.0})
    assert t.params["basement_rate_pre1990"] == 1.0
    # read-only geography cannot be redefined from the request
    t2 = build_twin(mode="synthetic", params={"cell_size": 999.0})
    assert t2.params["cell_size"] == twin_params.DEFAULTS["cell_size"]


def test_no_basements_removes_the_backup_pathway():
    t = build_twin(mode="synthetic", params={"basement_rate_pre1990": 0.0,
                                             "basement_rate_post1990": 0.0})
    assert not any(b.has_basement for b in t.buildings)


def test_stock_profile_and_exposure(twin):
    s = twin.summary()
    assert s["stock"]["decades"] and s["stock"]["materials"]
    assert 0 <= s["stock"]["basement_share"] <= 1
    assert s["exposure"]["residential"]["structure"] > 0


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
