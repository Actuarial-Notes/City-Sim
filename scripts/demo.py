#!/usr/bin/env python3
"""End-to-end CLI demo (the plan's Phase-0 vertical slice, scripted).

Builds the twin, runs a Monte-Carlo flood ensemble, prints the damage
distribution, and re-runs the same ensemble with backwater valves to show the
mitigation delta — no web UI involved.

    python scripts/demo.py [--runs 48] [--seed 1234] [--climate 1.0]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from citysim.hazards import get_module
from citysim.montecarlo import MonteCarloRunner
from citysim.twin import build_twin


def money(v: float) -> str:
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=48)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--climate", type=float, default=1.0,
                    help="rainfall intensity multiplier (e.g. 1.15 ≈ 2050)")
    ap.add_argument("--place", default="Hamilton demo (lower city)")
    args = ap.parse_args()

    print("── Tier 2: building digital twin")
    twin = build_twin(place=args.place)
    s = twin.summary()
    print(f"   {s['buildings']} buildings · {s['households']} households · "
          f"{s['sewer_nodes']} sewer nodes ({s['combined_sewer_nodes']} combined) · "
          f"grid {s['grid']['nx']}×{s['grid']['ny']} @ {s['grid']['cell_size']} m")

    module = get_module("flood")
    runner = MonteCarloRunner(twin, module, workers=args.workers)

    def progress(done: int, n: int) -> None:
        print(f"\r── Tier 3: flood ensemble  {done}/{n} runs", end="", flush=True)

    options = {}
    if args.climate != 1.0:
        options["climate_factor"] = args.climate
    out = runner.run(n=args.runs, seed=args.seed, options=options,
                     progress_cb=progress)
    print()

    hh = out["stats"]["per_household"]
    tot = out["stats"]["total"]
    print(f"\n   Annual damage per household ({args.runs} sampled years"
          f"{', climate ×%.2f' % args.climate if args.climate != 1.0 else ''}):")
    print(f"     mean {money(hh['mean'])} · median {money(hh['median'])} · "
          f"P95 {money(hh['p95'])} · P99 {money(hh['p99'])}")
    print(f"     VaR95 {money(hh['var95'])} · CVaR95 {money(hh['cvar95'])}")
    print(f"   Region totals: mean {money(tot['mean'])} · worst run {money(tot['max'])}")
    print(f"   Mechanisms: {out['stats']['mechanism_counts']}")

    print("\n── Mitigation scenario: backwater valves on every residential lateral")
    out_mit = runner.run(n=args.runs, seed=args.seed,
                         options=dict(options, backwater_valves=True))
    saved = out["stats"]["total"]["mean"] - out_mit["stats"]["total"]["mean"]
    print(f"   Expected annual damage {money(out_mit['stats']['total']['mean'])} "
          f"(saves {money(saved)}/yr vs unmitigated — the Protective Plumbing "
          f"Program case)")


if __name__ == "__main__":
    main()
