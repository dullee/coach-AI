"""Save coaching results and per-call token/cost records to the database."""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.engine import Engine

from backend import db
from backend.coach.base import CallRecord, CoachModel, CoachResult
from backend.summarize.contracts import MatchSummary


def save_result(engine: Engine, coach: CoachModel, summary: MatchSummary, account_id: int | None,
                result: CoachResult | None, calls: list[CallRecord] | None = None) -> int:
    """Store one report (result None = refused) and all its model calls. Returns the report id."""
    calls = calls if calls is not None else (result.calls if result else [])
    with engine.begin() as conn:
        report_id = conn.execute(db.coaching_reports.insert().values(
            match_id=summary.match_id, account_id=account_id, backend=coach.backend, model=coach.model,
            analysis_level=summary.analysis_level, valid=bool(result and result.valid),
            summary_json=summary.model_dump(mode="json", exclude_none=True),
            report_json=result.report.model_dump(mode="json") if result and result.report else None,
            problems=result.problems if result else ["refused"],
            cost_usd=result.cost_usd if result else None,
            latency_s=result.latency_s if result else None,
        )).inserted_primary_key[0]
        if calls:
            conn.execute(db.coach_calls.insert(), [{**asdict(c), "report_id": report_id} for c in calls])
    return report_id
