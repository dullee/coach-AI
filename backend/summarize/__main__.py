"""python -m backend.summarize --match-id N --account-id A [--demo FILE | --replay]

Prints the MatchSummary JSON for one player in one match.
  (no flag)   Free tier: API data only
  --demo FILE Medium tier: parse and store this replay first, then use it
  --replay    Medium tier: use a replay already stored with python -m backend.parse
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.replay_data import ReplayData
from backend.db import get_engine
from backend.ingest import DeadlockApiClient, NotFound
from backend.parse import parse_demo, save_match
from backend.static_data import load
from backend.summarize.builder import TOKEN_TARGET, build_summary, estimate_tokens, summary_json


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.summarize")
    ap.add_argument("--match-id", type=int, required=True)
    ap.add_argument("--account-id", type=int, required=True)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--demo", help="replay file for this match (Medium tier)")
    src.add_argument("--replay", action="store_true", help="use the stored replay (Medium tier)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with DeadlockApiClient() as client:
        static = load(client=client)
        try:
            meta = client.own_match_metadata(args.match_id)
        except NotFound:
            print(f"error: the community API has no data for match {args.match_id} yet; try again later", file=sys.stderr)
            return 1
        player = next((p for p in meta["players"] if p["account_id"] == args.account_id), None)
        if player is None:
            print(f"error: account {args.account_id} did not play in match {args.match_id}", file=sys.stderr)
            return 1
        replay = None
        if args.demo or args.replay:
            engine = get_engine()
            if args.demo:
                save_match(parse_demo(args.demo, requester_account_id=args.account_id), engine)
            replay = ReplayData.load(args.match_id, player["hero_id"], engine)
        fallback = None
        has_rank = (player.get("player_rank_data") or {}).get("initial_display_rank") or (replay and replay.rank_badge)
        if not has_rank:
            # Unranked match (its replay has rank 0 for everyone too): use the player's current rank.
            fallback = client.player_rank(args.account_id).badge or None
        summary = build_summary(meta, args.account_id, static, Benchmarks.load(), replay, fallback)

    print(summary_json(summary))
    tokens = estimate_tokens(summary)
    print(f"\n~{tokens} tokens (target < {TOKEN_TARGET}), {len(summary.issues)} issues", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
