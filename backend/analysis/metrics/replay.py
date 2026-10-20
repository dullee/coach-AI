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


# ---- replay moments (calibrated on fixture + developer replays, see docs/phase2-notes.md) -------

REAL_FIGHT_MIN_KILLS = 2         # Boon also reports kill-less lane skirmishes; those aren't teamfights
FIGHT_KILL_GRACE_S = 3.0         # kills right after the last damage still belong to the fight
NEAR_DEATH_HEALTH = 0.15         # under 15% of max health...
NEAR_DEATH_SURVIVE_S = 10.0      # ...and not dead within 10 s
NEAR_DEATH_SEPARATION_S = 15.0   # separate episodes
FARM_WINDOW_S = (180.0, 1200.0)  # 3:00-20:00, when farm matters most
FARM_GAP_MIN_S = 60.0            # a gap is at least a minute...
FARM_GAP_MAX_SOULS = 150         # ...earning fewer souls than this
DEFEND_DISTANCE = 4000.0         # "away" from your own objective when it fell


@dataclass(frozen=True)
class Fight:
    start_s: float
    end_s: float
    won: bool | None            # None = even
    ally_kills: int
    enemy_kills: int
    took_part: bool
    died_first: bool             # player was the first of their team to die in it
    ult_ready_at_start: bool
    ult_cast: bool


def _team_of(rd: ReplayData) -> dict[int, int]:
    return dict(zip(rd.players["slot"].to_list(), rd.players["team"].to_list()))


def _ultimate_class(rd: ReplayData, static: StaticData) -> str | None:
    hero_id = int(rd.players.filter(pl.col("slot") == rd.slot)["hero_id"][0])
    for cls in static.hero(hero_id).abilities:
        item = static.item_by_class_name(cls)
        if item and item.ability_type == "ultimate":
            return cls
    return None


def fights(rd: ReplayData, static: StaticData) -> list[Fight]:
    """Real teamfights (2+ kills) with the outcome for the player's team and the player's part in them."""
    if rd.teamfights is None or rd.teamfights.is_empty():
        return []
    team = _team_of(rd)
    offset = server_clock_offset(rd)
    ult = _ultimate_class(rd, static)
    me = rd.timeline.filter(pl.col("slot") == rd.slot).sort("tick")
    casts = rd.ability_uses.filter((pl.col("slot") == rd.slot) & (pl.col("ability_class") == ult)) if ult else None
    out = []
    for f in rd.teamfights.filter(pl.col("kills") >= REAL_FIGHT_MIN_KILLS).sort("start_tick").iter_rows(named=True):
        ks = rd.kills.filter(pl.col("game_s").is_between(f["start_s"], f["end_s"] + FIGHT_KILL_GRACE_S)).sort("tick")
        victims = ks["victim_slot"].to_list()
        ally_deaths = [v for v in victims if team.get(v) == rd.team]
        enemy_deaths = [v for v in victims if v is not None and team.get(v) != rd.team]
        won = None if len(enemy_deaths) == len(ally_deaths) else len(enemy_deaths) > len(ally_deaths)
        took_part = rd.slot in (f["participant_slots"] or [])
        at_start = me.filter(pl.col("tick") <= f["start_tick"]).tail(1)
        ready = False
        if ult and offset is not None and at_start.height and at_start["is_alive"][0] and at_start["has_ultimate_trained"][0]:
            ready_at = max(at_start["ultimate_cooldown_start"][0], at_start["ultimate_cooldown_end"][0])
            ready = ready_at <= f["start_s"] + offset
        cast = bool(casts is not None and casts.filter(pl.col("game_s").is_between(f["start_s"], f["end_s"])).height)
        out.append(Fight(f["start_s"], f["end_s"], won, len(enemy_deaths), len(ally_deaths), took_part,
                         bool(ally_deaths) and ally_deaths[0] == rd.slot, ready, cast))
    return out


def near_deaths(rd: ReplayData) -> list[float]:
    """Match times where the player dropped under NEAR_DEATH_HEALTH of max health and survived."""
    me = rd.timeline.filter(pl.col("slot") == rd.slot).sort("tick")
    low = me.filter(pl.col("is_alive") & (pl.col("max_health") > 0)
                    & (pl.col("health") < pl.col("max_health") * NEAR_DEATH_HEALTH))
    deaths = rd.kills.filter(pl.col("victim_slot") == rd.slot)["game_s"].to_list()
    out: list[float] = []
    for t in low["game_s"].to_list():
        if t is None or (out and t - out[-1] < NEAR_DEATH_SEPARATION_S):
            continue
        if any(0 <= d - t <= NEAR_DEATH_SURVIVE_S for d in deaths):
            continue
        out.append(round(t, 1))
    return out


def farming_gaps(rd: ReplayData) -> list[tuple[float, float]]:
    """(start, end) of stretches in 3:00-20:00 where the player was alive but earned fewer than
    FARM_GAP_MAX_SOULS souls for at least FARM_GAP_MIN_S (wandering, waiting, slow rotations).

    No combat filter: the replay's in-combat timer also counts trooper fights (44% of lane
    time in a fixture), and a player who is fighting is normally also earning."""
    lo, hi = FARM_WINDOW_S
    me = (rd.timeline.filter((pl.col("slot") == rd.slot) & pl.col("game_s").is_between(lo, hi))
          .filter(pl.col("tick") % 64 == 0).sort("tick")      # one sample per second (64 ticks/s)
          .with_columns((pl.col("souls") + pl.col("spent_souls")).alias("earned")))
    gaps: list[tuple[float, float]] = []
    start = start_earned = prev_t = None

    def close():
        if start is not None and prev_t - start >= FARM_GAP_MIN_S:
            gaps.append((round(start, 1), round(prev_t, 1)))

    for t, earned, alive in me.select("game_s", "earned", "is_alive").iter_rows():
        if not alive:
            close()
            start = None
        elif start is None or earned - start_earned >= FARM_GAP_MAX_SOULS:
            close()
            start, start_earned = t, earned
        prev_t = t
    close()
    return gaps


def objectives_lost_away(rd: ReplayData) -> tuple[int, list[float]]:
    """(own objectives destroyed, times of those that fell while the player was alive and far away)."""
    lost = (rd.objectives.filter((pl.col("health") == 0) & (pl.col("team") == rd.team))
            .sort("tick").group_by("entity_id", maintain_order=True).first())
    me = rd.timeline.filter(pl.col("slot") == rd.slot)
    away = []
    for o in lost.iter_rows(named=True):
        row = me.filter(pl.col("tick") <= o["tick"]).sort("tick").tail(1)
        if row.height and row["is_alive"][0] and _xyz_dist(row.row(0, named=True), o) > DEFEND_DISTANCE:
            away.append(round(o["game_s"], 1))
    return lost.height, away


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
