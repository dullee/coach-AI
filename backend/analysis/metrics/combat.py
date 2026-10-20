"""Combat and teamplay: damage per minute, kill participation, gun accuracy."""

from __future__ import annotations

import polars as pl

from backend.analysis.metrics.base import Metric, last, minutes, only_if


def _dpm(df, static):
    return last(df, "st_dmg") / minutes(df)


def _kill_participation(df, static):
    # Denominator = enemy deaths, not team kills: assists also count when an
    # enemy dies to troopers or towers (with team kills, values went above 1).
    enemy_deaths = df["enemy_deaths"].cast(pl.Float64)
    return only_if((df["kills"] + df["assists"]).cast(pl.Float64) / enemy_deaths, enemy_deaths > 0)


def _accuracy(df, static):
    hit, miss = last(df, "st_hit"), last(df, "st_miss")
    return only_if(hit / (hit + miss), (hit + miss) > 50)


METRICS = [
    Metric("damage_per_min", "Hero damage per minute", "damage/min", "full match", "combat", True, _dpm),
    Metric("kill_participation", "Kill participation", "share of enemy deaths", "full match", "teamplay", True, _kill_participation),
    Metric("accuracy", "Gun accuracy", "share of shots hit", "full match", "mechanics", True, _accuracy),
]
