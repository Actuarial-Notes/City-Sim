#!/usr/bin/env python3
"""Bake CitySim into a static site (the public demo deployment).

There is no Python on GitHub Pages, so the solver runs *here*, at build time:
this script builds the twin, runs a full Monte-Carlo ensemble per scenario
preset, and writes out exactly the payloads the SPA would otherwise fetch from
the API — including the same binary replay frames (`citysim.results.pack`).
The front end reads them through `StaticBackend` in `static/api.js`, so the
deployed demo runs the real physics; it just cannot launch *new* ensembles.

    python scripts/export_static.py --out site --runs 96

Layout produced::

    site/index.html                       (+ injected static-mode config)
    site/static/*.js                      every script index.html references
    site/data/manifest.json               twin summary, hazards, presets
    site/data/scene.json                  3D scene (terrain, buildings, streets)
    site/data/assumptions.json            = GET /api/hazards/flood/assumptions
    site/data/twin_params.json            = GET /api/twin/params
    site/data/<preset>/results.json       = GET /api/results/{job}
    site/data/<preset>/assumptions.json   = GET /api/results/{job}/assumptions
    site/data/<preset>/building_losses.bin.gz   float32 (n_runs × n_buildings)
    site/data/<preset>/runs/<n>.bin.gz    = GET /api/results/{job}/runs/{n}/frames
"""

from __future__ import annotations

import argparse
import builtins
import functools
import gzip
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# a baked export is minutes of solver time; keep the log live when it is piped
# into a CI job or a file rather than a terminal
print = functools.partial(builtins.print, flush=True)     # noqa: A001

from citysim.hazards import get_module                       # noqa: E402
from citysim.hazards.flood import assumptions as flood_assumptions   # noqa: E402
from citysim.hazards.flood.module import FloodModule         # noqa: E402
from citysim.hazards.registry import list_modules            # noqa: E402
from citysim.montecarlo import MonteCarloRunner              # noqa: E402
from citysim.results import pack_frames                      # noqa: E402
from citysim.twin import build_twin, scene_payload           # noqa: E402
from citysim.twin import params as twin_params               # noqa: E402

STATIC_DIR = Path(__file__).resolve().parents[1] / "citysim" / "server" / "static"
REPO_URL = "https://github.com/Actuarial-Notes/City-Sim"

#: The scenarios a visitor can switch between. Each is a full ensemble, so
#: adding one costs a full Monte-Carlo batch of build time.
PRESETS = [
    {
        "id": "present",
        "label": "Present-day climate",
        "description": "Today's IDF statistics, no household mitigation — the baseline.",
        "options": {},
    },
    {
        "id": "valves",
        "label": "Present day + backwater valves",
        "description": "Backwater valves on every residential lateral (Hamilton's "
                       "Protective Plumbing Program) — severs the sewer-backup pathway.",
        "options": {"backwater_valves": True},
    },
    {
        "id": "climate2050",
        "label": "2050 climate (+15% intensity)",
        "description": "IDF_CC-style scaling of storm intensity, no mitigation.",
        "options": {"climate_factor": 1.15},
    },
    {
        "id": "climate2050_valves",
        "label": "2050 climate + backwater valves",
        "description": "Does the mitigation hold up against a 2050-shifted storm regime?",
        "options": {"climate_factor": 1.15, "backwater_valves": True},
    },
]


def write_json(path: Path, payload: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload).encode()
    path.write_bytes(blob)
    return len(blob)


