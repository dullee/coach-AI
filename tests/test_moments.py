"""Replay-moment detectors on small synthetic replays (one case that fires, one that doesn't)."""

from __future__ import annotations

import polars as pl
import pytest

from backend.analysis.brackets import brackets
from backend.analysis.issues import Context
from backend.analysis.issues import moments
from backend.analysis.replay_data import ReplayData

OFFSET = 50.0   # server clock = match clock + OFFSET (as in real replays)


def det(name):
    return next(d for d in moments.DETECTORS if d.id == name)


@pytest.fixture
def hero(static):
    return next(h for h in static.playable_heroes() if len(h.abilities) == 4)


def row(tick, slot, **kw):
    base = {"tick": tick, "game_s": tick / 64, "slot": slot, "x": 0.0, "y": 0.0, "z": 0.0, "is_alive": True,
            "health": 1000, "max_health": 1000, "souls": 0, "spent_souls": 0, "death_time": 0.0,
            "has_ultimate_trained": True, "ultimate_cooldown_start": 0.0, "ultimate_cooldown_end": 0.0}
    return {**base, **kw}


def replay(static, hero, timeline, *, kills=None, fights=None, ability_uses=None, objectives=None):
    others = [h for h in static.playable_heroes() if h.id != hero.id][:3]
    players = pl.DataFrame({"slot": [0, 1, 2, 3], "hero_id": [hero.id] + [h.id for h in others],
                            "team": [0, 0, 1, 1], "rank_badge": [55] * 4})
    kills = kills if kills is not None else pl.DataFrame(schema={"tick": pl.Int64, "game_s": pl.Float64,
                                                                 "victim_slot": pl.Int8, "attacker_slot": pl.Int8})
    empty_obj = pl.DataFrame(schema={"tick": pl.Int64, "game_s": pl.Float64, "entity_id": pl.Int64, "health": pl.Int64,
                                     "team": pl.Int8, "x": pl.Float64, "y": pl.Float64, "z": pl.Float64})
    return ReplayData(match_id=1, slot=0, team=0, players=players, kills=kills,
                      ability_uses=ability_uses if ability_uses is not None else pl.DataFrame(schema={"slot": pl.Int8, "game_s": pl.Float64, "ability_class": pl.String}),
                      objectives=objectives if objectives is not None else empty_obj,
                      timeline=pl.DataFrame(timeline), teamfights=fights)


def ctx(static, rd):
    return Context(static=static, benchmarks=None, hero_id=int(rd.players["hero_id"][0]),
                   bracket=brackets(static)[2], player=None, metric_values={}, replay=rd)


def fight_table(starts, kills=2):
    return pl.DataFrame({"fight_id": list(range(len(starts))), "start_tick": [s * 64 for s in starts],
                         "end_tick": [(s + 10) * 64 for s in starts], "start_s": [float(s) for s in starts],
                         "end_s": [float(s + 10) for s in starts], "participant_slots": [[0, 1, 2, 3]] * len(starts),
                         "kills": [kills] * len(starts)})


def test_died_first_in_lost_fights(static, hero):
    starts = [100, 300, 500]
    # In each fight the player (slot 0) dies first, then teammate 1; enemies lose nobody -> lost.
    k = pl.DataFrame({"tick": [s * 64 + 64 for s in starts] + [s * 64 + 128 for s in starts],
                      "game_s": [s + 1.0 for s in starts] + [s + 2.0 for s in starts],
                      "victim_slot": [0] * 3 + [1] * 3, "attacker_slot": [2] * 6}).sort("tick")
    tl = [row(s * 64, 0, death_time=s + 1 + OFFSET) for s in starts] + [row(s * 64 + 64, 0, death_time=s + 1 + OFFSET) for s in starts]
    issue = det("died_first_in_lost_fights").run(ctx(static, replay(static, hero, tl, kills=k, fights=fight_table(starts))))
    assert issue.evidence.player_value == 3 and issue.severity == pytest.approx(1.0) and issue.timestamps_s == [100.0, 300.0, 500.0]
    # Same fights but the player dies second: no issue.
    k2 = k.with_columns(pl.when(pl.col("victim_slot") == 0).then(1).otherwise(0).cast(pl.Int8).alias("victim_slot"))
    assert det("died_first_in_lost_fights").run(ctx(static, replay(static, hero, tl, kills=k2, fights=fight_table(starts)))) is None


