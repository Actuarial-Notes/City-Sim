import numpy as np
import pytest

from citysim.hazards import get_module
from citysim.hazards.flood import rainfall
from citysim.hazards.flood.damage import building_loss
from citysim.hazards.flood.surface2d import Surface2D
from citysim.twin import build_twin
from citysim.twin.schema import Building


@pytest.fixture(scope="session")
def twin():
    return build_twin()


@pytest.fixture(scope="session")
def flood():
    return get_module("flood")


# ------------------------------------------------------------------ rainfall
def test_idf_monotonic_in_return_period():
    depths = [rainfall.storm_depth(T, 2.0) for T in (2, 5, 10, 25, 50, 100)]
    assert all(b > a for a, b in zip(depths, depths[1:]))


def test_idf_longer_storms_have_more_volume_lower_intensity():
    assert rainfall.storm_depth(10, 6.0) > rainfall.storm_depth(10, 1.0)
    assert rainfall.storm_depth(10, 6.0) / 6.0 < rainfall.storm_depth(10, 1.0)


def test_hyetograph_conserves_volume_and_peaks_where_told():
    for pf in (0.2, 0.5, 0.8):
        h = rainfall.chicago_hyetograph(40.0, 2.0, pf, dt_s=60.0)
        assert h.sum() * (60.0 / 3600.0) == pytest.approx(40.0, rel=1e-6)
        peak_at = np.argmax(h) / len(h)
        assert abs(peak_at - pf) < 0.1


def test_climate_factor_scales_depth():
    assert rainfall.storm_depth(100, 2.0, climate_factor=1.3) == \
        pytest.approx(rainfall.storm_depth(100, 2.0) * 1.3)


# ------------------------------------------------------------------ 2D solver
def test_surface2d_mass_conservation_flat_bowl():
    dem = np.zeros((20, 20))
    dem[0, :] = dem[-1, :] = dem[:, 0] = dem[:, -1] = 5.0   # walled bowl
    s = Surface2D(dem, 5.0, np.full((20, 20), 0.03), np.zeros((20, 20)),
                  np.ones((20, 20)))
    s.h[10, 10] = 2.0
    v0 = s.volume()
    for _ in range(300):
        s.step(s.stable_dt())
    assert s.volume() == pytest.approx(v0, rel=1e-6)
    assert s.h.max() < 2.0          # it spread out
    assert (s.h >= 0).all()


def test_surface2d_water_flows_downhill():
    dem = np.tile(np.linspace(10, 0, 30), (10, 1))          # slope to the east
    s = Surface2D(dem, 5.0, np.full((10, 30), 0.03), np.zeros((10, 30)),
                  np.ones((10, 30)))
    s.h[5, 3] = 1.0
    for _ in range(400):
        s.step(s.stable_dt())
    east = s.h[:, 15:].sum()
    west = s.h[:, :15].sum()
    assert east > west


# ------------------------------------------------------------------ module contract
def test_scenarios_deterministic_and_within_bounds(twin, flood):
    a = flood.sample_scenarios(twin, 16, seed=99)
    b = flood.sample_scenarios(twin, 16, seed=99)
    assert a == b
    for s in a:
        assert 1.0 <= s["return_period"] <= 500.0
        assert 1.0 <= s["duration_h"] <= 6.0
        assert 0.0 < s["peak_frac"] < 1.0
        assert s["total_mm"] > 0


def test_simulation_reproducible_and_physical(twin, flood):
    s = flood.sample_scenarios(twin, 2, seed=5)[1]
    r1 = flood.simulate(twin, s)
    r2 = flood.simulate(twin, s)
    assert np.allclose(r1.max_intensity, r2.max_intensity)
    assert r1.max_intensity.max() < 3.0          # no numerical blow-up
    assert (r1.max_intensity >= 0).all()


def test_bigger_storm_bigger_loss(twin, flood):
    base = {"run": 0, "seed": 1, "duration_h": 2.0, "peak_frac": 0.4,
            "climate_factor": 1.0, "infil_factor": 0.8, "manning_factor": 1.0,
            "blockage_factor": 0.85, "ddc_factor": 1.0, "options": {}}
    small = dict(base, return_period=2, total_mm=rainfall.storm_depth(2, 2.0))
    big = dict(base, return_period=100, total_mm=rainfall.storm_depth(100, 2.0))
    loss_small = flood.assess_impact(twin, flood.simulate(twin, small), small).total_loss
    loss_big = flood.assess_impact(twin, flood.simulate(twin, big), big).total_loss
    assert loss_big > loss_small


def test_backwater_valves_reduce_backup_losses(twin, flood):
    s = {"run": 0, "seed": 1, "return_period": 25, "duration_h": 2.0,
         "peak_frac": 0.4, "total_mm": rainfall.storm_depth(25, 2.0),
         "climate_factor": 1.0, "infil_factor": 0.7, "manning_factor": 1.0,
         "blockage_factor": 0.8, "ddc_factor": 1.0, "options": {}}
    sim = flood.simulate(twin, s)
    without = flood.assess_impact(twin, sim, s).total_loss
    s_mit = dict(s, options={"backwater_valves": True})
    with_valves = flood.assess_impact(twin, sim, s_mit).total_loss
    assert with_valves < without


# ------------------------------------------------------------------ damage curves
def _house(**kw):
    d = dict(id="t", footprint=[(0, 0), (10, 0), (10, 10), (0, 10)],
             centroid=(5, 5), height=6.0, storeys=2, year_built=1930,
             use="residential", material="masonry", foundation="basement",
             basement_depth=2.0, ground_elev=100.0, first_floor_height=0.3,
             dwelling_units=1, structure_value=500_000.0, contents_value=175_000.0)
    d.update(kw)
    return Building(**d)


def test_damage_zero_when_dry():
    assert building_loss(_house(), 0.0, None)["total"] == 0.0


def test_damage_monotonic_in_depth():
    losses = [building_loss(_house(), d, None)["total"] for d in (0.3, 0.6, 1.0, 1.5)]
    assert all(b >= a for a, b in zip(losses, losses[1:]))
    assert losses[-1] > 0


def test_sewer_backup_claim_magnitude_matches_ontario_benchmark():
    # full surcharge (head at grade) on a typical house lands in the
    # tens-of-thousands range the Ontario literature reports
    loss = building_loss(_house(), 0.0, backup_head=100.0)
    assert 20_000 < loss["total"] < 120_000
    assert loss["mechanisms"] == ["sewer_backup"]


def test_slab_building_immune_to_backup():
    slab = _house(foundation="slab", basement_depth=0.0)
    assert building_loss(slab, 0.0, backup_head=100.0)["total"] == 0.0


def test_ddc_factor_scales(twin):
    a = building_loss(_house(), 0.8, None, ddc_factor=1.0)["total"]
    b = building_loss(_house(), 0.8, None, ddc_factor=1.5)["total"]
    assert b == pytest.approx(a * 1.5, rel=1e-6)
