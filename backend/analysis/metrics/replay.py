"""Replay-only metrics (Medium tier): death context, objective presence, ability casts.

Clock note (verified in Phase 0 follow-up on two replays): the timeline's
timer fields (death_time, ultimate_cooldown_*) use the server clock, which is
the match clock plus a fixed per-match offset (73.4 s and 99.5 s in our
fixtures). The offset is measured from deaths. ultimate_cooldown_end holds
the cast time and ultimate_cooldown_start the ready time, so "ready at" is
taken as the larger of the two.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from backend.analysis.replay_data import ReplayData
from backend.static_data import StaticData

ALONE_DISTANCE = 3000.0      # world units; ~85th percentile of nearest-ally distance at deaths in fixtures
OBJECTIVE_RADIUS = 3000.0    # "present" at an objective kill if this close
ULT_READY_GRACE_S = 20.0     # ult must have been ready this long before death to count


@dataclass(frozen=True)
class DeathContext:
    game_s: float
    nearest_ally: float | None   # None = no living teammate
    ult_trained: bool
    ult_ready_for_s: float | None  # None = not ready; inf = never cast yet


def server_clock_offset(rd: ReplayData) -> float | None:
    """server time - match time, from death_time at each death tick (median)."""
    offsets = []
    for k in rd.kills.filter(pl.col("victim_slot").is_in(rd.timeline["slot"].unique().to_list())).iter_rows(named=True):
        row = rd.timeline.filter((pl.col("slot") == k["victim_slot"]) & (pl.col("tick") >= k["tick"])).sort("tick").head(1)
        if row.height and row["death_time"][0] > 0:
            offsets.append(row["death_time"][0] - k["game_s"])
    return float(pl.Series(offsets).median()) if offsets else None


def _xyz_dist(a: dict, b: dict) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2) ** 0.5


def death_contexts(rd: ReplayData) -> list[DeathContext]:
    offset = server_clock_offset(rd)
    out = []
    for k in rd.kills.filter(pl.col("victim_slot") == rd.slot).sort("tick").iter_rows(named=True):
        at = rd.timeline.filter(pl.col("tick") == k["tick"] - 1)
        me = at.filter(pl.col("slot") == rd.slot)
        if me.is_empty():
            continue
        me_row = me.row(0, named=True)
        allies = at.filter((pl.col("slot") != rd.slot) & pl.col("is_alive")).iter_rows(named=True)
        dists = [_xyz_dist(me_row, a) for a in allies]
        ready_for = None
        if me_row["has_ultimate_trained"] and offset is not None:
            ready_at = max(me_row["ultimate_cooldown_start"], me_row["ultimate_cooldown_end"])
            now = k["game_s"] + offset
            if ready_at <= 0:
                ready_for = float("inf")
            elif ready_at <= now:
                ready_for = now - ready_at
        out.append(DeathContext(k["game_s"], min(dists) if dists else None, bool(me_row["has_ultimate_trained"]), ready_for))
    return out


def objective_presence(rd: ReplayData) -> tuple[int, int, list[float]]:
    """(enemy objectives destroyed, how many the player was near, times of the missed ones)."""
    destroyed = (
        rd.objectives.filter((pl.col("health") == 0) & (pl.col("team") != rd.team))
        .sort("tick").group_by("entity_id", maintain_order=True).first()
    )
    me = rd.timeline.filter(pl.col("slot") == rd.slot)
    near, missed = 0, []
    for o in destroyed.iter_rows(named=True):
        row = me.filter(pl.col("tick") <= o["tick"]).sort("tick").tail(1)
        if row.height and row["is_alive"][0] and _xyz_dist(row.row(0, named=True), o) <= OBJECTIVE_RADIUS:
            near += 1
        else:
            missed.append(round(o["game_s"], 1))
    return destroyed.height, near, missed


def ability_casts(rd: ReplayData, static: StaticData) -> dict[str, int]:
    """Casts per hero ability. Abilities with 0 casts are left out: some are
    passives (e.g. a weapon-based ability showed 0 in every fixture) and the
    static data has no reliable passive flag, so 0 can't be read as "unused"."""
    hero_id = int(rd.players.filter(pl.col("slot") == rd.slot)["hero_id"][0])
    mine = rd.ability_uses.filter(pl.col("slot") == rd.slot)
    counts = dict(mine.group_by("ability_class").len().iter_rows())
    out = {}
    for cls in static.hero(hero_id).abilities:
        if counts.get(cls):
            item = static.item_by_class_name(cls)
            out[item.name if item else cls] = int(counts[cls])
    return out