def test_ultimate_unused_in_fights(static, hero):
    starts = [100, 300, 500, 700]
    ult = hero.abilities[3]
    # Ultimate ready long before each fight; one death far away so the server-clock offset can be measured.
    k = pl.DataFrame({"tick": [900 * 64], "game_s": [900.0], "victim_slot": [2], "attacker_slot": [0]})
    tl = [row(s * 64, 0, ultimate_cooldown_start=10.0, ultimate_cooldown_end=5.0) for s in starts]
    tl += [row(900 * 64, 2, death_time=900 + OFFSET), row(900 * 64, 0)]
    unused = det("ultimate_unused_in_fights").run(ctx(static, replay(static, hero, tl, kills=k, fights=fight_table(starts, kills=3))))
    assert unused.evidence.player_value == 4 and unused.evidence.details["fights_with_ultimate_ready"] == 4
    # Cast in three of the four fights: only 1 unused -> no issue.
    casts = pl.DataFrame({"slot": [0, 0, 0], "game_s": [102.0, 303.0, 505.0], "ability_class": [ult] * 3})
    assert det("ultimate_unused_in_fights").run(ctx(static, replay(static, hero, tl, kills=k, fights=fight_table(starts, kills=3),
                                                                    ability_uses=casts))) is None


def test_many_near_deaths(static, hero):
    tl = [row(t * 64, 0, health=100) for t in range(100, 1300, 100)]   # 12 separate dips to 10% health
    issue = det("many_near_deaths").run(ctx(static, replay(static, hero, tl)))
    assert issue.evidence.player_value == 12 and 0.5 < issue.severity <= 1
    few = [row(t * 64, 0, health=100) for t in range(100, 600, 100)]    # 5 dips: normal
    assert det("many_near_deaths").run(ctx(static, replay(static, hero, few))) is None


def test_farming_gaps(static, hero):
    # 3:00-6:00 earning steadily, then 90 s with no souls, then earning again.
    tl = [row(t * 64, 0, souls=(t - 180) * 10) for t in range(180, 361)]
    tl += [row(t * 64, 0, souls=1800) for t in range(361, 451)]
    tl += [row(t * 64, 0, souls=1800 + (t - 450) * 10) for t in range(451, 600)]
    issue = det("farming_gaps").run(ctx(static, replay(static, hero, tl)))
    # The gap can start up to ~15 s early: it is measured from the last point where 150 souls had been earned.
    assert issue.evidence.player_value >= 85 and issue.timestamps_s and 345 <= issue.timestamps_s[0] <= 362
    steady = [row(t * 64, 0, souls=(t - 180) * 10) for t in range(180, 600)]
    assert det("farming_gaps").run(ctx(static, replay(static, hero, steady))) is None


def test_objectives_lost_while_away(static, hero):
    ticks = [t * 64 for t in (200, 400, 600, 800, 1000, 1200)]
    obj = pl.DataFrame({"tick": ticks, "game_s": [t / 64 for t in ticks], "entity_id": list(range(6)), "health": [0] * 6,
                        "team": [0] * 6, "x": [0.0] * 6, "y": [0.0] * 6, "z": [0.0] * 6})
    far = [row(t - 1, 0, x=9000.0) for t in ticks]
    issue = det("objectives_lost_while_away").run(ctx(static, replay(static, hero, far, objectives=obj)))
    assert issue.evidence.player_value == 6 and issue.evidence.details["own_objectives_lost"] == 6
    near = [row(t - 1, 0, x=500.0) for t in ticks]
    assert det("objectives_lost_while_away").run(ctx(static, replay(static, hero, near, objectives=obj))) is None


def test_old_replays_without_fights_are_skipped(static, hero):
    rd = replay(static, hero, [row(64, 0)], fights=None)
    assert det("died_first_in_lost_fights").run(ctx(static, rd)) is None
    assert det("ultimate_unused_in_fights").run(ctx(static, rd)) is None
