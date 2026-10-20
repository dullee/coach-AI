"""Save a ParsedMatch: event tables to the database, timeline to Parquet."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from backend import config, db
from backend.parse.demo import ParsedMatch


def timeline_relpath(match_id: int) -> str:
    return f"parsed/{match_id}/timeline.parquet"


def save_match(parsed: ParsedMatch, engine: Engine, data_dir: Path | None = None) -> None:
    """Store a parsed match. Re-saving the same match replaces it."""
    data_dir = Path(data_dir or config.data_dir())
    match_id = parsed.match["match_id"]
    rel = timeline_relpath(match_id)
    out = data_dir / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    parsed.timeline.write_parquet(tmp, compression="zstd")
    tmp.replace(out)

    with engine.begin() as conn:
        for table in db.EVENT_TABLES.values():
            conn.execute(delete(table).where(table.c.match_id == match_id))
        conn.execute(delete(db.matches).where(db.matches.c.match_id == match_id))
        conn.execute(db.matches.insert(), [{**parsed.match, "timeline_path": rel}])
        for name, table in db.EVENT_TABLES.items():
            rows = getattr(parsed, name).to_dicts()
            if rows:
                conn.execute(table.insert(), rows)


def load_timeline(match_id: int, engine: Engine, data_dir: Path | None = None) -> pl.DataFrame:
    data_dir = Path(data_dir or config.data_dir())
    with engine.connect() as conn:
        rel = conn.execute(select(db.matches.c.timeline_path).where(db.matches.c.match_id == match_id)).scalar_one()
    return pl.read_parquet(data_dir / rel)
