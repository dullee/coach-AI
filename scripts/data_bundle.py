"""Pack / unpack the local data folder for moving the project to another PC.

    python scripts/data_bundle.py pack   [--out coach-data.zip] [--with-lake]
    python scripts/data_bundle.py unpack coach-data.zip

data/ is not in git. Some of it can't be recreated (Valve deletes replays after ~2-4 weeks),
the rest just takes time to rebuild. The bundle holds:

| folder / file      | why                                                         |
|--------------------|-------------------------------------------------------------|
| demos/             | the 3 test replays (no longer downloadable)                 |
| uploads/           | replays uploaded through the app                            |
| coach.db, parsed/  | parsed matches, reports, ratings, cached analyses           |
| benchmarks/        | rank benchmarks (rebuilding takes ~15 min of downloads)     |
| static/            | hero/item data (re-downloadable, but small)                 |
| ml/                | training inputs, teacher reports, datasets, eval results    |
| review/            | review sheets                                               |
| lake_cache/        | only with --with-lake (~500 MB): needed to rebuild the same |
|                    | benchmarks or training inputs offline                       |

Left out (rebuilt automatically): cache/ (API responses), boon/ (Boon's name catalog).
Replays downloaded in Deadlock live in the Steam folder, not here; copy that folder too if you
want to re-parse them on the other PC.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

INCLUDE = ["demos", "uploads", "coach.db", "parsed", "benchmarks", "static", "ml", "review"]
OPTIONAL = {"lake": "lake_cache"}


def pack(out: Path, with_lake: bool, data_dir: Path | None = None) -> Path:
    data_dir = Path(data_dir or config.data_dir())
    names = INCLUDE + ([OPTIONAL["lake"]] if with_lake else [])
    files = []
    for name in names:
        p = data_dir / name
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file() and not f.name.endswith((".part", ".download", ".tmp"))]
    total = sum(f.stat().st_size for f in files)
    print(f"packing {len(files)} files ({total / 1e9:.2f} GB) into {out} ...")
    # .dem and parquet files are already dense; storing them avoids minutes of slow compression.
    with zipfile.ZipFile(out, "w", allowZip64=True) as z:
        for f in files:
            dense = f.suffix in (".dem", ".parquet", ".gguf")
            z.write(f, f.relative_to(data_dir).as_posix(), compress_type=zipfile.ZIP_STORED if dense else zipfile.ZIP_DEFLATED)
    print(f"done: {out} ({out.stat().st_size / 1e9:.2f} GB)")
    return out


def unpack(src: Path, data_dir: Path | None = None) -> None:
    data_dir = Path(data_dir or config.data_dir())
    data_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():   # refuse paths that would escape data/
            target = (data_dir / name).resolve()
            if data_dir.resolve() not in target.parents:
                raise ValueError(f"unsafe path in bundle: {name}")
        z.extractall(data_dir)
    print(f"unpacked {src} into {data_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="python scripts/data_bundle.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--out", type=Path, default=Path("coach-data.zip"))
    p.add_argument("--with-lake", action="store_true")
    u = sub.add_parser("unpack")
    u.add_argument("zip", type=Path)
    args = ap.parse_args()
    if args.cmd == "pack":
        pack(args.out, args.with_lake)
    else:
        unpack(args.zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
