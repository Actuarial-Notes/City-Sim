"""Minimal in-process job queue (plan §5.1).

Monte-Carlo batches take minutes, so they run as background jobs with live
progress the UI streams over WebSocket/polling. A thread per job is plenty
here because the heavy lifting inside a job already fans out to worker
processes; swapping in Celery/RQ later only replaces this class.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[["JobHandle"], Any],
               meta: dict | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "kind": kind, "status": "queued",
            "progress": 0.0, "message": "queued", "meta": meta or {},
            "result": None, "error": None,
            "created": time.time(), "finished": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        handle = JobHandle(self, job_id)

        def _run() -> None:
            self.update(job_id, status="running", message="started")
            try:
                result = fn(handle)
                self.update(job_id, status="done", progress=1.0,
                            message="complete", result=result,
                            finished=time.time())
            except Exception as e:  # surface the real failure to the UI
                self.update(job_id, status="error", error=str(e),
                            message=f"failed: {e}", finished=time.time())
                traceback.print_exc()

        threading.Thread(target=_run, daemon=True).start()
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def public(self, job_id: str) -> dict | None:
        job = self.get(job_id)
        if job is None:
            return None
        out = {k: job[k] for k in
               ("id", "kind", "status", "progress", "message", "meta", "error")}
        if job["status"] == "done":
            out["result"] = job["result"]
        return out


class JobHandle:
    def __init__(self, manager: JobManager, job_id: str):
        self.manager = manager
        self.job_id = job_id

    def progress(self, frac: float, message: str = "") -> None:
        self.manager.update(self.job_id, progress=round(float(frac), 4),
                            **({"message": message} if message else {}))
