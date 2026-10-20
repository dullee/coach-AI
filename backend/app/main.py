"""Prototype web app: pick one of your matches, get a coaching report.

    python -m backend.app        -> http://127.0.0.1:8000

Prototype scope (developer request, 2026-10-20): no accounts/tiers, one HTML
page instead of the Next.js frontend, in-process jobs instead of a worker.
Runs on localhost only.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from backend import config, db
from backend.app import jobs
from backend.app.replays import local_replays
from backend.coach.factory import BACKENDS, BackendUnavailable, make_coach
from backend.coach.service import make_report
from backend.db import get_engine
from backend.ingest import ApiError, DeadlockApiClient
from backend.static_data import load

app = FastAPI(title="Deadlock coach (prototype)")
STATIC = Path(__file__).parent / "static"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


def _local_server_up() -> bool:
    """The local backend needs a running OpenAI-compatible server (e.g. Ollama); check quickly."""
    import httpx

    from backend.coach.local import LocalCoach

    try:
        return httpx.get(f"{LocalCoach().base_url}/models", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/api/backends")
def backends() -> dict:
    available, models = [], {}
    for b in BACKENDS:
        try:
            coach = make_coach(b)
        except BackendUnavailable:
            continue
        if b == "local" and not _local_server_up():
            continue
        available.append(b)
        models[b] = coach.model
    wanted = os.environ.get("COACH_BACKEND")
    default = wanted if wanted in available else (available[0] if available else "gemini")
    return {"available": available, "default": default, "models": models}


@app.get("/api/matches")
def matches(account_id: int) -> dict:
    engine = get_engine()
    static = load()
    rows: dict[int, dict] = {}
    with DeadlockApiClient() as client:
        try:
            history = client.match_history(account_id)
        except ApiError:
            history = []
    for m in history:
        if m.game_mode != 1:
            continue
        rows[m.match_id] = {
            "match_id": m.match_id, "hero": static.hero_name(m.hero_id),
            "date": datetime.fromtimestamp(m.start_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "result": "win" if m.match_result == m.player_team else "loss",
            "kda": f"{m.player_kills}/{m.player_deaths}/{m.player_assists}",
            "duration": f"{m.match_duration_s // 60}:{m.match_duration_s % 60:02d}",
        }
    with engine.connect() as conn:
        parsed_rows = conn.execute(
            select(db.matches.c.match_id, db.match_players.c.hero_id, db.match_players.c.slot,
                   db.match_players.c.team, db.matches.c.winning_team, db.matches.c.duration_s)
            .join(db.match_players, db.match_players.c.match_id == db.matches.c.match_id)
            .where(db.match_players.c.account_id == account_id)).all()
        final_kda = {}
        for mid, _, slot, *_ in parsed_rows:
            snap = conn.execute(select(db.stat_snapshots.c.kills, db.stat_snapshots.c.deaths, db.stat_snapshots.c.assists)
                                .where((db.stat_snapshots.c.match_id == mid) & (db.stat_snapshots.c.slot == slot))
                                .order_by(db.stat_snapshots.c.time_s.desc())).first()
            if snap:
                final_kda[mid] = "/".join(map(str, snap))
        parsed = [(r[0], r[1]) for r in parsed_rows]
        reported = {r[0] for r in conn.execute(
            select(db.coaching_reports.c.match_id).where(
                (db.coaching_reports.c.account_id == account_id) & db.coaching_reports.c.valid))}
    for match_id, hero_id, _slot, team, winner, duration in parsed_rows:
        dur = int(duration or 0)
        rows.setdefault(match_id, {
            "match_id": match_id, "hero": static.hero_name(hero_id),
            "result": None if winner is None else ("win" if winner == team else "loss"),
            "kda": final_kda.get(match_id), "duration": f"{dur // 60}:{dur % 60:02d}" if dur else None,
        })
    local = local_replays()
    for match_id in local:
        rows.setdefault(match_id, {"match_id": match_id, "hero": None})
    parsed_ids = {m for m, _ in parsed}
    out = []
    for match_id in sorted(rows, reverse=True)[:40]:
        r = rows[match_id]
        r["replay"] = "parsed" if match_id in parsed_ids else ("on disk" if match_id in local else None)
        r["has_report"] = match_id in reported
        out.append(r)
    return {"matches": out}


class ReportRequest(BaseModel):
    account_id: int
    match_id: int
    backend: str | None = None
    model: str | None = None


def _report_job(account_id: int, match_id: int, backend: str | None, model: str | None, progress) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        parsed = conn.execute(select(db.match_players.c.match_id).where(
            (db.match_players.c.match_id == match_id) & (db.match_players.c.account_id == account_id))).first()
    demo = None if parsed else local_replays().get(match_id)
    outcome = make_report(account_id, match_id, backend=backend, model=model, demo_path=demo, progress=progress)
    return _report_payload(outcome.report_id, outcome.summary.model_dump(mode="json", exclude_none=True),
                           outcome.result.report.model_dump(mode="json") if outcome.result.report else None,
                           outcome.result.valid, outcome.result.problems, outcome.coach.backend, outcome.coach.model,
                           outcome.result.cost_usd, outcome.result.latency_s)


def _report_payload(report_id, summary, report, valid, problems, backend, model, cost, latency) -> dict:
    return {"report_id": report_id, "summary": summary, "report": report, "valid": valid, "problems": problems,
            "backend": backend, "model": model, "cost_usd": cost, "latency_s": latency,
            "feedback": _feedback_for(report_id)}


def _feedback_for(report_id: int) -> dict:
    t = db.report_feedback
    with get_engine().connect() as conn:
        rows = conn.execute(t.select().where(t.c.report_id == report_id).order_by(t.c.id)).mappings().all()
    return {str(r["priority_index"]): {"rating": r["rating"], "comment": r["comment"]} for r in rows}


@app.post("/api/reports")
def start_report(req: ReportRequest) -> dict:
    try:
        make_coach(req.backend, req.model)
    except BackendUnavailable as e:
        raise HTTPException(400, str(e)) from e
    job = jobs.submit(_report_job, req.account_id, req.match_id, req.backend, req.model)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return {"status": job.status, "progress": job.progress, "result": job.result, "error": job.error}


@app.get("/api/reports/latest")
def latest_report(account_id: int, match_id: int) -> dict:
    t = db.coaching_reports
    with get_engine().connect() as conn:
        r = conn.execute(t.select().where((t.c.account_id == account_id) & (t.c.match_id == match_id) & t.c.valid)
                         .order_by(t.c.id.desc())).mappings().first()
    if r is None:
        raise HTTPException(404, "no report yet")
    return _report_payload(r["id"], r["summary_json"], r["report_json"], r["valid"], r["problems"], r["backend"],
                           r["model"], r["cost_usd"], r["latency_s"])


@app.post("/api/upload")
def upload(file: UploadFile = File(...), account_id: int = Form(...)) -> dict:
    """Save an uploaded .dem; returns its match ID. Parsing happens when a report is requested."""
    from boon import Demo

    dest_dir = config.data_dir() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f"upload_{os.getpid()}.part"
    with open(tmp, "wb") as out:
        shutil.copyfileobj(file.file, out, 1 << 20)
    try:
        match_id = Demo(str(tmp), preload=False).match_id
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"not a valid Deadlock replay ({e})") from e
    if match_id is None:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "this replay has no match ID")
    tmp.replace(dest_dir / f"{match_id}.dem")
    return {"match_id": match_id}


class FeedbackRequest(BaseModel):
    report_id: int
    priority_index: int          # 0-based priority; -1 = whole report
    rating: int | None = None    # +1 / -1 / None (comment only)
    comment: str | None = None


@app.post("/api/feedback")
def feedback(req: FeedbackRequest) -> dict:
    """Store (or replace) the player's rating/comment for one priority of a report."""
    from sqlalchemy import delete

    if req.rating not in (None, 1, -1):
        raise HTTPException(400, "rating must be 1, -1 or null")
    t = db.report_feedback
    engine = get_engine()
    with engine.begin() as conn:
        if conn.execute(select(db.coaching_reports.c.id).where(db.coaching_reports.c.id == req.report_id)).first() is None:
            raise HTTPException(404, "unknown report")
        conn.execute(delete(t).where((t.c.report_id == req.report_id) & (t.c.priority_index == req.priority_index)))
        if req.rating is not None or (req.comment or "").strip():
            conn.execute(t.insert().values(report_id=req.report_id, priority_index=req.priority_index,
                                           rating=req.rating, comment=(req.comment or "").strip()[:2000] or None))
    return {"feedback": _feedback_for(req.report_id)}


def _analysis_deps():
    from backend.analysis.benchmarks import Benchmarks

    return get_engine(), load(), Benchmarks.load()


@app.get("/api/progress")
def progress_view(account_id: int, last_n: int = 10) -> dict:
    from backend.analysis.progress import progress

    engine, static, bench = _analysis_deps()
    with DeadlockApiClient() as client:
        return progress(client, engine, static, bench, account_id, last_n=min(max(last_n, 2), 30))


@app.get("/api/followup")
def followup_view(account_id: int, match_id: int) -> dict:
    from backend.analysis.progress import follow_up

    engine, static, bench = _analysis_deps()
    with DeadlockApiClient() as client:
        return {"follow_up": follow_up(client, engine, static, bench, account_id, match_id)}
