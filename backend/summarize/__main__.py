"""python -m backend.summarize --match-id N --account-id A [--demo FILE]

Prints the MatchSummary JSON for one player in one match.
  --demo FILE  parse and store this replay first (Medium tier)
Without --demo, a replay already stored for this player and match is used if
there is one (Medium tier); otherwise API data only (Free tier).
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.analysis.benchmarks import Benchmarks
from backend.db import get_engine
from backend.ingest import DeadlockApiClient, NotFound, RateLimited
from backend.parse import parse_demo, save_match
from backend.static_data import load
from backend.summarize.builder import TOKEN_TARGET, build_summary, estimate_tokens, summary_json
from backend.summarize.sources import match_data


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.summarize")
    ap.add_argument("--match-id", type=int, required=True)
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--demo", help="replay file for this match (Medium tier)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    engine = get_engine()
    if args.demo:
        parsed = parse_demo(args.demo, requester_account_id=args.account_id)
        if parsed.match["match_id"] != args.match_id:
            print(f"error: {args.demo} is match {parsed.match['match_id']}, not {args.match_id}", file=sys.stderr)
            return 1
        save_match(parsed, engine)

    with DeadlockApiClient() as client:
        static = load(client=client)
        try:
            meta, replay = match_data(client, args.match_id, args.account_id, engine)
        except NotFound:
            print(f"error: no data for match {args.match_id} yet; download its replay and pass --demo", file=sys.stderr)
            return 1
        except RateLimited as e:
            print(f"error: the community API has no data for match {args.match_id} and its Steam lookup quota is used up "
                  f"(try again in {e.retry_after_s / 60:.0f} min), or pass --demo with the replay", file=sys.stderr)
            return 1
        player = next((p for p in meta["players"] if p["account_id"] == args.account_id), None)
        if player is None:
            print(f"error: account {args.account_id} did not play in match {args.match_id}", file=sys.stderr)
            return 1
        fallback = None
        has_rank = (player.get("player_rank_data") or {}).get("initial_display_rank") or (replay and replay.rank_badge)
        if not has_rank:
            fallback = client.player_rank(args.account_id).badge or None  # current rank, if any
        summary = build_summary(meta, args.account_id, static, Benchmarks.load(), replay, fallback)

    print(summary_json(summary))
    src = "replay" if meta.get("source") == "replay" else "community API"
    print(f"\n~{estimate_tokens(summary)} tokens (target < {TOKEN_TARGET}), {len(summary.issues)} issues, stats from {src}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
