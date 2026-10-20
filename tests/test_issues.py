"""Unit tests for every detector (CLAUDE.md Phase 2: "every detector has a unit test")."""

from __future__ import annotations

import polars as pl
import pytest

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.benchmarks.build import QUANTILES
from backend.analysis.brackets import brackets, next_bracket_up
from backend.analysis.frame import from_metadata
from backend.analysis.issues import DETECTORS, Context, run_all
from backend.analysis.issues import benchmark as bench_detectors
from backend.analysis.issues import items as item_detectors
from backend.analysis.issues import replay as replay_detectors
from backend.analysis.metrics import METRICS
from backend.analysis.metrics.items import big_item_ids
from backend.analysis.replay_data import ReplayData
from tests.conftest import FIXTURES, fixture_metadata

QCOLS = [f"p{int(q * 100):02d}" for q in QUANTILES]
KNOTS = [50, 60, 80, 100, 120, 140, 150]   # p05..p95 used for every synthetic metric

# issue id -> metric it checks (explicit, so a renamed metric breaks this test)
METRIC_OF = {
    "low_souls_per_min_laning": "souls_per_min_0_10",
    "low_souls_per_min_midgame": "souls_per_min_10_20",
    "low_last_hits_laning": "last_hits_0_10",
    "low_damage_output": "damage_per_min",
    "low_kill_participation": "kill_participation",
    "low_accuracy": "accuracy",
    "many_deaths_laning": "deaths_0_10",
    "many_deaths_overall": "deaths_per_10_min",
    "low_objective_damage": "objective_damage_per_min",
    "late_first_big_item": "first_big_item_s",
}
TESTED = set(METRIC_OF) | {"late_core_items", "died_far_from_team", "died_with_ultimate_ready", "low_objective_presence"}
TESTED |= {"died_first_in_lost_fights", "ultimate_unused_in_fights", "many_near_deaths", "farming_gaps",
           "objectives_lost_while_away"}   # tests/test_moments.py


@pytest.fixture(scope="module")
def setup(static):
    hero = next(h for h in static.playable_heroes() if len(h.abilities) == 4)
    bracket = brackets(static)[2]
    item_id = big_item_ids(static)[0]
    up = next_bracket_up(bracket, static)
    metrics = pl.DataFrame([
        {"metric": m, "hero_id": hero.id, "bracket": b, "n": 1000, **dict(zip(QCOLS, map(float, KNOTS)))}
        for m in METRICS for b in (bracket.id, up.id)
    ])
    items = pl.DataFrame([{
        "hero_id": hero.id, "bracket": next_bracket_up(bracket, static).id, "item_id": item_id,
        "n": 500, "p25": 500.0, "p50": 600.0, "p75": 700.0, "games": 1000, "buy_rate": 0.5,
    }])
    # A real player row (first player of a fixture match) to carry list columns.
    row = from_metadata(fixture_metadata(FIXTURES[0]["match_id"])).slice(0, 1).with_columns(pl.lit(hero.id).alias("hero_id"))
    return {"hero": hero, "bracket": bracket, "item_id": item_id, "row": row,
            "bench": Benchmarks(metrics, items, {"patch_start": "2026-09-29"})}


def ctx_for(static, s, values=None, row=None, replay=None):
    return Context(static=static, benchmarks=s["bench"], hero_id=s["hero"].id, bracket=s["bracket"],
                   player=row if row is not None else s["row"], metric_values=values or {}, replay=replay)


def test_every_detector_is_tested_and_ids_unique():
    ids = [d.id for d in DETECTORS]
    assert len(ids) >= 10 and len(ids) == len(set(ids))
    assert set(ids) == TESTED


# ---- benchmark detectors --------------------------------------------------------

