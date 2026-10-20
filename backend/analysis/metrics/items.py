"""Item timings. Which items count as "big" comes from static data (shop tier)."""

from __future__ import annotations

import polars as pl

from backend.analysis.metrics.base import Metric
from backend.static_data import StaticData

BIG_ITEM_MIN_TIER = 3  # tiers 1-5 in the shop; tier 3+ items are the expensive build-defining ones


def big_item_ids(static: StaticData) -> list[int]:
    return [i.id for i in static.items.values() if i.type == "upgrade" and (i.tier or 0) >= BIG_ITEM_MIN_TIER]


def first_purchases(df: pl.DataFrame, item_ids: list[int]) -> pl.DataFrame:
    """Long table: one row per (frame row, item) with the first purchase time.

    Columns: _r (frame row index), item_id, t.
    """
    return (
        df.select(pl.int_range(pl.len(), dtype=pl.UInt32).alias("_r"), pl.col("it_id").alias("item_id"), pl.col("it_t").alias("t"))
        .explode("item_id", "t")
        .drop_nulls()
        .filter(pl.col("item_id").is_in(item_ids))
        .group_by("_r", "item_id")
        .agg(pl.col("t").min())
    )


def nth_big_item_time(df: pl.DataFrame, static: StaticData, n: int) -> pl.Series:
    """When the player had bought n different tier-3+ items (null if never).

    Independent of build order, unlike comparing items one by one: per-item
    comparisons flagged ~75% of real players as "late" on some item."""
    nth = (
        first_purchases(df, big_item_ids(static))
        .group_by("_r").agg(pl.col("t").sort().get(n - 1, null_on_oob=True).alias("t"))
    )
    return (
        pl.DataFrame({"_r": pl.Series(range(df.height), dtype=pl.UInt32)})
        .join(nth, on="_r", how="left").sort("_r")["t"].cast(pl.Float64)
    )


METRICS = [
    Metric("first_big_item_s", "Time of first tier-3+ item", "seconds", "full match", "items", False,
           lambda df, static: nth_big_item_time(df, static, 1)),
    Metric("third_big_item_s", "Time of third tier-3+ item", "seconds", "full match", "items", False,
           lambda df, static: nth_big_item_time(df, static, 3)),
]
