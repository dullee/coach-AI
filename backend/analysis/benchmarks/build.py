"""Build rank-bracket benchmarks from the community data lake.

Source: data.deadlock-api.com, table match_player (hourly Parquet, ~3 GB per
file; never downloaded whole). For each of the newest files since the last
major patch, DuckDB reads only the needed columns and rows (ranked, normal
mode, at least 10 minutes, no abandons) and writes a small local extract to
data/lake_cache/. Re-running only fetches files not extracted yet.

Each file takes a few minutes, so this is an offline batch job (once per
patch, or weekly), not part of a request.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
import polars as pl

from backend import config
from backend.analysis.brackets import ALL, tier_to_bracket_map
from backend.analysis.frame import LAKE_COLUMNS, from_lake
from backend.analysis.metrics import compute_all
from backend.analysis.metrics.items import big_item_ids, first_purchases
from backend.static_data import StaticData

log = logging.getLogger(__name__)

LAKE_URL = "https://data.deadlock-api.com"
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
MIN_MATCHES = 200       # per (hero, bracket, metric) before falling back
MIN_ITEM_BUYS = 50      # per (hero, bracket, item)
MIN_DURATION_S = 600


def lake_manifest(http: httpx.Client | None = None) -> dict:
    own = http is None
    http = http or httpx.Client(timeout=60)
    try:
        r = http.get(f"{LAKE_URL}/v1/manifest.json")
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            http.close()


def pick_files(manifest: dict, since_epoch: float, max_files: int) -> list[dict]:
    """The max_files newest "base" files (one per match-ID range, ~3 GB, ~1.4 days of
    matches each) plus every small "delta"/"residual" update file since the patch.
    Overlapping rows are removed later by load_rows()."""
    files = [f for f in manifest["tables"]["match_player"]["files"] if f.get("hi", 0) >= since_epoch]
    base = sorted((f for f in files if f.get("kind") == "base"), key=lambda f: f.get("partition", -1), reverse=True)
    updates = [f for f in files if f.get("kind") != "base"]
    return base[:max_files] + updates


def cache_name(key: str) -> str:
    """Unique local file name: base files in different partitions share a basename."""
    return key.removeprefix("v1/tables/").replace("/", "__")


def extract_file(con: duckdb.DuckDBPyConnection, url: str, out: Path, since_epoch: float) -> None:
    cols = ", ".join(f'"{c}"' for c in LAKE_COLUMNS)
    tmp = out.with_suffix(".tmp.parquet")
    con.execute(
        f"""COPY (
              SELECT {cols} FROM read_parquet('{url}')
              WHERE game_mode = 'Normal' AND match_mode = 'Ranked'
                AND start_time >= to_timestamp({since_epoch})
                AND duration_s >= {MIN_DURATION_S}
                AND abandon_match_time_s = 0
                AND coalesce(not_scored, false) = false
            ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
    )
    tmp.replace(out)


