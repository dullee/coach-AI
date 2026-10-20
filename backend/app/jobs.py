"""In-process background jobs (prototype stand-in for the RQ/Celery worker in the plan).

Reports take 10 s to several minutes (replay parsing + model call), longer
than a web request should, so the page starts a job and polls its status.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, Job] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | error
    progress: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


def submit(fn, *args, **kwargs) -> Job:
    job = Job(uuid.uuid4().hex[:12])
    with _lock:
        _jobs[job.id] = job

    def run():
        job.status = "running"
        try:
            job.result = fn(*args, progress=job.progress.append, **kwargs)
            job.status = "done"
        except Exception as e:  # shown to the user; full trace in the server log
            traceback.print_exc()
            job.error = str(e) or type(e).__name__
            job.status = "error"

    _pool.submit(run)
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)
