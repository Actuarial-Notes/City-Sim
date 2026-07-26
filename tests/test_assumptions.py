"""The assumption registry, and that its knobs actually reach the solver.

The point of V2 is that the model is no longer a black box: every constant that
moves a dollar figure is declared, ranged, cited, and adjustable. That is only
true if the declarations and the code cannot drift apart — which is what these
tests pin down.
"""

from __future__ import annotations

import numpy as np
import pytest

from citysim.hazards import get_module
from citysim.hazards.flood import assumptions as fa
from citysim.hazards.flood.damage import building_loss
from citysim.hazards.flood.module import FloodModule, _has_valve
from citysim.montecarlo import MonteCarloRunner
from citysim.twin import build_twin
from citysim.twin import params as tp


@pytest.fixture(scope="module")
def twin():
    # the procedural twin is a quarter the cells of the Hamilton one, which
    # matters when a test runs a dozen simulations
    return build_twin(mode="synthetic")


# ------------------------------------------------------------- spec integrity
def test_every_knob_default_is_inside_its_own_range():
    for k in fa.KNOBS:
        if k["type"] == "bool":
            assert isinstance(k["default"], bool), k["id"]
            continue
        assert k["min"] <= k["default"] <= k["max"], k["id"]
        assert k["step"] > 0, k["id"]


def test_every_knob_is_documented_and_cited():
    for k in fa.KNOBS:
        assert k["description"].strip(), k["id"]
        assert k["group"] in {g["id"] for g in fa.spec()["groups"]}, k["id"]
        if k.get("source"):
            assert k["source"] in {s["id"] for s in fa.SOURCES}, k["id"]


def test_min_max_pairs_point_at_real_knobs():
    ids = {k["id"] for k in fa.KNOBS}
    for k in fa.KNOBS:
        if k.get("pair"):
            assert k["pair"] in ids, k["id"]
            assert k["default"] <= fa.DEFAULTS[k["pair"]], k["id"]


def test_every_knob_is_actually_consumed_somewhere():
    """Guards against a knob that looks adjustable but is wired to nothing.

    A slider the solver ignores is worse than no slider: it tells the user the
    model depends on something it does not.
    """
    from pathlib import Path
    root = Path(fa.__file__).resolve().parents[3]
    sources = "\n".join(
        p.read_text() for p in root.rglob("*.py")
        if "test" not in p.parts and p.name != "assumptions.py")
    unused = [k["id"] for k in fa.KNOBS if k["id"] not in sources]
    assert not unused, f"declared but never read: {unused}"


def test_resolve_clamps_and_ignores_unknown_keys():
    r = fa.resolve({"ddc_severity": 99.0, "sill_depth": -5.0, "not_a_knob": 3})
    assert r["ddc_severity"] == 2.0
    assert r["sill_depth"] == 0.0
    assert "not_a_knob" not in r


def test_resolve_orders_min_max_pairs():
    # a user who drags the minimum above the maximum gets a valid swapped range,
    # not an empty one that would silently produce degenerate scenarios
    r = fa.resolve({"duration_min_h": 6.0, "duration_max_h": 1.0})
    assert r["duration_min_h"] <= r["duration_max_h"]


def test_deviations_reports_only_what_moved():
    assert fa.deviations(fa.resolve({})) == []
    devs = fa.deviations(fa.resolve({"climate_factor": 1.15}))
    assert [d["id"] for d in devs] == ["climate_factor"]
    assert devs[0]["default"] == 1.0 and devs[0]["value"] == 1.15


def test_landcover_table_is_the_single_source():
    """The lookup used to be duplicated in synthetic.py and builder.py."""
    n = len(fa.LANDCOVER_CLASSES)
    assert len(fa.LANDCOVER_MANNING) == n
    assert len(fa.LANDCOVER_IMPERVIOUSNESS) == n
    assert len(fa.LANDCOVER_INFILTRATION) == n
    from citysim.twin import builder, synthetic
    for mod in (builder, synthetic):
        src = open(mod.__file__).read()
        assert "0.013, 0.02" not in src, f"{mod.__name__} still has its own copy"


