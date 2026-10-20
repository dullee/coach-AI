"""Phase 3 "Done when" run: coaching reports for 20 real player-matches.

    python scripts/phase3_check.py --account-id <your id> [--n 20] [--backend claude|gemini|local] [--model M]

Matches: your own matches first (API history + parsed replays), then players
from the anonymized fixture matches in tests/data/api/ to reach N. Every
report is validated (schema, issue ids, invented numbers), all calls are
logged with tokens and cost, and a sheet for spot-checking is written to
data/review/phase3_reports.md after every report. Rerunning reuses valid
reports from earlier runs with the same model (use --fresh to redo them);
--offline only rebuilds the sheet from stored reports.
This calls the model API: Claude Haiku 5.5 costs roughly $0.001-0.003 per
report; the Gemini free tier is free but slow (1-4 min per report).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from backend import config, db  # noqa: E402
from backend.analysis.benchmarks import Benchmarks  # noqa: E402
from backend.coach.base import CoachRefusal, CoachResult  # noqa: E402
from backend.coach.contracts import CoachingReport  # noqa: E402
from backend.coach.factory import BACKENDS, BackendUnavailable, default_backend, make_coach  # noqa: E402
from backend.coach.render import render  # noqa: E402
from backend.coach.store import save_result  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.ingest import DeadlockApiClient, NotFound, RateLimited  # noqa: E402
from backend.static_data import load  # noqa: E402
from backend.summarize.builder import build_summary, estimate_tokens  # noqa: E402
from backend.summarize.sources import match_data  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--backend", choices=BACKENDS, default=default_backend())
    ap.add_argument("--model")
    ap.add_argument("--fresh", action="store_true", help="ignore reports from earlier runs")
    ap.add_argument("--offline", action="store_true", help="only build the sheet from stored reports; call no model")
    args = ap.parse_args()

    try:
        coach = make_coach(args.backend, args.model)
    except BackendUnavailable as e:
        print(f"error: {e}")
        return 1
    engine, bench = get_engine(), Benchmarks.load()
    jobs = []  # (label, summary, account_id)
    with DeadlockApiClient() as client:
        static = load(client=client)
        badge = client.player_rank(args.account_id).badge or None
        own = {m.match_id for m in client.match_history(args.account_id) if m.game_mode == 1}
        with engine.connect() as conn:
            own |= {r[0] for r in conn.execute(select(db.matches.c.match_id)
                                               .where(db.matches.c.requester_account_id == args.account_id))}
        for mid in sorted(own, reverse=True):
            try:
                meta, replay = match_data(client, mid, args.account_id, engine)
            except (NotFound, RateLimited):
                continue
            jobs.append((f"your match {mid}", build_summary(meta, args.account_id, static, bench, replay, badge), args.account_id))
    fixtures = sorted((config.REPO_ROOT / "tests" / "data" / "api").glob("metadata_*.json"))
    for f in fixtures:
        meta = json.loads(f.read_text(encoding="utf-8"))
        for p in meta["players"]:
            if len(jobs) >= args.n:
                break
            jobs.append((f"fixture {meta['match_id']} (anonymous player)",
                         build_summary(meta, p["account_id"], static, bench), None))
    jobs = jobs[: args.n]

    done = previous_reports(engine, coach.backend, coach.model) if not args.fresh else {}
    out = config.data_dir() / "review" / "phase3_reports.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    totals = {"valid": 0, "first_try": 0, "cost": 0.0, "calls": 0, "latency": 0.0, "failed": 0, "reused": 0, "done": 0}

    def write_sheet() -> str:
        k = max(totals["done"], 1)
        head = (f"**Result so far ({totals['done']}/{len(jobs)} done):** {totals['valid']} valid "
                f"({totals['first_try']} on the first try), {totals['failed']} refused or failed, "
                f"{totals['reused']} reused from earlier runs, {totals['calls']} calls, total ${totals['cost']:.4f} "
                f"(${totals['cost'] / k:.5f} per report), {totals['latency'] / k:.1f}s per report.")
        out.write_text("\n".join([f"# Phase 3 check: {len(jobs)} reports, {coach.backend}:{coach.model}", "", head, "",
                                  "Spot-check: does every claim match the summary numbers? Is the advice useful?", "",
                                  *sections]), encoding="utf-8")
        return head

    for n, (label, summary, account) in enumerate(jobs, 1):
        key = (summary.match_id, summary.player.hero)
        if key in done:
            result, cost, latency, ncalls = done[key]
            totals["reused"] += 1
            print(f"[{n}/{len(jobs)}] {label}: reusing the valid report from an earlier run", flush=True)
        elif args.offline:
            sections += [f"## {n}. {label}: not done yet", ""]
            continue
        else:
            print(f"[{n}/{len(jobs)}] {label} ...", flush=True)
            try:
                result = coach.generate(summary)
            except CoachRefusal as e:
                save_result(engine, coach, summary, account, None, e.calls)
                totals["failed"] += 1
                totals["done"] += 1
                sections += [f"## {n}. {label}: REFUSED", ""]
                write_sheet()
                continue
            except RuntimeError as e:  # HTTP error from Gemini / local server after retries
                print(f"   failed: {e}")
                totals["failed"] += 1
                totals["done"] += 1
                sections += [f"## {n}. {label}: FAILED ({e})", ""]
                write_sheet()
                continue
            save_result(engine, coach, summary, account, result)
            cost, latency, ncalls = result.cost_usd or 0, result.latency_s, len(result.calls)
        totals["done"] += 1
        totals["valid"] += result.valid
        totals["first_try"] += result.valid and ncalls == 1
        totals["cost"] += cost
        totals["calls"] += ncalls
        totals["latency"] += latency
        sections += [f"## {n}. {label}", f"valid={result.valid} · calls={ncalls} · "
                     f"cost=${cost:.5f} · {latency:.1f}s · summary ~{estimate_tokens(summary)} tokens",
                     "", "```", render(summary, result), "```", "",
                     "<details><summary>MatchSummary</summary>", "", "```json",
                     summary.model_dump_json(indent=1, exclude_none=True), "```", "</details>", ""]
        write_sheet()  # after every report, so stopping the run loses nothing

    head = write_sheet()
    print(head.replace("**", ""))
    print(f"wrote {out}")
    return 0


def previous_reports(engine, backend: str, model: str) -> dict:
    """Valid reports from earlier runs with the same backend and model, keyed by (match_id, hero)."""
    out = {}
    with engine.connect() as conn:
        rows = conn.execute(select(db.coaching_reports).where(
            (db.coaching_reports.c.backend == backend) & (db.coaching_reports.c.model == model)
            & db.coaching_reports.c.valid).order_by(db.coaching_reports.c.id)).mappings().all()
        counts = dict(conn.execute(select(db.coach_calls.c.report_id, func.count())
                                   .group_by(db.coach_calls.c.report_id)).all())
    for r in rows:
        key = (r["match_id"], r["summary_json"]["player"]["hero"])
        result = CoachResult(CoachingReport.model_validate(r["report_json"]), True)
        out[key] = (result, r["cost_usd"] or 0, r["latency_s"] or 0, counts.get(r["id"], 1))
    return out


if __name__ == "__main__":
    sys.exit(main())
