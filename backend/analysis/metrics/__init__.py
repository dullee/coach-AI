"""All API-level metrics (one file per family). Replay-only metrics live in metrics/replay.py."""

from __future__ import annotations

import polars as pl

from backend.analysis.metrics import combat, farming, items, objectives, survival
from backend.analysis.metrics.base import Metric
from backend.static_data import StaticData

METRICS: dict[str, Metric] = {m.id: m for mod in (farming, combat, survival, objectives, items) for m in mod.METRICS}


def compute_all(df: pl.DataFrame, static: StaticData) -> pl.DataFrame:
    """Frame with one column per metric (same row order as df)."""
    return pl.DataFrame([m.compute(df, static) for m in METRICS.values()])


__all__ = ["METRICS", "Metric", "compute_all"]