# ------------------------------------------------------- overrides reach code
def test_ddc_severity_scales_loss_proportionally():
    class B:
        material = "masonry"
        foundation = "basement"
        basement_depth = 2.1
        ground_elev = 80.0
        first_floor_height = 0.3
        structure_value = 500_000.0
        contents_value = 175_000.0
        has_basement = True
        basement_floor_elev = 77.9

    base = building_loss(B(), 0.0, 80.5, params=fa.resolve({}))
    hot = building_loss(B(), 0.0, 80.5, params=fa.resolve({"ddc_severity": 2.0}))
    assert base["total"] > 0
    assert hot["total"] == pytest.approx(base["total"] * 2, rel=1e-6)


def test_sill_depth_gates_basement_inundation():
    class B:
        material = "wood_frame"
        foundation = "basement"
        basement_depth = 2.0
        ground_elev = 80.0
        first_floor_height = 0.5
        structure_value = 400_000.0
        contents_value = 140_000.0
        has_basement = True
        basement_floor_elev = 78.0

    shallow = 0.20
    assert building_loss(B(), shallow, None, params=fa.resolve({}))["total"] == 0
    lowered = fa.resolve({"sill_depth": 0.10})
    hit = building_loss(B(), shallow, None, params=lowered)
    assert hit["total"] > 0
    assert "basement_inundation" in hit["mechanisms"]


def test_idf_scale_and_climate_factor_both_scale_storm_depth(twin):
    m = get_module("flood")
    base = m.sample_scenarios(twin, 6, 7)
    scaled = m.sample_scenarios(twin, 6, 7, {"idf_scale": 2.0})
    climate = m.sample_scenarios(twin, 6, 7, {"climate_factor": 1.5})
    for a, b, c in zip(base, scaled, climate):
        # total_mm is stored rounded to 0.01 mm, so scaling the input and scaling
        # the rounded output differ in the last digit
        assert b["total_mm"] == pytest.approx(a["total_mm"] * 2.0, rel=1e-3)
        assert c["total_mm"] == pytest.approx(a["total_mm"] * 1.5, rel=1e-3)


def test_out_of_range_climate_factor_is_clamped_not_applied(twin):
    """The API never trusts the client to have kept the slider honest."""
    m = get_module("flood")
    ceiling = next(k for k in fa.KNOBS if k["id"] == "climate_factor")["max"]
    at_max = m.sample_scenarios(twin, 3, 7, {"climate_factor": ceiling})
    absurd = m.sample_scenarios(twin, 3, 7, {"climate_factor": 12.0})
    assert [s["total_mm"] for s in absurd] == [s["total_mm"] for s in at_max]


def test_sampling_range_knobs_bound_the_draws(twin):
    m = get_module("flood")
    sc = m.sample_scenarios(twin, 24, 3, {"duration_min_h": 2.0, "duration_max_h": 2.5,
                                          "blockage_min": 0.9, "blockage_max": 0.95})
    assert all(2.0 <= s["duration_h"] <= 2.5 for s in sc)
    assert all(0.9 <= s["blockage_factor"] <= 0.95 for s in sc)


def test_ddc_sigma_zero_removes_cost_uncertainty(twin):
    m = get_module("flood")
    sc = m.sample_scenarios(twin, 12, 5, {"ddc_sigma": 0.0})
    assert {s["ddc_factor"] for s in sc} == {1.0}


def test_resolved_set_travels_with_the_scenario(twin):
    m = get_module("flood")
    sc = m.sample_scenarios(twin, 2, 11, {"lateral_factor": 0.25})
    # the solver reads it back out of the scenario, which is what makes it
    # available inside a worker process without extra plumbing
    assert FloodModule.resolved(sc[0])["lateral_factor"] == 0.25
    # a pre-V2 scenario with no resolved block still works
    assert FloodModule.resolved({"options": {}})["lateral_factor"] == \
        fa.DEFAULTS["lateral_factor"]


