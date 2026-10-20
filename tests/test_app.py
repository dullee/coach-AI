"""Prototype web app: page, backend list, upload validation, background jobs."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.app import jobs
from backend.app.main import app

client = TestClient(app)


def test_index_and_backends():
    assert client.get("/").status_code == 200
    b = client.get("/api/backends").json()
    assert set(b["available"]) <= {"claude", "gemini", "local"} and b["default"] in ("claude", "gemini", "local")


def test_upload_rejects_non_replays():
    r = client.post("/api/upload", files={"file": ("x.dem", b"not a demo")}, data={"account_id": "1"})
    assert r.status_code == 400


def test_unknown_backend_is_rejected():
    r = client.post("/api/reports", json={"account_id": 1, "match_id": 1, "backend": "nope"})
    assert r.status_code == 400


def test_jobs_report_progress_and_errors():
    def work(x, progress):
        progress("step one")
        return {"x": x}

    def boom(progress):
        raise ValueError("bad match")

    ok, bad = jobs.submit(work, 5), jobs.submit(boom)
    for _ in range(50):
        if ok.status in ("done", "error") and bad.status in ("done", "error"):
            break
        time.sleep(0.05)
    assert ok.status == "done" and ok.result == {"x": 5} and ok.progress == ["step one"]
    assert bad.status == "error" and bad.error == "bad match"
    j = client.get(f"/api/jobs/{ok.id}").json()
    assert j["status"] == "done" and j["result"] == {"x": 5}
    assert client.get("/api/jobs/doesnotexist").status_code == 404
