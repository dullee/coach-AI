"""Metric definitions and shared helpers.

A metric takes the player-match frame (analysis/frame.py) and returns one
float per row (null when it doesn't apply, e.g. a 10-20 min metric in a
15-minute game). Because they are vectorized, the same function computes a
user's value and the benchmark distribution over millions of rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import polars as pl

from backend.static_data import StaticData

Category = Literal["farming", "survival", "combat", "teamplay", "objectives", "mechanics", "items"]


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str
    window: str
    category: Category
    higher_is_better: bool
    fn: Callable[[pl.DataFrame, StaticData], pl.Series]

    def compute(self, df: pl.DataFrame, static: StaticData) -> pl.Series:
        return self.fn(df, static).cast(pl.Float64).alias(self.id)


def minutes(df: pl.DataFrame) -> pl.Series:
    return df["duration_s"].cast(pl.Float64) / 60.0


def value_at(df: pl.DataFrame, time_col: str, value_col: str, at_s: float) -> pl.Series:
    """Linearly interpolate a cumulative stat (list column) at at_s seconds, per row.

    Stats snapshots come every 180 s plus one at the end of the match.
    Null when the match has no snapshot at or after at_s.
    """
    exploded = (
        df.select(
            pl.int_range(pl.len(), dtype=pl.UInt32).alias("_r"),
            pl.col(time_col).alias("_t"),
            pl.col(value_col).alias("_v"),
        )
        .explode("_t", "_v")
        .drop_nulls()
    )
    before = exploded.filter(pl.col("_t") <= at_s).group_by("_r").agg(
        pl.col("_t").max().alias("t0"), pl.col("_v").sort_by("_t").last().alias("v0"))
    after = exploded.filter(pl.col("_t") >= at_s).group_by("_r").agg(
        pl.col("_t").min().alias("t1"), pl.col("_v").sort_by("_t").first().alias("v1"))
    j = (
        pl.DataFrame({"_r": pl.Series(range(df.height), dtype=pl.UInt32)})
        .join(before, on="_r", how="left")
        .join(after, on="_r", how="left")
        .sort("_r")
    )
    t0, t1, v0, v1 = (pl.col(c).cast(pl.Float64) for c in ("t0", "t1", "v0", "v1"))
    return j.select(
        pl.when(t0.is_null() | t1.is_null()).then(None)
        .when(t1 == t0).then(v0)
        .otherwise(v0 + (v1 - v0) * (at_s - t0) / (t1 - t0))
    ).to_series()


def last(df: pl.DataFrame, col: str) -> pl.Series:
    return df[col].list.last().cast(pl.Float64)


def only_if(values: pl.Series, keep: pl.Series) -> pl.Series:
    return pl.select(pl.when(keep).then(values).otherwise(None)).to_series()
