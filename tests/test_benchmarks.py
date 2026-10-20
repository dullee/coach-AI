from __future__ import annotations

import polars as pl
import pytest

from backend.analysis.benchmarks import Benchmarks, Distribution
from backend.analysis.benchmarks.build import (
    MIN_MATCHES, QUANTILES, cache_name, compute_item_benchmarks, compute_metric_benchmarks, pick_files,
)
from backend.analysis.brackets import ALL, brackets
from backend.analysis.metrics import METRICS

QCOLS = [f"p{int(q * 100):02d}" for q in QUANTILES]


def test_metric_benchmarks_are_ordered(static, lake_frame):
    b = compute_metric_benchmarks(lake_frame, static)
    assert set(b["metric"].unique()) == set(METRICS)
    for lo, hi in zip(QCOLS, QCOLS[1:]):
        assert (b[lo] <= b[hi] + 1e-9).all()
    # all three scopes exist: hero+bracket, hero overall (bracket "all"), all heroes (hero_id 0)
    assert (b["bracket"] == ALL).any() and (b["hero_id"] == 0).any()
    assert set(b.filter(pl.col("bracket") != ALL)["bracket"].unique()) <= {x.id for x in brackets(static)}


def test_item_benchmarks(static, lake_frame):
    items = compute_item_benchmarks(lake_frame, static)
    assert items.height > 0
    assert items["buy_rate"].is_between(0, 1).all()
    assert (items["p25"] <= items["p50"]).all() and (items["p50"] <= items["p75"]).all()
    assert set(items["item_id"].unique()) <= set(static.items)


def test_distribution_percentile():
    d = Distribution("m", 1, "t05-06", 500, dict(zip(QUANTILES, [10, 20, 30, 40, 50, 60, 70])))
    assert d.p50 == 40
    assert d.percentile(40) == pytest.approx(0.5)
    assert d.percentile(45) == pytest.approx(0.625)
    assert 0 < d.percentile(-100) < 0.05 and 0.95 < d.percentile(1e9) < 1
    values = [d.percentile(v) for v in range(0, 80, 5)]
    assert values == sorted(values)


def _bench(rows: list[dict]) -> Benchmarks:
    base = {c: 0.0 for c in QCOLS}
    df = pl.DataFrame([{**base, "p50": 1.0, **r} for r in rows])
    return Benchmarks(df, pl.DataFrame(schema={"hero_id": pl.Int32, "bracket": pl.String, "item_id": pl.Int64}), {})


def test_lookup_falls_back_when_sample_is_small():
    b = _bench([
        {"metric": "m", "hero_id": 7, "bracket": "t01-02", "n": MIN_MATCHES - 1, "p50": 1.0},
        {"metric": "m", "hero_id": 7, "bracket": ALL, "n": MIN_MATCHES, "p50": 2.0},
        {"metric": "m", "hero_id": 0, "bracket": "t01-02", "n": 10_000, "p50": 3.0},
    ])
    assert b.distribution("m", 7, "t01-02").p50 == 2.0          # hero overall beats hero+bracket with n too small
    assert b.distribution("m", 8, "t01-02").p50 == 3.0          # unknown hero -> all heroes in bracket
    assert b.distribution("m", 7, None).p50 == 2.0              # unranked -> hero overall
    assert b.distribution("other", 7, "t01-02") is None


def test_pick_files_and_cache_names():
    files = [
        {"key": "v1/tables/match_player/g2/base/part-1/A.parquet", "kind": "base", "partition": 1, "hi": 100, "bytes": 1},
        {"key": "v1/tables/match_player/g2/base/part-2/A.parquet", "kind": "base", "partition": 2, "hi": 100, "bytes": 1},
        {"key": "v1/tables/match_player/g2/delta/B.parquet", "kind": "delta", "hi": 100, "bytes": 1},
        {"key": "v1/tables/match_player/g2/delta/OLD.parquet", "kind": "delta", "hi": 5, "bytes": 1},
    ]
    picked = pick_files({"tables": {"match_player": {"files": files}}}, since_epoch=50, max_files=1)
    assert [f["key"].split("/")[-2:] for f in picked] == [["part-2", "A.parquet"], ["delta", "B.parquet"]]
    # Same basename in two partitions must not collide in the local cache.
    assert cache_name(files[0]["key"]) != cache_name(files[1]["key"])