def write_gz(path: Path, blob: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    packed = gzip.compress(blob, 9)
    path.write_bytes(packed)
    return len(packed)


def results_payload(job_id: str, twin_id: str, output: dict) -> dict:
    """The `GET /api/results/{job_id}` body, built without a running server."""
    matrix = output["building_matrix"]
    return {
        "job_id": job_id,
        "twin_id": twin_id,
        "stats": output["stats"],
        "scenarios": output["scenarios"],
        "runs": [{k: v for k, v in r.items() if k != "building_losses"}
                 for r in output["runs"]],
        "exceedance": output["exceedance"],
        "building_ids": output["building_ids"],
        "building_mean_loss": np.round(matrix.mean(axis=0), 2).tolist(),
        "building_p95_loss": np.round(np.percentile(matrix, 95, axis=0), 2).tolist(),
        "run_index": [{
            "run": r["run"],
            "total_loss": r["total_loss"],
            "per_household_mean": r["per_household_mean"],
            "return_period": s["return_period"],
            "total_mm": s["total_mm"],
            "duration_h": s["duration_h"],
        } for r, s in zip(output["runs"], output["scenarios"])],
    }


def runs_to_bake(output: dict, n_top: int) -> list[int]:
    """Runs the UI can open: the representative runs plus the worst-loss table."""
    reps = list(output["stats"]["representative_runs"].values())
    top = [r["run"] for r in sorted(output["runs"],
                                    key=lambda r: -r["total_loss"])[:n_top]]
    return sorted(set(int(r) for r in reps + top))


def copy_front_end(out: Path, manifest_url: str) -> None:
    """Copy the SPA, and prove that every script it references came along.

    The file list used to be hardcoded, which meant adding a script to
    index.html silently produced a deploy that 404s. It is now derived from the
    page itself.
    """
    (out / "static").mkdir(parents=True, exist_ok=True)
    html = (STATIC_DIR / "index.html").read_text()

    referenced = re.findall(r'<script src="static/([^"]+)"', html)
    if not referenced:
        raise SystemExit("index.html references no scripts — check the markup")
    for name in referenced:
        src = STATIC_DIR / name
        if not src.exists():
            raise SystemExit(f"index.html references static/{name}, which does not exist")
        shutil.copy(src, out / "static" / name)

    marker = "<!--CITYSIM_STATIC_CONFIG-->"
    if marker not in html:
        raise SystemExit("index.html is missing the CITYSIM_STATIC_CONFIG marker")
    html = html.replace(
        marker, f'<script>window.CITYSIM_STATIC = "{manifest_url}";</script>')
    (out / "index.html").write_text(html)
    # Pages would otherwise hand the whole tree to Jekyll, which drops files
    # it does not recognise
    (out / ".nojekyll").write_text("")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site", help="output directory")
    ap.add_argument("--runs", type=int, default=96, help="Monte-Carlo runs per preset")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--frame-runs", type=int, default=8,
                    help="worst-loss runs to bake replay frames for, per preset "
                         "(the representative runs are always included)")
    ap.add_argument("--place", default="Hamilton (lower city)")
    ap.add_argument("--mode", default="hamilton",
                    choices=["hamilton", "synthetic", "auto"])
    ap.add_argument("--presets", default="",
                    help="comma-separated preset ids (default: all)")
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    wanted = [p.strip() for p in args.presets.split(",") if p.strip()]
    presets = [p for p in PRESETS if not wanted or p["id"] in wanted]
    if not presets:
        raise SystemExit(f"no presets matched {args.presets!r}")

    t0 = time.time()
    print(f"── Tier 2: building twin ({args.mode})")
    twin = build_twin(place=args.place, mode=args.mode)
    summary = twin.summary()
    print(f"   {summary['buildings']} buildings · {summary['households']} households · "
          f"{summary['sewer_nodes']} sewer nodes")

    # the viewer's scene payload is the API's, byte for byte
    n_bytes = write_json(out / "data" / "scene.json", scene_payload(twin))
    print(f"   scene.json {n_bytes/1e3:.0f} kB")

    # The Setup tabs render from these. A prebaked deploy cannot re-run the
    # solver, but it can and should still show every curve, table and citation —
    # the assumptions are not adjustable there, they are not hidden.
    write_json(out / "data" / "assumptions.json", flood_assumptions.spec())
    write_json(out / "data" / "twin_params.json", twin_params.spec())

    module = get_module("flood")
    runner = MonteCarloRunner(twin, module, workers=args.workers)
    manifest_presets = []

    for preset in presets:
        print(f"\n── Tier 3: ensemble '{preset['id']}' — {args.runs} runs "
              f"{preset['options'] or '(baseline)'}")

        def progress(done: int, n: int) -> None:
            print(f"\r   {done}/{n} runs", end="")

        output = runner.run(n=args.runs, seed=args.seed,
                            options=dict(preset["options"]), progress_cb=progress)
        print(f"  ({output['stats']['elapsed_s']}s)")

        pdir = out / "data" / preset["id"]
        write_json(pdir / "results.json",
                   results_payload(preset["id"], twin.id, output))
        # what this ensemble actually ran under — read back from its scenarios,
        # exactly as GET /api/results/{job}/assumptions does
        resolved = FloodModule.resolved(output["scenarios"][0])
        write_json(pdir / "assumptions.json", {
            "resolved": resolved,
            "deviations": flood_assumptions.deviations(resolved),
            "twin_params": twin.params,
            "twin_deviations": twin_params.deviations(twin.params),
            "seed": args.seed,
            "n_runs": args.runs,
        })
        write_gz(pdir / "building_losses.bin.gz",
                 output["building_matrix"].astype("<f4").tobytes())

        bake = runs_to_bake(output, args.frame_runs)
        total = 0
        for i, run in enumerate(bake, 1):
            scenario = output["scenarios"][run]
            sim = module.simulate(twin, scenario, record_frames=True)
            blob = pack_frames(
                depth=np.stack([f["depth"] for f in sim.frames]),
                t=[f["t"] for f in sim.frames],
                rain_mmh=[f["rain_mmh"] for f in sim.frames],
                surcharging=[f["surcharging"] for f in sim.frames],
                scenario=scenario,
                run_summary={k: v for k, v in output["runs"][run].items()
                             if k != "building_losses"},
            )
            total += write_gz(pdir / "runs" / f"{run}.bin.gz", blob)
            print(f"\r   replay frames {i}/{len(bake)}", end="")
        print(f"  ({total/1e6:.1f} MB for {len(bake)} runs)")

        hh = output["stats"]["per_household"]
        manifest_presets.append({
            **{k: preset[k] for k in ("id", "label", "description", "options")},
            "n_runs": args.runs,
            "frames_runs": bake,
            "headline": {"mean": hh["mean"], "p95": hh["p95"]},
        })

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo_url": REPO_URL,
        "twin": summary,
        "hazards": list_modules(),
        "n_runs": args.runs,
        "seed": args.seed,
        "presets": manifest_presets,
    }
    write_json(out / "data" / "manifest.json", manifest)
    copy_front_end(out, "data/manifest.json")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n── static site → {out}/  ({size/1e6:.1f} MB, "
          f"{time.time()-t0:.0f}s total)")
    print(f"   preview:  python -m http.server -d {out} 8080")


if __name__ == "__main__":
    main()
