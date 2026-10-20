from __future__ import annotations

import polars as pl
import pytest

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.benchmarks.build import QUANTILES
from backend.analysis.brackets import brackets
from backend.analysis.level import estimate_level
from backend.analysis.metrics import METRICS

QCOLS = [f"p{int(q * 100):02d}" for q in QUANTILES]
SPREAD = [-30, -20, -10, 0, 10, 20, 30]  # quantile offsets around each bracket's median


def ladder(static, hero_id):
    """Synthetic benchmarks where each bracket's median is 100 higher (better) than the one below."""
    rows = []
    for i, b in enumerate(brackets(static)):
        for m in METRICS.values():
            med = 100 + 100 * i if m.higher_is_better else 1000 - 100 * i
            rows.append({"metric": m.id, "hero_id": hero_id, "bracket": b.id, "n": 1000,
                         **{c: float(med + o) for c, o in zip(QCOLS, SPREAD)}})
    return Benchmarks(pl.DataFrame(rows), pl.DataFrame(schema={"hero_id": pl.Int32, "bracket": pl.String, "item_id": pl.Int64}), {})


def values_at(static, i):
    """Metric values equal to the median of bracket i."""
    return {m.id: float(100 + 100 * i if m.higher_is_better else 1000 - 100 * i) for m in METRICS.values()}


@pytest.fixture
def hero(static):
    return static.playable_heroes()[0].id


def test_compares_with_the_bracket_just_above(static, hero):
    bs = brackets(static)
    est = estimate_level(values_at(static, 1), hero, static, ladder(static, hero))
    assert est.level == bs[1] and est.compare_with == bs[2]
    assert est.scores[bs[0].id] > 0.9 and est.scores[bs[1].id] == pytest.approx(0.5) and est.scores[bs[2].id] < 0.1


def test_below_lowest_bracket_compares_with_lowest(static, hero):
    bs = brackets(static)
    est = estimate_level(values_at(static, -3), hero, static, ladder(static, hero))
    assert est.level is None and est.compare_with == bs[0]


def test_top_player_compares_with_top(static, hero):
    bs = brackets(static)
    est = estimate_level(values_at(static, 9), hero, static, ladder(static, hero))
    assert est.level == bs[-1] and est.compare_with == bs[-1]


def test_too_few_metrics_gives_no_estimate(static, hero):
    few = dict(list(values_at(static, 1).items())[:2])
    assert estimate_level(few, hero, static, ladder(static, hero)) is None


def test_real_unranked_player_gets_an_estimate(static, sample_benchmarks):
    from backend.summarize.builder import build_summary
    from tests.conftest import fixture_metadata

    meta = fixture_metadata(120849236)  # unranked match
    s = build_summary(meta, meta["players"][0]["account_id"], static, sample_benchmarks)
    assert s.player.bracket_basis == "estimated_from_stats"
    assert s.player.estimated_level and s.player.rank_bracket in {b.label for b in brackets(static)}
