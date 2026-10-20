"""python -m backend.static_data refresh   -> download newest hero/item/rank assets
python -m backend.static_data show      -> print what is stored

Run "refresh" on a schedule (e.g. daily from the background worker or the OS
task scheduler). load() also refreshes automatically when given an API client
and the stored copy is older than 24 hours.
"""

from __future__ import annotations

import argparse
import logging

from backend.ingest.client import DeadlockApiClient
from backend.static_data.loader import load, refresh, stored_versions


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.static_data")
    parser.add_argument("command", choices=["refresh", "show"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "refresh":
        with DeadlockApiClient() as client:
            path = refresh(client)
        print(f"stored static data in {path}")
    versions = stored_versions()
    if not versions:
        print("no static data stored yet")
        return
    sd = load()
    print(
        f"client version {sd.client_version}: {len(sd.heroes)} heroes "
        f"({len(sd.playable_heroes())} playable), {len(sd.items)} items/abilities, "
        f"{len(sd.ranks)} rank tiers; stored versions {versions}"
    )


if __name__ == "__main__":
    main()
