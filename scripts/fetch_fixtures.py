"""Download the test replays listed in tests/fixtures.json into data/demos/.

    python scripts/fetch_fixtures.py

Files already present with the right SHA-256 are skipped. Valve deletes
replays after roughly 2-4 weeks, so once the fixtures are downloaded, keep a
backup copy of data/demos/ (e.g. an external drive or personal cloud folder).
After the replays expire, a fresh clone can only restore them from that backup;
copy the .dem files into data/demos/ and rerun this script to verify them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.ingest import DeadlockApiClient, ReplayUnavailable, fetch_replay  # noqa: E402
from backend.ingest.replay import sha256_file  # noqa: E402

FIXTURES = config.REPO_ROOT / "tests" / "fixtures.json"


def main() -> int:
    entries = json.loads(FIXTURES.read_text(encoding="utf-8"))["replays"]
    dest = config.demos_dir()
    failed = 0
    with DeadlockApiClient() as client:
        for e in entries:
            path = dest / e["file"]
            if path.exists() and sha256_file(path) == e["sha256"]:
                print(f"ok        {e['file']}")
                continue
            try:
                got = fetch_replay(e["match_id"], client=client, dest_dir=dest)
            except ReplayUnavailable as err:
                print(f"MISSING   {e['file']}: {err}. Restore it from your backup copy.")
                failed += 1
                continue
            if got.name != e["file"] or sha256_file(got) != e["sha256"]:
                print(f"MISMATCH  {got.name}: downloaded file does not match tests/fixtures.json")
                failed += 1
            else:
                print(f"fetched   {e['file']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
