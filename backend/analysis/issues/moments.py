"""Replay-moment detectors (Medium tier): fights, close calls, idle stretches, undefended objectives.

Thresholds come from 41 real player-matches (3 fixture replays x 12 players +
5 of the developer's replays); each is set to flag roughly the top 10-15%:

| detector                    | fixture median | trigger                         |
|-----------------------------|----------------|---------------------------------|
| died_first_in_lost_fights   | 1              | 2+                              |
| ultimate_unused_in_fights   | 1              | 3+ and >= half of ult-ready fights |
| many_near_deaths            | 6              | 10+                             |
| farming_gaps                | 0 s            | 60+ s idle in 3:00-20:00        |
| objectives_lost_while_away  | 3              | 5+ and >= half of own losses    |
"""

from __future__ import annotations

from backend.analysis.issues.base import Context, Detector
from backend.analysis.metrics.replay import (
    DEFEND_DISTANCE, FARM_GAP_MAX_SOULS, FARM_GAP_MIN_S, NEAR_DEATH_HEALTH, farming_gaps, fights,
    near_deaths, objectives_lost_away,
)
from backend.summarize.contracts import Evidence, Issue


def _fights(ctx: Context):
    if "fights" not in ctx.cache:
        ctx.cache["fights"] = fights(ctx.replay, ctx.static)
    return ctx.cache["fights"]


def _died_first_in_lost_fights(ctx: Context) -> Issue | None:
    """Severity: min(1, 0.35 * count)."""
    mine = [f for f in _fights(ctx) if f.took_part]
    hits = [f for f in mine if f.died_first and f.won is False]
    if len(hits) < 2:
        return None
    return Issue(
        id="died_first_in_lost_fights", category="positioning", severity=round(min(1.0, 0.35 * len(hits)), 3),
        evidence=Evidence(player_value=len(hits), unit="fights", window="full match",
                          details={"teamfights_joined": len(mine),
                                   "lost_fights_joined": sum(1 for f in mine if f.won is False),
                                   "meaning": "you were your team's first death in a fight your team lost"}),
        timestamps_s=[round(f.start_s, 1) for f in hits], hero=ctx.hero_name,
    )


def _ultimate_unused_in_fights(ctx: Context) -> Issue | None:
    """Severity: min(1, 0.2 * count)."""
    ready = [f for f in _fights(ctx) if f.took_part and f.ult_ready_at_start]
    unused = [f for f in ready if not f.ult_cast]
    if len(unused) < 3 or len(unused) < len(ready) / 2:
        return None
    return Issue(
        id="ultimate_unused_in_fights", category="abilities", severity=round(min(1.0, 0.2 * len(unused)), 3),
        evidence=Evidence(player_value=len(unused), unit="fights", window="full match",
                          details={"fights_with_ultimate_ready": len(ready),
                                   "meaning": "ultimate was ready when the teamfight started and was not cast before it ended"}),
        timestamps_s=[round(f.start_s, 1) for f in unused], hero=ctx.hero_name,
    )


def _many_near_deaths(ctx: Context) -> Issue | None:
    """Severity: min(1, (count - 6) / 8): 0.5 at 10, 1.0 at 14."""
    times = near_deaths(ctx.replay)
    if len(times) < 10:
        return None
    return Issue(
        id="many_near_deaths", category="survival", severity=round(min(1.0, (len(times) - 6) / 8), 3),
        evidence=Evidence(player_value=len(times), unit="close calls", window="full match",
                          details={"meaning": f"health under {int(NEAR_DEATH_HEALTH * 100)}% of maximum and survived",
                                   "typical_player": 6}),
        timestamps_s=times[:8], hero=ctx.hero_name,
    )


def _farming_gaps(ctx: Context) -> Issue | None:
    """Severity: min(1, idle seconds / 180)."""
    gaps = farming_gaps(ctx.replay)
    idle = sum(b - a for a, b in gaps)
    if idle < 60:
        return None
    return Issue(
        id="farming_gaps", category="farming", severity=round(min(1.0, idle / 180), 3),
        evidence=Evidence(player_value=round(idle), unit="seconds", window="3:00-20:00",
                          details={"stretches": len(gaps),
                                   "meaning": f"alive but earning under {FARM_GAP_MAX_SOULS} souls "
                                              f"for at least {int(FARM_GAP_MIN_S)} seconds"}),
        timestamps_s=[a for a, _ in gaps], hero=ctx.hero_name,
    )


def _objectives_lost_while_away(ctx: Context) -> Issue | None:
    """Severity: share of own objective losses that happened while away."""
    total, away = objectives_lost_away(ctx.replay)
    if len(away) < 5 or len(away) < total / 2:
        return None
    return Issue(
        id="objectives_lost_while_away", category="objectives", severity=round(min(1.0, len(away) / total), 3),
        evidence=Evidence(player_value=len(away), unit="objectives", window="full match",
                          details={"own_objectives_lost": total,
                                   "away_means": f"alive and more than {int(DEFEND_DISTANCE)} units away"}),
        timestamps_s=away[:6], hero=ctx.hero_name,
    )


DETECTORS = [
    Detector("died_first_in_lost_fights", "positioning", True, _died_first_in_lost_fights,
             "First to die in teamfights your team lost.", label="First to die in lost fights"),
    Detector("ultimate_unused_in_fights", "abilities", True, _ultimate_unused_in_fights,
             "Ultimate ready but not used in teamfights.", label="Ultimate unused in fights"),
    Detector("many_near_deaths", "survival", True, _many_near_deaths,
             "Many close calls at very low health.", label="Close calls at low health"),
    Detector("farming_gaps", "farming", True, _farming_gaps,
             "Stretches of a minute or more earning almost nothing.", label="Idle stretches (no farm)"),
    Detector("objectives_lost_while_away", "objectives", True, _objectives_lost_while_away,
             "Own objectives lost while far away.", label="Away when your objectives fell"),
]