@pytest.mark.parametrize("detector", bench_detectors.DETECTORS, ids=lambda d: d.id)
def test_metric_detector(detector, static, setup):
    metric = METRICS[METRIC_OF[detector.id]]
    bad, worse, fine = (55, 52, 100) if metric.higher_is_better else (145, 148, 100)

    def run(v):
        return detector.run(ctx_for(static, setup, {metric.id: float(v)}))

    assert run(fine) is None
    issue = run(bad)
    assert issue is not None and issue.id == detector.id
    assert issue.category == metric.category and 0 < issue.severity <= 1
    assert issue.evidence.player_value == bad and issue.evidence.bracket_p50 == 100
    assert issue.evidence.unit == metric.unit and issue.hero == setup["hero"].name
    assert run(worse).severity >= issue.severity
    # Missing value or missing benchmark -> no issue
    assert run_none(detector, static, setup, metric.id) is None


def run_none(detector, static, setup, metric_id):
    return detector.run(ctx_for(static, setup, {metric_id: None}))


def test_death_detectors_report_death_times(static, setup):
    row = setup["row"].with_columns(pl.Series("death_t", [[100, 300, 900]]))
    det = next(d for d in bench_detectors.DETECTORS if d.id == "many_deaths_laning")
    issue = det.run(ctx_for(static, setup, {"deaths_0_10": 145.0}, row=row))
    assert issue.timestamps_s == [100.0, 300.0]


# ---- late_core_items -------------------------------------------------------------

def _with_item(setup, t):
    return setup["row"].with_columns(pl.Series("it_id", [[setup["item_id"]]]), pl.Series("it_t", [[t]]))


def test_late_core_items(static, setup):
    det = item_detectors.DETECTORS[0]
    row = _with_item(setup, 900)  # the one item, bought 300 s after the higher-rank median (600 s)

    def run(third_item_s):
        return det.run(ctx_for(static, setup, {"third_big_item_s": third_item_s}, row=row))

    assert run(100.0) is None          # at the higher-rank median
    assert run(None) is None           # never bought 3 big items
    issue = run(145.0)
    assert issue.id == "late_core_items" and issue.category == "items"
    assert issue.evidence.player_value == 145 and issue.evidence.bracket_p50 == 100
    up = next_bracket_up(setup["bracket"], static)
    assert issue.evidence.details["benchmark_scope"].endswith(up.label)   # compared one bracket up
    assert issue.evidence.details["slowest_item"] == static.items[setup["item_id"]].name
    assert issue.evidence.details["slowest_item_higher_rank_p50_s"] == 600
    assert run(149.0).severity >= issue.severity


# ---- replay detectors --------------------------------------------------------------

OFFSET = 50.0  # server clock = match clock + OFFSET


def make_replay(static, setup, *, ally_dist, ult_ready_before_death_s, objective_dist, player_alive=True):
    hero = setup["hero"]
    others = [h for h in static.playable_heroes() if h.id != hero.id][:3]
    players = pl.DataFrame({"slot": [0, 1, 2, 3], "hero_id": [hero.id] + [h.id for h in others],
                            "team": [0, 0, 0, 1], "rank_badge": [55, 55, 55, 55]})
    death_ticks = [1000, 2000, 3000]
    kills = pl.DataFrame({"tick": death_ticks, "game_s": [t / 64 for t in death_ticks],
                          "victim_slot": [0, 0, 0], "attacker_slot": [3, 3, 3]})
    rows = []
    for t in death_ticks:
        s = t / 64
        for tick, dead in ((t - 1, False), (t, True)):
            rows.append({"tick": tick, "slot": 0, "x": 0.0, "y": 0.0, "z": 0.0, "is_alive": not dead,
                         "death_time": s + OFFSET if dead else 0.0, "has_ultimate_trained": True,
                         "ultimate_cooldown_start": s + OFFSET - ult_ready_before_death_s,
                         "ultimate_cooldown_end": s + OFFSET - ult_ready_before_death_s - 100})
            for slot in (1, 2):
                rows.append({"tick": tick, "slot": slot, "x": ally_dist, "y": 0.0, "z": 0.0, "is_alive": True,
                             "death_time": 0.0, "has_ultimate_trained": False,
                             "ultimate_cooldown_start": 0.0, "ultimate_cooldown_end": 0.0})
    obj_ticks = [4000, 5000, 6000, 7000]
    for t in obj_ticks:
        rows.append({"tick": t - 1, "slot": 0, "x": objective_dist, "y": 0.0, "z": 0.0, "is_alive": player_alive,
                     "death_time": 0.0, "has_ultimate_trained": True,
                     "ultimate_cooldown_start": 0.0, "ultimate_cooldown_end": 0.0})
    objectives = pl.DataFrame({"tick": obj_ticks, "game_s": [t / 64 for t in obj_ticks], "entity_id": [1, 2, 3, 4],
                               "health": [0, 0, 0, 0], "team": [1, 1, 1, 1],
                               "x": [0.0] * 4, "y": [0.0] * 4, "z": [0.0] * 4})
    ability_uses = pl.DataFrame({"slot": [0, 0, 1], "ability_class": [hero.abilities[0], hero.abilities[0], hero.abilities[1]]})
    return ReplayData(match_id=1, slot=0, team=0, players=players, kills=kills, ability_uses=ability_uses,
                      objectives=objectives, timeline=pl.DataFrame(rows))


