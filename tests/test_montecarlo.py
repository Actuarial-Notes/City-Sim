import numpy as np
import pytest

from citysim.hazards import get_module
from citysim.montecarlo import MonteCarloRunner, latin_hypercube
from citysim.results import ResultsStore
from citysim.twin import build_twin


def test_lhs_stratification():
    u = latin_hypercube(50, 3, seed=1)
    assert u.shape == (50, 3)
    for d in range(3):
        # exactly one sample per stratum
        assert sorted((u[:, d] * 50).astype(int).tolist()) == list(range(50))


def test_lhs_deterministic():
    assert np.allclose(latin_hypercube(20, 4, seed=7), latin_hypercube(20, 4, seed=7))


@pytest.fixture(scope="module")
def mc_output():
    twin = build_twin()
    runner = MonteCarloRunner(twin, get_module("flood"), workers=4)
    return twin, runner.run(n=8, seed=42)


def test_runner_output_shape(mc_output):
    twin, out = mc_output
    assert len(out["runs"]) == 8
    assert out["building_matrix"].shape == (8, len(twin.buildings))
    st = out["stats"]["per_household"]
    assert st["p95"] >= st["median"] >= 0
    assert st["cvar95"] >= st["var95"]
    reps = out["stats"]["representative_runs"]
    assert set(reps) == {"median", "p90", "p95", "p99", "max"}


def test_exceedance_curve_monotone(mc_output):
    _, out = mc_output
    ex = out["exceedance"]
    losses = [p["loss"] for p in ex]
    probs = [p["prob"] for p in ex]
    assert losses == sorted(losses, reverse=True)
    assert probs == sorted(probs)


def test_results_store_roundtrip(tmp_path, mc_output):
    twin, out = mc_output
    store = ResultsStore(tmp_path)
    store.save_job("jobx", twin.id, out)
    loaded = store.load_job("jobx")
    assert loaded["twin_id"] == twin.id
    assert loaded["stats"]["n_runs"] == 8
    assert loaded["building_matrix"].shape == out["building_matrix"].shape
    assert store.list_jobs()[0]["job_id"] == "jobx"


def test_frames_cache_roundtrip(tmp_path, mc_output):
    twin, out = mc_output
    module = get_module("flood")
    sim = module.simulate(twin, out["scenarios"][0], record_frames=True)
    store = ResultsStore(tmp_path)
    store.save_job("jobx", twin.id, out)
    store.save_frames("jobx", 0, sim.frames, extra={"a": 1})
    fr = store.load_frames("jobx", 0)
    assert fr["depth"].shape[0] == len(sim.frames)
    assert fr["extra"] == {"a": 1}
    assert store.load_frames("jobx", 3) is None
