"""Estimated level for players without a rank.

An unranked player is compared with "ranked players who have just better
stats": we find the highest bracket whose typical player this match's stats
match or beat (average percentile across all metrics >= 50%), then compare
with the bracket one step above it. If the player is below even the lowest
bracket, the lowest bracket is the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.brackets import Bracket, brackets
from backend.analysis.issues.base import good_percentile
from backend.analysis.metrics import METRICS
from backend.static_data import StaticData

MIN_METRICS = 5   # need at least this many comparable metrics to estimate a level


@dataclass(frozen=True)
class LevelEstimate:
    level: Bracket | None      # highest bracket matched; None = below the lowest bracket
    compare_with: Bracket      # one step above `level` (or the lowest / top bracket)
    scores: dict[str, float]   # bracket id -> average percentile of this match vs that bracket


def estimate_level(values: dict[str, float | None], hero_id: int, static: StaticData,
                   benchmarks: Benchmarks) -> LevelEstimate | None:
    bs = brackets(static)
    scores: dict[str, float] = {}
    for b in bs:
        pcts = []
        for metric in METRICS.values():
            value = values.get(metric.id)
            dist = benchmarks.bracket_distribution(metric.id, hero_id, b.id)
            if value is None or dist is None:
                continue
            pcts.append(good_percentile(metric, dist, value))
        if len(pcts) >= MIN_METRICS:
            scores[b.id] = sum(pcts) / len(pcts)
    if not scores:
        return None
    matched = [b for b in bs if scores.get(b.id, 0) >= 0.5]
    level = matched[-1] if matched else None
    if level is None:
        compare = bs[0]
    else:
        i = bs.index(level)
        compare = bs[min(i + 1, len(bs) - 1)]
    return LevelEstimate(level, compare, scores)
