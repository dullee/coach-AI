"""Load built benchmarks and place a player's value in the distribution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from backend import config
from backend.analysis.benchmarks.build import MIN_ITEM_BUYS, MIN_MATCHES, QUANTILES
from backend.analysis.brackets import ALL

QCOLS = [f"p{int(q * 100):02d}" for q in QUANTILES]


@dataclass(frozen=True)
class Distribution:
    metric: str
    hero_id: int          # 0 = all heroes
    bracket: str          # bracket id or "all"
    n: int
    quantiles: dict[float, float]

    @property
    def p50(self) -> float:
        return self.quantiles[0.5]

    def percentile(self, value: float) -> float:
        """Approximate share of players with a lower value (0..1), interpolated between quantiles."""
        qs = sorted(self.quantiles.items(), key=lambda kv: kv[1])
        if value <= qs[0][1]:
            return qs[0][0] / 2
        if value >= qs[-1][1]:
            return 1 - (1 - qs[-1][0]) / 2
        for (q0, v0), (q1, v1) in zip(qs, qs[1:]):
            if v0 <= value <= v1:
                return q0 if v1 == v0 else q0 + (q1 - q0) * (value - v0) / (v1 - v0)
        return 0.5


@dataclass(frozen=True)
class ItemTiming:
    item_id: int
    bracket: str
    n: int
    buy_rate: float
    p25: float
    p50: float
    p75: float


class Benchmarks:
    def __init__(self, metrics: pl.DataFrame, items: pl.DataFrame, meta: dict, curves: pl.DataFrame | None = None):
        self.meta = meta
        self._curves = curves
        self._metrics = {
            (r["metric"], r["hero_id"], r["bracket"]): r for r in metrics.to_dicts()
        }
        self._items = {(r["hero_id"], r["bracket"], r["item_id"]): r for r in items.to_dicts()}

    @classmethod
    def load(cls, directory: Path | None = None) -> Benchmarks:
        if directory is None:
            root = config.data_dir() / "benchmarks"
            dirs = sorted(p for p in root.glob("*") if (p / "meta.json").exists()) if root.exists() else []
            if not dirs:
                raise FileNotFoundError(f"no benchmarks in {root}; run: python -m backend.analysis.benchmarks build")
            directory = dirs[-1]
        directory = Path(directory)
        curves = pl.read_parquet(directory / "curves.parquet") if (directory / "curves.parquet").exists() else None
        return cls(pl.read_parquet(directory / "metrics.parquet"), pl.read_parquet(directory / "items.parquet"),
                   json.loads((directory / "meta.json").read_text(encoding="utf-8")), curves)

    def distribution(self, metric: str, hero_id: int, bracket: str | None) -> Distribution | None:
        """Most specific distribution with enough samples: hero+bracket, then hero, then bracket."""
        for key in ((metric, hero_id, bracket), (metric, hero_id, ALL), (metric, 0, bracket)):
            r = self._metrics.get(key)
            if key[2] is not None and r and r["n"] >= MIN_MATCHES:
                return Distribution(metric, key[1], key[2], r["n"], {q: r[c] for q, c in zip(QUANTILES, QCOLS)})
        return None

    def bracket_distribution(self, metric: str, hero_id: int, bracket: str) -> Distribution | None:
        """Like distribution(), but never leaves the bracket: hero+bracket, then all heroes in it."""
        d = self.distribution(metric, hero_id, bracket)
        if d is not None and d.bracket == bracket:
            return d
        r = self._metrics.get((metric, 0, bracket))
        if r and r["n"] >= MIN_MATCHES:
            return Distribution(metric, 0, bracket, r["n"], {q: r[c] for q, c in zip(QUANTILES, QCOLS)})
        return None

    def souls_curve(self, hero_id: int, bracket: str | None, min_n: int = 100) -> dict | None:
        """Net-worth curve (minute -> p25/p50/p75) from the most specific scope with enough
        matches at the first mark: hero+bracket, then hero overall, then all heroes in the bracket."""
        if self._curves is None:
            return None
        for hero, b in ((hero_id, bracket), (hero_id, ALL), (0, bracket)):
            if b is None:
                continue
            rows = self._curves.filter((pl.col("hero_id") == hero) & (pl.col("bracket") == b) & (pl.col("n") >= min_n)).sort("minute")
            if rows.height and rows["minute"][0] == rows["minute"].min() and rows.height >= 3:
                return {"hero_id": hero, "bracket": b, "points": rows.select("minute", "n", "p25", "p50", "p75").to_dicts()}
        return None

    def item_timing(self, hero_id: int, bracket: str | None, item_id: int) -> ItemTiming | None:
        for b in (bracket, ALL):
            r = self._items.get((hero_id, b, item_id))
            if b is not None and r and r["n"] >= MIN_ITEM_BUYS:
                return ItemTiming(item_id, b, r["n"], r["buy_rate"], r["p25"], r["p50"], r["p75"])
        return None
