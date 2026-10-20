"""What every detector gets, and the shared severity rule for benchmark comparisons."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

from backend.analysis.benchmarks import Benchmarks, Distribution
from backend.analysis.brackets import Bracket
from backend.analysis.metrics.base import Metric
from backend.analysis.replay_data import ReplayData
from backend.static_data import StaticData
from backend.summarize.contracts import Evidence, Issue

# A benchmark metric becomes an Issue when the player is in the bottom 35% of
# their bracket (after flipping "lower is better" metrics). Severity grows
# linearly from ~0.33 at the 35th percentile to 1.0 at the 5th.
ISSUE_BELOW = 0.35
STRENGTH_ABOVE = 0.70


@dataclass
class Context:
    static: StaticData
    benchmarks: Benchmarks
    hero_id: int
    bracket: Bracket | None
    player: pl.DataFrame                      # 1-row player-match frame (API data)
    metric_values: dict[str, float | None]
    replay: ReplayData | None = None
    cache: dict = field(default_factory=dict)  # detectors can share expensive replay computations

    @property
    def hero_name(self) -> str:
        return self.static.hero_name(self.hero_id)

    @property
    def bracket_id(self) -> str | None:
        return self.bracket.id if self.bracket else None

    def distribution(self, metric_id: str) -> Distribution | None:
        return self.benchmarks.distribution(metric_id, self.hero_id, self.bracket_id)


def good_percentile(metric: Metric, dist: Distribution, value: float) -> float:
    p = dist.percentile(value)
    return p if metric.higher_is_better else 1 - p


def benchmark_severity(good_pct: float) -> float:
    return round(min(1.0, max(0.0, (0.5 - good_pct) / 0.45)), 3)


def metric_evidence(metric: Metric, value: float, dist: Distribution, good_pct: float, **details) -> Evidence:
    return Evidence(
        player_value=round(value, 3), bracket_p50=round(dist.p50, 3), percentile=round(good_pct, 3),
        unit=metric.unit, window=metric.window,
        details={"benchmark_scope": f"{'hero' if dist.hero_id else 'all heroes'} / {dist.bracket}", "benchmark_n": dist.n, **details},
    )


@dataclass(frozen=True)
class Detector:
    id: str
    category: str
    needs_replay: bool
    run: Callable[[Context], Issue | None]
    doc: str = ""
