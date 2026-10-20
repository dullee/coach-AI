"""End-to-end: account + match (+ optional replay) -> MatchSummary -> CoachingReport.

Shared by the CLI (python -m backend.coach) and the web app (backend.app).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.analysis.benchmarks import Benchmarks
from backend.coach.base import CoachModel, CoachResult
from backend.coach.factory import make_coach
from backend.coach.store import save_result
from backend.db import get_engine
from backend.ingest import DeadlockApiClient
from backend.parse import parse_demo, save_match
from backend.static_data import load
from backend.summarize.builder import build_summary
from backend.summarize.contracts import MatchSummary
from backend.summarize.sources import match_data

Progress = Callable[[str], None]


class ReportError(Exception):
    """A problem the user can act on (no data for the match, wrong account, ...)."""


@dataclass
class ReportOutcome:
    summary: MatchSummary
    result: CoachResult
    coach: CoachModel
    report_id: int


def make_report(account_id: int, match_id: int, *, backend: str | None = None, model: str | None = None,
                demo_path: str | Path | None = None, progress: Progress = lambda msg: None) -> ReportOutcome:
    from backend.ingest import NotFound, RateLimited

    coach = make_coach(backend, model)  # raises BackendUnavailable early, before slow work
    engine = get_engine()
    if demo_path:
        progress("Parsing the replay (about 15 s)...")
        parsed = parse_demo(demo_path, requester_account_id=account_id)
        if parsed.match["match_id"] != match_id:
            raise ReportError(f"that replay is match {parsed.match['match_id']}, not {match_id}")
        save_match(parsed, engine)

    progress("Loading match data and benchmarks...")
    with DeadlockApiClient() as client:
        static = load(client=client)
        try:
            meta, replay = match_data(client, match_id, account_id, engine)
        except (NotFound, RateLimited) as e:
            raise ReportError(f"no data for match {match_id} yet ({type(e).__name__}); "
                              "download its replay in Deadlock and try again") from e
        player = next((p for p in meta["players"] if p["account_id"] == account_id), None)
        if player is None:
            raise ReportError(f"account {account_id} did not play in match {match_id}")
        has_rank = (player.get("player_rank_data") or {}).get("initial_display_rank") or (replay and replay.rank_badge)
        fallback = None if has_rank else (client.player_rank(account_id).badge or None)
        progress("Analysing the match...")
        summary = build_summary(meta, account_id, static, Benchmarks.load(), replay, fallback)

    progress(f"Writing the coaching report with {coach.backend}:{coach.model} (10 s to a few minutes)...")
    result = coach.generate(summary)
    report_id = save_result(engine, coach, summary, account_id, result)
    return ReportOutcome(summary, result, coach, report_id)
