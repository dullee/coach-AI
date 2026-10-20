"""python -m backend.parse <file.dem> [--account-id N]
python -m backend.parse --match-id N [--account-id N]   (fetch the replay first)

Parses a replay and stores it in the database. --account-id marks the player
the report is for; without it every player is stored anonymously.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from backend.db import get_engine
from backend.ingest import DeadlockApiClient, ReplayUnavailable, fetch_replay
from backend.parse.demo import ParseError, parse_demo
from backend.parse.store import save_match


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.parse")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("demo", nargs="?", help="path to a .dem file")
    src.add_argument("--match-id", type=int, help="download the replay for this match ID")
    parser.add_argument("--account-id", type=int, help="account ID of the player the report is for")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    start = time.perf_counter()
    try:
        if args.match_id is not None:
            with DeadlockApiClient() as client:
                path = fetch_replay(args.match_id, client=client)
        else:
            path = args.demo
        parsed = parse_demo(path, requester_account_id=args.account_id)
        save_match(parsed, get_engine())
    except (ReplayUnavailable, ParseError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    m = parsed.match
    print(
        f"stored match {m['match_id']} (build {m['build']}, {m['duration_s'] / 60:.1f} min): "
        f"{len(parsed.kills)} kills, {len(parsed.damage)} damage events, "
        f"{len(parsed.purchases)} purchases, {len(parsed.timeline):,} timeline rows "
        f"in {time.perf_counter() - start:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