def fetch_extracts(since_epoch: float, max_files: int, cache_dir: Path | None = None) -> list[Path]:
    cache_dir = Path(cache_dir or config.data_dir() / "lake_cache" / "match_player")
    cache_dir.mkdir(parents=True, exist_ok=True)
    files = pick_files(lake_manifest(), since_epoch, max_files)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET enable_progress_bar = false;")
    paths = []
    for i, f in enumerate(files, 1):
        out = cache_dir / cache_name(f["key"])
        if not out.exists():
            log.info("extracting %d/%d %s (%d MB remote)", i, len(files), f["key"], f["bytes"] // 1_000_000)
            t = time.perf_counter()
            extract_file(con, f"{LAKE_URL}/{f['key']}", out, since_epoch)
            log.info("  done in %.0fs", time.perf_counter() - t)
        paths.append(out)
    return paths


def load_rows(paths: list[Path]) -> pl.DataFrame:
    df = pl.concat([pl.read_parquet(p) for p in paths], how="vertical_relaxed")
    # base/delta/residual files can overlap; keep one row per player per match.
    return df.unique(subset=["match_id", "account_id"], keep="last")


def add_bracket(frame: pl.DataFrame, static: StaticData) -> pl.DataFrame:
    """Bracket from the player's own rank, else the lobby average (both are badges)."""
    tier = pl.coalesce("rank_badge", "lobby_badge") // 10
    return frame.with_columns(tier.replace_strict(tier_to_bracket_map(static), default=None).alias("bracket"))


def _quantile_aggs(col: str) -> list[pl.Expr]:
    return [pl.col(col).count().alias("n")] + [
        pl.col(col).quantile(q, interpolation="linear").alias(f"p{int(q * 100):02d}") for q in QUANTILES
    ]


def compute_metric_benchmarks(frame: pl.DataFrame, static: StaticData) -> pl.DataFrame:
    values = pl.concat([frame.select("hero_id", "bracket"), compute_all(frame, static)], how="horizontal")
    long = values.unpivot(index=["hero_id", "bracket"], variable_name="metric", value_name="value").drop_nulls("value")
    by_hero_bracket = long.drop_nulls("bracket").group_by("metric", "hero_id", "bracket").agg(_quantile_aggs("value"))
    by_hero = long.group_by("metric", "hero_id").agg(_quantile_aggs("value")).with_columns(pl.lit(ALL).alias("bracket"))
    by_bracket = (long.drop_nulls("bracket").group_by("metric", "bracket").agg(_quantile_aggs("value"))
                  .with_columns(pl.lit(0, dtype=pl.Int32).alias("hero_id")))
    cols = ["metric", "hero_id", "bracket", "n", *[f"p{int(q * 100):02d}" for q in QUANTILES]]
    return pl.concat([d.select(cols) for d in (by_hero_bracket, by_hero, by_bracket)], how="vertical_relaxed")


def compute_item_benchmarks(frame: pl.DataFrame, static: StaticData) -> pl.DataFrame:
    """First-purchase time of each tier-3+ item, per hero and bracket (and per hero overall)."""
    keys = frame.select(pl.int_range(pl.len(), dtype=pl.UInt32).alias("_r"), "hero_id", "bracket")
    buys = first_purchases(frame, big_item_ids(static)).join(keys, on="_r")
    games = keys.group_by("hero_id", "bracket").len("games")

    def agg(df: pl.DataFrame, by: list[str]) -> pl.DataFrame:
        return df.group_by(*by, "item_id").agg(
            pl.len().alias("n"),
            pl.col("t").quantile(0.25).alias("p25"),
            pl.col("t").median().alias("p50"),
            pl.col("t").quantile(0.75).alias("p75"),
        )

    per_bracket = agg(buys.drop_nulls("bracket"), ["hero_id", "bracket"]).join(games, on=["hero_id", "bracket"])
    per_hero = (agg(buys, ["hero_id"]).with_columns(pl.lit(ALL).alias("bracket"))
                .join(keys.group_by("hero_id").len("games"), on="hero_id"))
    out = pl.concat([per_bracket, per_hero.select(per_bracket.columns)], how="vertical_relaxed")
    return out.with_columns((pl.col("n") / pl.col("games")).alias("buy_rate"))


# Minute marks for the net-worth curve. Stats snapshots come every 3 min until 15:00, then every
# 5 min, so these marks sit on or close to real snapshots (value_at interpolates between them).
CURVE_MINUTES = (3, 6, 9, 12, 15, 18, 20, 25, 30, 35, 40)


def compute_curve_benchmarks(frame: pl.DataFrame) -> pl.DataFrame:
    """Net worth (souls) at each CURVE_MINUTES mark: p25/p50/p75 per (hero, bracket), hero and bracket."""
    from backend.analysis.metrics.base import value_at

    parts = []
    for m in CURVE_MINUTES:
        v = value_at(frame, "st_t", "st_nw", m * 60)
        parts.append(frame.select("hero_id", "bracket").with_columns(
            pl.lit(m, dtype=pl.Int16).alias("minute"),
            pl.when(frame["duration_s"] >= m * 60).then(v).otherwise(None).alias("value")))
    long = pl.concat(parts).drop_nulls("value")

    def agg(df, by):
        return df.group_by(*by, "minute").agg(
            pl.len().alias("n"), pl.col("value").quantile(0.25).alias("p25"),
            pl.col("value").median().alias("p50"), pl.col("value").quantile(0.75).alias("p75"))

    hb = agg(long.drop_nulls("bracket"), ["hero_id", "bracket"])
    h = agg(long, ["hero_id"]).with_columns(pl.lit(ALL).alias("bracket"))
    b = agg(long.drop_nulls("bracket"), ["bracket"]).with_columns(pl.lit(0, dtype=pl.Int32).alias("hero_id"))
    cols = ["hero_id", "bracket", "minute", "n", "p25", "p50", "p75"]
    return pl.concat([d.select(cols) for d in (hb, h, b)], how="vertical_relaxed").sort(cols[:3])


def build(static: StaticData, patch_start_iso: str, max_files: int = 5, out_root: Path | None = None) -> Path:
    since = datetime.fromisoformat(patch_start_iso.replace("Z", "+00:00")).timestamp()
    paths = fetch_extracts(since, max_files)
    frame = add_bracket(from_lake(load_rows(paths)), static)
    log.info("%d ranked player-rows from %d matches", frame.height, frame["match_id"].n_unique())

    label = patch_start_iso[:10]
    out = Path(out_root or config.data_dir() / "benchmarks") / label
    out.mkdir(parents=True, exist_ok=True)
    compute_metric_benchmarks(frame, static).write_parquet(out / "metrics.parquet")
    compute_item_benchmarks(frame, static).write_parquet(out / "items.parquet")
    compute_curve_benchmarks(frame).write_parquet(out / "curves.parquet")
    meta = {
        "patch_start": patch_start_iso, "client_version": static.client_version,
        "files": [p.name for p in paths], "player_rows": frame.height,
        "matches": frame["match_id"].n_unique(), "built_at": time.time(),
        "rows_with_own_rank": int(frame["rank_badge"].is_not_null().sum()),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return out
