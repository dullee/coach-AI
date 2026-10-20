"""Deaths by phase."""

from __future__ import annotations

import polars as pl

from backend.analysis.metrics.base import Metric, only_if
from backend.analysis.metrics.farming import LANE_END_S


def _deaths_0_10(df, static):
    n = df.select(pl.col("death_t").list.eval((pl.element() < LANE_END_S).sum()).list.first()).to_series()
    return only_if(n.cast(pl.Float64), df["duration_s"] >= LANE_END_S)


def _deaths_per_10(df, static):
    return df["deaths"].cast(pl.Float64) / (df["duration_s"].cast(pl.Float64) / 600.0)


METRICS = [
    Metric("deaths_0_10", "Deaths in lane", "deaths", "0-10 min", "survival", False, _deaths_0_10),
    Metric("deaths_per_10_min", "Deaths per 10 minutes", "deaths/10 min", "full match", "survival", False, _deaths_per_10),
]
