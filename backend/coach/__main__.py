"""python -m backend.coach <match_id> --account-id A [--demo FILE] [--model M | --local] [--json]

Builds the MatchSummary (Free tier, or Medium if a replay is stored or given),
asks the coaching model for a CoachingReport, validates it (retry once),
prints it, and logs tokens and cost for every call to the database.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import anthropic

from backend.analysis.benchmarks import Benchmarks
from backend.coach.base import CoachRefusal
from backend.coach.claude import NO_CREDENTIALS, ClaudeCoach, has_credentials
from backend.coach.local import LocalCoach
from backend.coach.render import render
from backend.coach.store import save_result
from backend.db import get_engine
from backend.ingest import DeadlockApiClient, NotFound, RateLimited
from backend.parse import parse_demo, save_match
from backend.static_data import load
from backend.summarize.builder import build_summary
from backend.summarize.sources import match_data


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.coach")
    ap.add_argument("match_id", type=int)
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--demo", help="replay file for this match (Medium tier)")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--model", help="Claude model (default: COACH_CLAUDE_MODEL or claude-haiku-5-5)")
    which.add_argument("--local", action="store_true", help="use the local OpenAI-compatible server (COACH_LOCAL_URL)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    if not args.local and not has_credentials():
        print(f"error: {NO_CREDENTIALS}", file=sys.stderr)
        return 1
    engine = get_engine()
    if args.demo:
        save_match(parse_demo(args.demo, requester_account_id=args.account_id), engine)

    with DeadlockApiClient() as client:
        static = load(client=client)
        try:
            meta, replay = match_data(client, args.match_id, args.account_id, engine)
        except (NotFound, RateLimited) as e:
            print(f"error: no data for match {args.match_id} ({type(e).__name__}); pass --demo with its replay", file=sys.stderr)
            return 1
        player = next((p for p in meta["players"] if p["account_id"] == args.account_id), None)
        if player is None:
            print(f"error: account {args.account_id} did not play in match {args.match_id}", file=sys.stderr)
            return 1
        has_rank = (player.get("player_rank_data") or {}).get("initial_display_rank") or (replay and replay.rank_badge)
        fallback = None if has_rank else (client.player_rank(args.account_id).badge or None)
        summary = build_summary(meta, args.account_id, static, Benchmarks.load(), replay, fallback)

    coach = LocalCoach() if args.local else ClaudeCoach(model=args.model)
    try:
        result = coach.generate(summary)
    except CoachRefusal as e:
        save_result(engine, coach, summary, args.account_id, None, e.calls)
        print("error: the model declined this request; try --model claude-opus-5-5 (has server-side fallback)", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("error: no valid Anthropic API key; set ANTHROPIC_API_KEY", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError as e:
        print(f"error: could not reach the model API ({e})", file=sys.stderr)
        return 1
    save_result(engine, coach, summary, args.account_id, result)

    if args.json:
        print(json.dumps(result.report.model_dump(mode="json") if result.report else None, indent=1, ensure_ascii=False))
    else:
        print(render(summary, result))
    cost = f"${result.cost_usd:.5f}" if result.cost_usd is not None else "unknown"
    tokens = " + ".join(f"{c.input_tokens}+{c.cache_read_tokens or 0}c in / {c.output_tokens} out" for c in result.calls)
    print(f"\n[{coach.backend}:{coach.model}] {len(result.calls)} call(s), {tokens}, cost {cost}, "
          f"{result.latency_s:.1f}s, valid={result.valid}", file=sys.stderr)
    return 0 if result.valid else 2


if __name__ == "__main__":
    sys.exit(main())
