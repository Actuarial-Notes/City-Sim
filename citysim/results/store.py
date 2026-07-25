"""Job/run persistence (plan §5.7 storage design).

Per Monte-Carlo job we store the sampled parameter vectors, per-run summary
losses, the per-building loss matrix, and reduced statistics. Replay frames
are *derived data*: any run re-simulates deterministically from its stored
scenario, so frames are materialised on demand and cached per run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class ResultsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def save_job(self, job_id: str, twin_id: str, output: dict) -> None:
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job_id,
            "twin_id": twin_id,
            "stats": output["stats"],
            "scenarios": output["scenarios"],
            "runs": [{k: v for k, v in r.items() if k != "building_losses"}
                     for r in output["runs"]],
            "exceedance": output["exceedance"],
            "building_ids": output["building_ids"],
        }
        (d / "job.json").write_text(json.dumps(payload))
        np.savez_compressed(d / "building_losses.npz",
                            matrix=output["building_matrix"].astype(np.float32))

    def load_job(self, job_id: str) -> dict:
        d = self.job_dir(job_id)
        payload = json.loads((d / "job.json").read_text())
        payload["building_matrix"] = np.load(d / "building_losses.npz")["matrix"]
        return payload

    def exists(self, job_id: str) -> bool:
        return (self.job_dir(job_id) / "job.json").exists()

    def list_jobs(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            f = d / "job.json"
            if f.exists():
                p = json.loads(f.read_text())
                out.append({"job_id": p["job_id"], "twin_id": p["twin_id"],
                            "hazard": p["stats"].get("hazard"),
                            "n_runs": p["stats"].get("n_runs")})
        return out

    # ---------------------------------------------------------- frame cache
    def frames_path(self, job_id: str, run: int) -> Path:
        return self.job_dir(job_id) / f"frames_{run}.npz"

    def save_frames(self, job_id: str, run: int, frames: list[dict],
                    extra: dict | None = None) -> None:
        depth = np.stack([f["depth"] for f in frames])
        np.savez_compressed(
            self.frames_path(job_id, run),
            depth=depth,
            t=np.array([f["t"] for f in frames], dtype=np.float32),
            rain=np.array([f["rain_mmh"] for f in frames], dtype=np.float32),
            node_head=np.stack([f["node_head"] for f in frames]),
            surcharging=np.array(
                json.dumps([f["surcharging"] for f in frames]), dtype="U"),
            extra=np.array(json.dumps(extra or {}), dtype="U"),
        )

    def load_frames(self, job_id: str, run: int) -> dict | None:
        p = self.frames_path(job_id, run)
        if not p.exists():
            return None
        z = np.load(p)
        return {
            "depth": z["depth"],
            "t": z["t"],
            "rain": z["rain"],
            "node_head": z["node_head"],
            "surcharging": json.loads(str(z["surcharging"])),
            "extra": json.loads(str(z["extra"])),
        }
