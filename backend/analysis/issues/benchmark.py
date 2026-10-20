"""Detectors that compare one metric against the player's hero + rank bracket.

Severity (all of them): see base.benchmark_severity. Trigger: player in the
bottom 35% of the bracket for that metric.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.analysis.issues.base import (
    ISSUE_BELOW, Context, Detector, benchmark_severity, good_percentile, metric_evidence,
)
from backend.analysis.metrics import METRICS
from backend.analysis.metrics.farming import LANE_END_S
from backend.summarize.contracts import Issue


def _death_times(ctx: Context, before_s: float | None = None) -> list[float]:
    times = ctx.player["death_t"][0].to_list() if ctx.player["death_t"][0] is not None else []
    return [float(t) for t in times if before_s is None or t < before_s]


def metric_detector(
    issue_id: str, metric_id: str, doc: str,
    timestamps: Callable[[Context], list[float]] | None = None,
) -> Detector:
    metric = METRICS[metric_id]

    def run(ctx: Context) -> Issue | None:
        value = ctx.metric_values.get(metric_id)
        dist = ctx.distribution(metric_id)
        if value is None or dist is None:
            return None
        g = good_percentile(metric, dist, value)
        if g >= ISSUE_BELOW:
            return None
        return Issue(
            id=issue_id, category=metric.category, severity=benchmark_severity(g),
            evidence=metric_evidence(metric, value, dist, g, ctx.static),
            timestamps_s=timestamps(ctx) if timestamps else [], hero=ctx.hero_name,
        )

    return Detector(issue_id, metric.category, False, run, doc)


DETECTORS = [
    metric_detector("low_souls_per_min_laning", "souls_per_min_0_10",
                    "Earned fewer souls than most players on this hero and rank in the first 10 minutes."),
    metric_detector("low_souls_per_min_midgame", "souls_per_min_10_20",
                    "Souls income from 10 to 20 minutes is below the bracket."),
    metric_detector("low_last_hits_laning", "last_hits_0_10",
                    "Few lane troopers last-hit by 10 minutes."),
    metric_detector("low_damage_output", "damage_per_min",
                    "Hero damage per minute below the bracket."),
    metric_detector("low_kill_participation", "kill_participation",
                    "Took part in a small share of the team's kills."),
    metric_detector("low_accuracy", "accuracy",
                    "Hit a smaller share of shots than most players on this hero."),
    metric_detector("many_deaths_laning", "deaths_0_10",
                    "Died more in the first 10 minutes than most players on this hero.",
                    timestamps=lambda ctx: _death_times(ctx, LANE_END_S)),
    metric_detector("many_deaths_overall", "deaths_per_10_min",
                    "Died more often than most players on this hero and rank.",
                    timestamps=lambda ctx: _death_times(ctx)),
    metric_detector("low_objective_damage", "objective_damage_per_min",
                    "Dealt little damage to guardians, walkers, shrines and the patron."),
    metric_detector("late_first_big_item", "first_big_item_s",
                    "First tier-3+ item came later than for most players on this hero."),
]
