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


def test_death_map_without_replay_uses_api_positions():
    from backend.analysis.visuals import deaths_map

    meta = {"players": [{"account_id": 5, "death_details": [
        {"game_time_s": 120, "death_pos": {"x": 1.0, "y": 2.0, "z": 0}, "killer_pos": {"x": 3.0, "y": 4.0, "z": 0}},
        {"game_time_s": 300, "death_pos": None}]}]}
    d = deaths_map(meta, 5, None)
    assert d == [{"t": 120, "x": 1.0, "y": 2.0, "alone": None, "nearest_ally": None, "killer": {"x": 3.0, "y": 4.0}}]


def test_map_placement_matches_objective_markers():
    """The placement formula reproduces the map asset's relative positions (values captured 2026-10-21)."""
    from backend.analysis.visuals import MAP_OFFSET

    radius = 10752
    walkers = {  # world (x, y) of team-1 walkers in a stored replay -> relative (left, top) from /v1/assets/map
        (-5416.0, 5024.0): (0.1981, 0.2164), (1288.0, 3472.0): (0.5099, 0.2885), (6443.9375, 4776.375): (0.7497, 0.2279)}
    for (x, y), (left, top) in walkers.items():
        assert abs(MAP_OFFSET + x / (2 * radius) - left) < 0.001 and abs(MAP_OFFSET - y / (2 * radius) - top) < 0.001


def test_data_bundle_round_trip(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("data_bundle", "scripts/data_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "src"
    (src / "demos").mkdir(parents=True)
    (src / "demos" / "1.dem").write_bytes(b"PBDEMS2\x00" + b"x" * 100)
    (src / "ml" / "teacher").mkdir(parents=True)
    (src / "ml" / "teacher" / "t.jsonl").write_text('{"id": 1}\n')
    (src / "coach.db").write_bytes(b"db")
    (src / "cache").mkdir()
    (src / "cache" / "skip.json").write_text("{}")          # rebuilt automatically: not packed
    (src / "uploads").mkdir()
    (src / "uploads" / "x.dem.part").write_bytes(b"partial")  # unfinished files are skipped
    z = mod.pack(tmp_path / "b.zip", with_lake=False, data_dir=src)
    dst = tmp_path / "dst"
    mod.unpack(z, data_dir=dst)
    got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert got == ["coach.db", "demos/1.dem", "ml/teacher/t.jsonl"]
    assert (dst / "demos" / "1.dem").read_bytes() == (src / "demos" / "1.dem").read_bytes()