def _replay_det(name):
    return next(d for d in replay_detectors.DETECTORS if d.id == name)


def test_died_far_from_team(static, setup):
    det = _replay_det("died_far_from_team")
    far = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=5000.0,
                                                             ult_ready_before_death_s=-60, objective_dist=0.0)))
    assert far.category == "positioning" and far.evidence.player_value == 3
    assert far.evidence.details["total_deaths"] == 3 and far.severity == 1.0
    assert len(far.timestamps_s) == 3
    near = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                              ult_ready_before_death_s=-60, objective_dist=0.0)))
    assert near is None


def test_died_with_ultimate_ready(static, setup):
    det = _replay_det("died_with_ultimate_ready")
    wasted = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                                ult_ready_before_death_s=30, objective_dist=0.0)))
    assert wasted.evidence.player_value == 3 and wasted.severity == pytest.approx(0.9)
    ult = static.item_by_class_name(setup["hero"].abilities[3])
    assert wasted.evidence.details["ultimate"] == ult.name
    # Ready only 15 s before death: below the 20 s grace -> not counted
    assert det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                              ult_ready_before_death_s=15, objective_dist=0.0))) is None
    # On cooldown at death (ready 60 s later) -> not counted
    assert det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                              ult_ready_before_death_s=-60, objective_dist=0.0))) is None
    # A single wasted-ult death is normal; it takes 2+ to trigger
    one = make_replay(static, setup, ally_dist=500.0, ult_ready_before_death_s=30, objective_dist=0.0)
    one.kills = one.kills.head(1)
    assert det.run(ctx_for(static, setup, replay=one)) is None


def test_low_objective_presence(static, setup):
    det = _replay_det("low_objective_presence")
    absent = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                                ult_ready_before_death_s=-60, objective_dist=8000.0)))
    assert absent.evidence.player_value == 0 and absent.evidence.details["enemy_objectives_destroyed"] == 4
    assert absent.severity == 1.0 and len(absent.timestamps_s) == 4
    present = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0,
                                                                 ult_ready_before_death_s=-60, objective_dist=100.0)))
    assert present is None
    dead = det.run(ctx_for(static, setup, replay=make_replay(static, setup, ally_dist=500.0, ult_ready_before_death_s=-60,
                                                              objective_dist=100.0, player_alive=False)))
    assert dead is not None  # nearby but dead does not count as present


def test_run_all_sorts_and_skips_replay_detectors_without_replay(static, setup):
    values = {m: (52.0 if METRICS[m].higher_is_better else 148.0) for m in METRICS}
    issues = run_all(ctx_for(static, setup, values))
    assert issues and [i.severity for i in issues] == sorted((i.severity for i in issues), reverse=True)
    assert not {i.id for i in issues} & {d.id for d in replay_detectors.DETECTORS}