def test_overrides_survive_the_worker_pool(twin):
    """Parallel and serial must agree, or an override is being lost in pickling."""
    m = get_module("flood")
    opts = {"ddc_severity": 1.6, "climate_factor": 1.2, "lateral_factor": 0.45}
    par = MonteCarloRunner(twin, m, workers=2).run(n=4, seed=99, options=opts)

    scenarios = m.sample_scenarios(twin, 4, 99, opts)
    serial = []
    for s in scenarios:
        sim = m.simulate(twin, s)
        serial.append(m.assess_impact(twin, sim, s).total_loss)
    assert [r["total_loss"] for r in par["runs"]] == pytest.approx(serial, rel=1e-9)


# ---------------------------------------------------------------- mitigation
def test_valve_adoption_bounds_match_the_old_switch(twin):
    m = get_module("flood")
    scenarios = m.sample_scenarios(twin, 1, 4321)
    sim = m.simulate(twin, scenarios[0])

    def loss(opts):
        sc = m.sample_scenarios(twin, 1, 4321, opts)[0]
        return m.assess_impact(twin, sim, sc).total_loss

    none = loss({})
    full = loss({"backwater_valves": True})
    zero_adopt = loss({"backwater_valves": True, "valve_adoption": 0.0})
    half = loss({"backwater_valves": True, "valve_adoption": 0.5})
    useless = loss({"backwater_valves": True, "valve_effectiveness": 0.0})

    assert full < none, "valves must reduce loss"
    assert zero_adopt == none, "nobody installed one — same as no program"
    assert useless == pytest.approx(none), "a valve that blocks nothing changes nothing"
    assert full <= half <= none


def test_valve_assignment_is_stable_across_processes():
    # a salted hash() would differ per worker and make runs irreproducible
    ids = [f"b{i}" for i in range(400)]
    picked = [i for i in ids if _has_valve(i, 0.5)]
    assert 0.35 < len(picked) / len(ids) < 0.65
    assert picked == [i for i in ids if _has_valve(i, 0.5)]
    assert all(_has_valve(i, 1.0) for i in ids)
    assert not any(_has_valve(i, 0.0) for i in ids)


# ----------------------------------------------------------- twin parameters
def test_twin_param_spec_integrity():
    groups = {g["id"] for g in tp.GROUPS}
    for p in tp.PARAMS:
        assert p["min"] <= p["default"] <= p["max"], p["id"]
        assert p["description"].strip(), p["id"]
        assert p["group"] in groups, p["id"]
        if p.get("source"):
            assert p["source"] in {s["id"] for s in tp.SOURCES}, p["id"]


def test_twin_fingerprint_is_empty_for_defaults():
    # keeps the default twin's id identical to V1, so stored twins stay addressable
    assert tp.fingerprint(tp.resolve({})) == ""
    assert tp.fingerprint(tp.resolve({"cost_index": 1.5})) != ""


def test_solver_knobs_change_run_time_not_the_answer(twin):
    """A coarser coupling step must not move the result much.

    The 1D/2D coupling timestep is a performance knob. If halving it changed the
    losses materially it would be a physics knob wearing a performance label.
    """
    m = get_module("flood")
    fine = m.sample_scenarios(twin, 1, 77, {"coupling_dt_s": 2.0})[0]
    coarse = m.sample_scenarios(twin, 1, 77, {"coupling_dt_s": 20.0})[0]
    a = m.assess_impact(twin, m.simulate(twin, fine), fine).total_loss
    b = m.assess_impact(twin, m.simulate(twin, coarse), coarse).total_loss
    assert a == pytest.approx(b, rel=0.20), (a, b)


def test_peak_depths_stay_physical(twin):
    m = get_module("flood")
    sc = m.sample_scenarios(twin, 3, 8)
    for s in sc:
        sim = m.simulate(twin, s)
        assert np.isfinite(sim.max_intensity).all()
        assert sim.max_intensity.min() >= 0.0
        assert sim.max_intensity.max() < 12.0, "street depths should not reach metres"
