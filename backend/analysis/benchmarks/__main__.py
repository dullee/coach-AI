"""python -m backend.analysis.benchmarks build [--files N]
python -m backend.analysis.benchmarks show

build: pull ranked matches since the last major patch from the data lake
(N newest files, ~1.4 days of matches each, a few minutes per file) and
write percentile tables to data/benchmarks/<patch date>/.
"""

from __future__ import annotations

import argparse
import logging

from backend.analysis.benchmarks.build import build
from backend.analysis.benchmarks.store import Benchmarks
from backend.ingest import DeadlockApiClient
from backend.static_data import load


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.analysis.benchmarks")
    parser.add_argument("command", choices=["build", "show"])
    parser.add_argument("--files", type=int, default=5, help="number of newest data-lake files to use")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "build":
        with DeadlockApiClient() as client:
            static = load(client=client)
            patch_start = client.big_patch_days()[0]
        out = build(static, patch_start, max_files=args.files)
        print(f"benchmarks written to {out}")
    b = Benchmarks.load()
    print({k: v for k, v in b.meta.items() if k != "files"}, f"files={len(b.meta['files'])}")


if __name__ == "__main__":
    main()
