"""Replay-only detectors (Medium tier). They have no benchmark yet, so they
use fixed, documented rules calibrated on the fixture replays."""

from __future__ import annotations

from backend.analysis.issues.base import Context, Detector
from backend.analysis.metrics.replay import (
    ALONE_DISTANCE, OBJECTIVE_RADIUS, ULT_READY_GRACE_S, death_contexts, objective_presence,
)
from backend.summarize.contracts import Evidence, Issue


def _deaths(ctx: Context):
    if "deaths" not in ctx.cache:
        ctx.cache["deaths"] = death_contexts(ctx.replay)
    return ctx.cache["deaths"]


def _died_far_from_team(ctx: Context) -> Issue | None:
    """Trigger: 2+ deaths with no living teammate within ALONE_DISTANCE, and at
    least 30% of deaths. Severity: (0.2 + 0.8 * share) * min(1, alone / 3)."""
    deaths = _deaths(ctx)
    alone = [d for d in deaths if d.nearest_ally is None or d.nearest_ally > ALONE_DISTANCE]
    if len(alone) < 2 or len(alone) / len(deaths) < 0.3:
        return None
    share = len(alone) / len(deaths)
    return Issue(
        id="died_far_from_team", category="positioning",
        severity=round(min(1.0, (0.2 + 0.8 * share) * min(1.0, len(alone) / 3)), 3),
        evidence=Evidence(player_value=len(alone), unit="deaths", window="full match",
                          details={"total_deaths": len(deaths), "share": round(share, 2),
                                   "alone_means": f"no living teammate within {int(ALONE_DISTANCE)} units"}),
        timestamps_s=[round(d.game_s, 1) for d in alone],
        hero=ctx.hero_name,
    )


MIN_WASTED_ULT_DEATHS = 2


def _died_with_ultimate_ready(ctx: Context) -> Issue | None:
    """Trigger: 2+ deaths where the ultimate was trained and had been ready for
    at least ULT_READY_GRACE_S (a pattern, not a one-off: ~29% of all deaths in
    the fixtures happened with the ult ready, so one such death is normal).
    Severity: min(1, 0.3 * count)."""
    wasted = [d for d in _deaths(ctx) if d.ult_ready_for_s is not None and d.ult_ready_for_s >= ULT_READY_GRACE_S]
    if len(wasted) < MIN_WASTED_ULT_DEATHS:
        return None
    abilities = [ctx.static.item_by_class_name(c) for c in ctx.static.hero(ctx.hero_id).abilities]
    ult = next((a for a in abilities if a is not None and a.ability_type == "ultimate"), None)
    return Issue(
        id="died_with_ultimate_ready", category="abilities",
        severity=round(min(1.0, 0.3 * len(wasted)), 3),
        evidence=Evidence(player_value=len(wasted), unit="deaths", window="full match",
                          details={"ultimate": ult.name if ult else "ultimate",
                                   "total_deaths": len(_deaths(ctx)),
                                   "ready_at_least_s": int(ULT_READY_GRACE_S)}),
        timestamps_s=[round(d.game_s, 1) for d in wasted],
        hero=ctx.hero_name,
    )


def _low_objective_presence(ctx: Context) -> Issue | None:
    """Trigger: 3+ enemy objectives destroyed and the player was alive and within
    OBJECTIVE_RADIUS for under 25% of them. Severity: (0.5 - share) / 0.5."""
    total, near, missed = objective_presence(ctx.replay)
    if total < 3 or near / total >= 0.25:
        return None
    share = near / total
    return Issue(
        id="low_objective_presence", category="objectives",
        severity=round(min(1.0, (0.5 - share) / 0.5), 3),
        evidence=Evidence(player_value=near, unit="enemy objectives", window="full match",
                          details={"enemy_objectives_destroyed": total, "share": round(share, 2),
                                   "present_means": f"alive and within {int(OBJECTIVE_RADIUS)} units"}),
        timestamps_s=missed[:6],
        hero=ctx.hero_name,
    )


DETECTORS = [
    Detector("died_far_from_team", "positioning", True, _died_far_from_team,
             "Several deaths with no teammate nearby."),
    Detector("died_with_ultimate_ready", "abilities", True, _died_with_ultimate_ready,
             "Died while the ultimate was off cooldown."),
    Detector("low_objective_presence", "objectives", True, _low_objective_presence,
             "Absent when the team destroyed enemy objectives."),
]
