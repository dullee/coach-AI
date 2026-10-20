"""Farming: souls (net worth) per minute by phase, last hits in lane."""

from __future__ import annotations

from backend.analysis.metrics.base import Metric, minutes, only_if, value_at

LANE_END_S = 600     # laning phase: first 10 minutes
MID_END_S = 1200     # mid game: 10-20 minutes


def _spm_0_10(df, static):
    return only_if(value_at(df, "st_t", "st_nw", LANE_END_S) / 10.0, df["duration_s"] >= LANE_END_S)


def _spm_10_20(df, static):
    gained = value_at(df, "st_t", "st_nw", MID_END_S) - value_at(df, "st_t", "st_nw", LANE_END_S)
    return only_if(gained / 10.0, df["duration_s"] >= MID_END_S)


def _spm(df, static):
    return df["net_worth"].cast(float) / minutes(df)


def _last_hits_10(df, static):
    return only_if(value_at(df, "st_t", "st_creep", LANE_END_S), df["duration_s"] >= LANE_END_S)


METRICS = [
    Metric("souls_per_min_0_10", "Souls per minute in lane", "souls/min", "0-10 min", "farming", True, _spm_0_10),
    Metric("souls_per_min_10_20", "Souls per minute in mid game", "souls/min", "10-20 min", "farming", True, _spm_10_20),
    Metric("souls_per_min", "Souls per minute", "souls/min", "full match", "farming", True, _spm),
    Metric("last_hits_0_10", "Last hits in lane", "last hits", "0-10 min", "farming", True, _last_hits_10),
]
