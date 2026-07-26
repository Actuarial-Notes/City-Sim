import struct
import time
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # isolate the app's on-disk stores for the test session
    import citysim.server.app as appmod
    from citysim.results import ResultsStore
    from citysim.twin.store import TwinStore

    root = tmp_path_factory.mktemp("data")
    appmod.twin_store = TwinStore(root / "twins")
    appmod.results_store = ResultsStore(root / "results")
    appmod._twin_cache.clear()
    return TestClient(appmod.app)


def _wait(client, job_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.3)
    raise TimeoutError


def test_hazard_registry_lists_flood_and_roadmap(client):
    hazards = client.get("/api/hazards").json()
    names = {h["name"]: h["available"] for h in hazards}
    assert names["flood"] is True
    assert names["wind"] is False        # roadmap hazards surface as disabled


def test_assumption_spec_endpoint(client):
    """The Setup screen renders itself from this, so its shape is a contract."""
    spec = client.get("/api/hazards/flood/assumptions").json()
    assert spec["knobs"] and spec["sources"] and spec["limitations"]
    assert set(spec["defaults"]) == {k["id"] for k in spec["knobs"]}
    assert spec["curves"]["idf"]["points"]
    assert len(spec["curves"]["depth_damage"]["series"]) == 5
    assert client.get("/api/hazards/wind/assumptions").status_code == 404


def test_twin_param_spec_endpoint(client):
    spec = client.get("/api/twin/params").json()
    assert spec["params"] and spec["groups"]
    assert set(spec["defaults"]) == {p["id"] for p in spec["params"]}
    # geography is published for display but marked not-editable
    assert any(p.get("readonly") for p in spec["params"] if p["group"] == "region")


def test_full_pipeline(client):
    # build twin
    r = client.post("/api/twin", json={"place": "Hamilton demo (lower city)",
                                       "mode": "synthetic"}).json()
    job = _wait(client, r["job_id"])
    assert job["status"] == "done", job.get("error")
    twin_id = job["result"]["id"]

    scene = client.get(f"/api/twin/{twin_id}/scene").json()
    assert len(scene["dtm"]) == scene["shape"][0] * scene["shape"][1]
    assert scene["buildings"] and scene["sewer"]["nodes"]
    # the inspector needs every physical attribute, not the old subset
    b = scene["buildings"][0]
    for key in ("foundation", "basement_depth", "first_floor_height",
                "contents_value", "centroid", "dwelling_units"):
        assert key in b, key

    # simulate, with a couple of assumptions moved off default
    r = client.post("/api/simulate", json={
        "twin_id": twin_id, "hazard": "flood", "n_runs": 6, "workers": 2,
        "options": {"climate_factor": 1.15, "ddc_severity": 1.4},
    }).json()
    job = _wait(client, r["job_id"], timeout=300)
    assert job["status"] == "done", job.get("error")
    job_id = r["job_id"]

    # what the ensemble actually ran under, read back from its scenarios
    used = client.get(f"/api/results/{job_id}/assumptions").json()
    assert used["resolved"]["climate_factor"] == 1.15
    assert used["resolved"]["ddc_severity"] == 1.4
    assert {d["id"] for d in used["deviations"]} == {"climate_factor", "ddc_severity"}
    assert used["n_runs"] == 6

    res = client.get(f"/api/results/{job_id}").json()
    assert res["stats"]["n_runs"] == 6
    assert len(res["building_mean_loss"]) == len(scene["buildings"])
    assert len(res["run_index"]) == 6

    # per-building distribution
    bid = res["building_ids"][0]
    d = client.get(f"/api/results/{job_id}/buildings/{bid}").json()
    assert len(d["losses"]) == 6

    # replay frames: binary framing decodes and matches declared shape
    run = res["stats"]["representative_runs"]["max"]
    blob = client.get(f"/api/results/{job_id}/runs/{run}/frames").content
    hlen = struct.unpack("<I", blob[:4])[0]
    header = json.loads(blob[4:4 + hlen])
    body = blob[4 + hlen:]
    assert len(body) == header["n_frames"] * header["ny"] * header["nx"] * 2
    assert len(header["t"]) == header["n_frames"]
    # second request must hit the cache and be identical
    blob2 = client.get(f"/api/results/{job_id}/runs/{run}/frames").content
    assert blob == blob2


def test_twin_params_reach_the_builder(client):
    r = client.post("/api/twin", json={
        "place": "param test", "mode": "synthetic",
        "params": {"cost_index": 1.5, "basement_rate_pre1990": 0.2},
    }).json()
    job = _wait(client, r["job_id"])
    assert job["status"] == "done", job.get("error")
    used = job["result"]["params"]
    assert used["cost_index"] == 1.5
    assert used["basement_rate_pre1990"] == 0.2
    # fewer basements than the default 0.9 rate would produce
    assert job["result"]["stock"]["basement_share"] < 0.5


def test_out_of_range_twin_params_are_clamped_not_rejected(client):
    r = client.post("/api/twin", json={
        "place": "clamp test", "mode": "synthetic",
        "params": {"cost_index": 999.0},
    }).json()
    job = _wait(client, r["job_id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["params"]["cost_index"] == 2.0


def test_missing_resources_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/results/nope").status_code == 404
    assert client.get("/api/results/nope/assumptions").status_code == 404
    assert client.get("/api/twin/nope/scene").status_code == 404
