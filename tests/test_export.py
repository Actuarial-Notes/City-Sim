"""The static export is the public deployment, so it gets the same scrutiny as
the API: the files the front end fetches must exist, decode, and carry the same
payload shapes the live endpoints return."""

import gzip
import json
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "site"
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export_static.py"),
         "--out", str(out), "--runs", "4", "--frame-runs", "1",
         "--presets", "present,valves", "--workers", "2"],
        check=True, cwd=REPO, capture_output=True, text=True, timeout=900)
    return out


def test_front_end_is_copied_and_switched_to_static_mode(site):
    html = (site / "index.html").read_text()
    assert 'window.CITYSIM_STATIC = "data/manifest.json"' in html
    assert "<!--CITYSIM_STATIC_CONFIG-->" not in html    # placeholder consumed
    # relative script paths — the site is served from a project subpath
    assert 'src="static/app.js"' in html
    for name in ("app.js", "api.js", "viewer.js"):
        assert (site / "static" / name).exists()
    assert (site / ".nojekyll").exists()


def test_manifest_describes_every_baked_preset(site):
    manifest = json.loads((site / "data" / "manifest.json").read_text())
    assert manifest["twin"]["buildings"] > 0
    assert [h["name"] for h in manifest["hazards"]][0] == "flood"
    ids = [p["id"] for p in manifest["presets"]]
    assert ids == ["present", "valves"]
    for p in manifest["presets"]:
        assert p["frames_runs"], "every preset needs replayable runs"
        assert (site / "data" / p["id"] / "results.json").exists()


def test_scene_payload_matches_the_api_shape(site):
    scene = json.loads((site / "data" / "scene.json").read_text())
    assert len(scene["dtm"]) == scene["shape"][0] * scene["shape"][1]
    assert scene["buildings"] and scene["sewer"]["nodes"] and scene["sewer"]["conduits"]


def test_results_payload_matches_the_api_shape(site):
    res = json.loads((site / "data" / "present" / "results.json").read_text())
    assert res["stats"]["n_runs"] == 4
    assert len(res["run_index"]) == 4
    assert len(res["exceedance"]) == 4
    assert len(res["building_mean_loss"]) == len(res["building_ids"])
    assert len(res["building_p95_loss"]) == len(res["building_ids"])
    # every run the UI can open must have frames on disk
    manifest = json.loads((site / "data" / "manifest.json").read_text())
    baked = next(p for p in manifest["presets"] if p["id"] == "present")["frames_runs"]
    assert set(res["stats"]["representative_runs"].values()) <= set(baked)
    for run in baked:
        assert (site / "data" / "present" / "runs" / f"{run}.bin.gz").exists()


def test_building_loss_matrix_is_runs_by_buildings(site):
    res = json.loads((site / "data" / "present" / "results.json").read_text())
    raw = gzip.decompress((site / "data" / "present" / "building_losses.bin.gz").read_bytes())
    matrix = np.frombuffer(raw, dtype="<f4")
    n_runs, n_buildings = res["stats"]["n_runs"], len(res["building_ids"])
    assert matrix.size == n_runs * n_buildings
    # the client slices columns out of this; means must agree with the payload
    means = matrix.reshape(n_runs, n_buildings).mean(axis=0)
    assert np.allclose(means, res["building_mean_loss"], atol=0.01)


def test_replay_frames_use_the_api_binary_format(site):
    manifest = json.loads((site / "data" / "manifest.json").read_text())
    run = next(p for p in manifest["presets"] if p["id"] == "present")["frames_runs"][0]
    blob = gzip.decompress(
        (site / "data" / "present" / "runs" / f"{run}.bin.gz").read_bytes())
    hlen = struct.unpack("<I", blob[:4])[0]
    header = json.loads(blob[4:4 + hlen])
    body = blob[4 + hlen:]
    assert len(body) == header["n_frames"] * header["ny"] * header["nx"] * 2
    assert len(header["t"]) == header["n_frames"]
    assert len(header["rain_mmh"]) == header["n_frames"]
    assert len(header["surcharging"]) == header["n_frames"]
    assert header["scenario"]["run"] == run
    assert "total_loss" in header["run_summary"]
