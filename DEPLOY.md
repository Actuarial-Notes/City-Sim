# Deploying CitySim

Two deployments, because the app has two honest shapes:

| | **Static demo** | **Full app** |
|---|---|---|
| Where | GitHub Pages (free, no account beyond this repo) | any container host |
| Twin | prebaked at build time | built on demand, any place name |
| Ensembles | four scenario presets, baked | launched by the visitor, any size |
| 3D replay | baked runs (representative + worst-loss) | any run, re-simulated on request |
| Physics | the real Python solver, run in CI | the real Python solver, run live |
| Cost | none | a small always-on VM |

The static demo is not a mock: `scripts/export_static.py` runs the same
`MonteCarloRunner` over the same flood module and writes the same payloads the
API would serve — including byte-identical binary replay frames
(`citysim/results/pack.py`). What it cannot do is compute something that was
not baked, so the setup screen offers presets instead of free-form options.

---

## 1. Static demo → GitHub Pages

`.github/workflows/pages.yml` does the whole thing: install, test, run the
solver, publish.

- **Automatic** on every push to the default branch.
- **Manual**: Actions → *Deploy demo to GitHub Pages* → *Run workflow*, where
  you can override the run count, how many runs get 3D replay frames, and
  which presets to bake.

The first run also enables Pages on the repo (`actions/configure-pages` with
`enablement: true`). If your org blocks that, enable it by hand once —
Settings → Pages → Source: **GitHub Actions** — and re-run.

Published at `https://<owner>.github.io/City-Sim/`. Roughly 10–20 minutes of CI
for the default four presets × 96 runs; the site is ~30 MB, of which a visitor
downloads only the scenario and replays they open.

### Building it locally

```bash
python scripts/export_static.py --out site --runs 96
python -m http.server -d site 8080     # → http://localhost:8080
```

Useful flags: `--runs` (ensemble size per preset), `--frame-runs` (how many
worst-loss runs get replay frames — the representative runs are always
included), `--presets present,valves`, `--workers`, `--place`/`--mode` to bake
a different region (`--mode auto` pulls real OSM footprints).

Scenario presets live in `PRESETS` at the top of the script. Each one costs a
full Monte-Carlo batch of build time.

## 2. Full app → any container host

```bash
docker build -t citysim .
docker run --rm -p 8000:8000 -v citysim-data:/data citysim
# → http://localhost:8000
```

`CITYSIM_DATA_DIR` (default `/data` in the image) is where twins, results and
the replay-frame cache are written — mount a volume over it to keep them across
restarts. Run **one** worker: the job manager and twin cache are in-process
state, so a second worker would not see jobs started by the first.

**Fly.io** — `fly.toml` is included:

```bash
fly launch --no-deploy --copy-config
fly volumes create citysim_data --size 1
fly deploy
```

**Render** — `render.yaml` is a blueprint; point Render at the repo (New →
Blueprint) and it builds the Dockerfile. The free plan has no disk and sleeps,
so the first request after a sleep pays for a twin rebuild.

Anything else that runs a container works the same way: build the Dockerfile,
expose `$PORT`, mount a volume at `CITYSIM_DATA_DIR`.

### Sizing

Ensembles are CPU-bound and embarrassingly parallel — the runner uses a process
pool, so cores translate almost linearly into ensemble throughput. A 96-run
flood batch on the 110×150 demo grid takes about a minute on 2 cores. Memory is
modest (a few hundred MB per worker); the replay-frame cache is the disk
consumer at roughly 1–3 MB per cached run.

Nothing in the app authenticates or rate-limits, and any visitor can queue an
arbitrary ensemble. Put it behind auth or a proxy rate-limit before pointing a
crowd at it.
