"""Re-parse every stored match whose replay file is still on disk.

    python scripts/reparse_all.py

Needed after the parser starts storing something new (e.g. teamfights).
Each match keeps the account it was parsed for (matches.requester_account_id).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend import config, db  # noqa: E402
from backend.app.replays import local_replays  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.parse import parse_demo, save_match  # noqa: E402


def main() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(db.matches.c.match_id, db.matches.c.source_file,
                                   db.matches.c.requester_account_id)).all()
    files = local_replays()
    for f in config.demos_dir().glob("*.dem"):   # test fixtures
        files.setdefault(int(f.stem.split("_")[0]), f)
    done = 0
    for match_id, source, account in rows:
        path = files.get(match_id)
        if path is None:
            print(f"skip {match_id}: replay file not found ({source})")
            continue
        start = time.perf_counter()
        save_match(parse_demo(path, requester_account_id=account), engine)
        done += 1
        print(f"re-parsed {match_id} ({'account ' + str(account) if account else 'anonymous'}) in {time.perf_counter() - start:.0f}s")
    print(f"{done}/{len(rows)} matches re-parsed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
