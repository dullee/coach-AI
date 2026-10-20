"""Phase 3 "Done when" run: coaching reports for 20 real player-matches.

    python scripts/phase3_check.py --account-id <your id> [--n 20] [--model claude-haiku-5-5]

Matches: your own matches first (API history + parsed replays), then players
from the anonymized fixture matches in tests/data/api/ to reach N. Every
report is validated (schema, issue ids, invented numbers), all calls are
logged with tokens and cost, and a sheet for spot-checking is written to
data/review/phase3_reports.md. This calls the Claude API and costs money
(with Claude Haiku 5.5 roughly $0.001-0.003 per report).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend import config, db  # noqa: E402
from backend.analysis.benchmarks import Benchmarks  # noqa: E402
from backend.coach.base import CoachRefusal  # noqa: E402
from backend.coach.claude import NO_CREDENTIALS, ClaudeCoach, has_credentials  # noqa: E402
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
    ap.add_argument("--model")
    args = ap.parse_args()

    if not has_credentials():
        print(f"error: {NO_CREDENTIALS}")
        return 1
    engine, bench = get_engine(), Benchmarks.load()
    coach = ClaudeCoach(model=args.model)
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

    lines = [f"# Phase 3 check: {len(jobs)} reports, model {coach.model}", "",
             "Spot-check: does every claim match the summary numbers? Is the advice useful?", ""]
    totals = {"valid": 0, "first_try": 0, "cost": 0.0, "calls": 0, "latency": 0.0, "refused": 0}
    for n, (label, summary, account) in enumerate(jobs, 1):
        print(f"[{n}/{len(jobs)}] {label} ...", flush=True)
        try:
            result = coach.generate(summary)
        except CoachRefusal as e:
            save_result(engine, coach, summary, account, None, e.calls)
            totals["refused"] += 1
            lines += [f"## {n}. {label}: REFUSED", ""]
            continue
        save_result(engine, coach, summary, account, result)
        totals["valid"] += result.valid
        totals["first_try"] += result.valid and len(result.calls) == 1
        totals["cost"] += result.cost_usd or 0
        totals["calls"] += len(result.calls)
        totals["latency"] += result.latency_s
        lines += [f"## {n}. {label}", f"valid={result.valid} · calls={len(result.calls)} · "
                  f"cost=${(result.cost_usd or 0):.5f} · {result.latency_s:.1f}s · summary ~{estimate_tokens(summary)} tokens",
                  "", "```", render(summary, result), "```", "",
                  "<details><summary>MatchSummary</summary>", "", "```json",
                  summary.model_dump_json(indent=1, exclude_none=True), "```", "</details>", ""]

    k = len(jobs)
    head = (f"**Result:** {totals['valid']}/{k} valid ({totals['first_try']} on the first try), "
            f"{totals['refused']} refused, {totals['calls']} calls, total ${totals['cost']:.4f} "
            f"(${totals['cost'] / max(k, 1):.5f} per report), {totals['latency'] / max(k, 1):.1f}s per report.")
    lines.insert(2, head)
    out = config.data_dir() / "review" / "phase3_reports.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(head.replace("**", ""))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
