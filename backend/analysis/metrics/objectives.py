"""Objective damage (guardians, walkers, shrines, patron, mid boss)."""

from __future__ import annotations

from backend.analysis.metrics.base import Metric, last, minutes


def _objective_dpm(df, static):
    return last(df, "st_boss") / minutes(df)


METRICS = [
    Metric("objective_damage_per_min", "Objective damage per minute", "damage/min", "full match", "objectives", True, _objective_dpm),
]
