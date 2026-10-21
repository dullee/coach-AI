"""Death replays: frames, events and facts from a small synthetic stored match."""

from __future__ import annotations

import math

import polars as pl
import pytest

from backend import db
from backend.analysis.death_replay import FPS, WINDOW_AFTER_S, WINDOW_BEFORE_S, death_replays

MATCH, ME = 1234, 42
DEATH_S = 100.0
ITEM_ID = 2521299219          # an item in the static fixture (tests/data/static/6796)


def _store(tmp_path, static):
    heroes = sorted(static.heroes)[:4]
    engine = db.get_engine(f"sqlite:///{(tmp_path / 'coach.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(db.matches.insert(), [{"match_id": MATCH, "build": 1, "tick_rate": 64,
                                            "timeline_path": f"parsed/{MATCH}/timeline.parquet"}])
        conn.execute(db.match_players.insert(), [
            {"match_id": MATCH, "slot": 0, "hero_id": heroes[0], "team": 0, "is_requester": True, "account_id": ME},
            {"match_id": MATCH, "slot": 1, "hero_id": heroes[1], "team": 0, "is_requester": False, "account_id": None},   # teammate
            {"match_id": MATCH, "slot": 2, "hero_id": heroes[2], "team": 1, "is_requester": False, "account_id": None},   # killer
            {"match_id": MATCH, "slot": 3, "hero_id": heroes[3], "team": 1, "is_requester": False, "account_id": None},   # far enemy
        ])
        conn.execute(db.kills.insert(), [{"match_id": MATCH, "tick": 6400, "game_s": DEATH_S,
                                          "victim_slot": 0, "attacker_slot": 2, "assister_slots": []}])
        hit = lambda t, a, v, dmg: {"match_id": MATCH, "tick": int(t * 64), "game_s": t, "attacker_slot": a,  # noqa: E731
                                    "victim_slot": v, "damage": dmg, "ability_id": ITEM_ID}
        conn.execute(db.damage.insert(), [
            hit(80.0, 2, 0, 500),     # before the window: left out
            hit(95.0, 2, 0, 300), hit(99.5, 2, 0, 200), hit(98.0, None, 0, 50),   # taken (one from a trooper)
            hit(97.0, 0, 2, 120), hit(97.5, 2, 0, 0),                             # dealt; a 0-damage row is dropped
        ])
    # 64 Hz timeline, 80-105 s. Me at the origin, losing health 85-100 s, dead from 100 s.
    rows = []
    for tick in range(80 * 64, 105 * 64 + 1):
        t = tick / 64
        hp = max(0.0, min(1.0, (DEATH_S - t) / 15))
        for slot, x, y in ((0, 0.0, 0.0), (1, 2000.0, 0.0), (2, 0.0, 1000.0), (3, 9000.0, 9000.0)):
            alive = not (slot == 0 and t >= DEATH_S)
            rows.append({"slot": slot, "game_s": t, "tick": tick, "x": x + (t - 80) * 10 * (slot == 2), "y": y,
                         "yaw": 90.0, "health": round(1000 * hp) if slot == 0 else 1000, "max_health": 1000,
                         "is_alive": alive})
    path = tmp_path / "parsed" / str(MATCH) / "timeline.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame(rows).with_columns(pl.col("slot").cast(pl.Int8), pl.col("x").cast(pl.Float32),
                                    pl.col("y").cast(pl.Float32), pl.col("yaw").cast(pl.Float32)).write_parquet(path)
    return engine


@pytest.fixture
def replay(tmp_path, static):
    return death_replays(MATCH, ME, _store(tmp_path, static), static, data_dir=tmp_path)


def test_frames_cover_the_window(replay, static):
    d = replay["deaths"][0]
    n = int((WINDOW_BEFORE_S + WINDOW_AFTER_S) * FPS) + 1
    me = next(t for t in d["tracks"] if t["slot"] == 0)
    assert d["t"] == DEATH_S and d["killer"] == 2
    assert all(len(t["x"]) == n for t in d["tracks"])
    assert me["hp"][0] == 100 and me["hp"][-1] is None             # full at -15 s, dead at +2 s
    assert me["yaw"][0] == 90 and all("yaw" not in t for t in d["tracks"] if t["slot"] != 0)
    roles = {p["slot"]: p["role"] for p in replay["players"]}
    assert roles == {0: "you", 1: "ally", 2: "enemy", 3: "enemy"}


def test_events_in_window_only(replay):
    d = replay["deaths"][0]
    assert [(h["t"], h["from"], h["dmg"]) for h in d["taken"]] == [(-5.0, 2, 300), (-2.0, None, 50), (-0.5, 2, 200)]
    assert [(h["t"], h["to"], h["dmg"]) for h in d["dealt"]] == [(-3.0, 2, 120)]
    assert d["taken"][0]["what"] == "Soul Urn"                      # name from static data
    assert d["kills"] == [{"t": 0.0, "victim": 0, "killer": 2}]


def test_facts(replay):
    d = replay["deaths"][0]
    f = d["facts"]
    assert f["damage_taken"] == 550 and f["damage_dealt"] == 120
    assert f["damage_by_source"] == [{"from": 2, "dmg": 500}, {"from": None, "dmg": 50}]
    assert f["nearest_ally"] == 2000 and f["alone"] is False
    assert f["enemies_near"] == 1                                   # the far enemy is not counted
    assert f["below_half_hp_s"] == pytest.approx(7.5, abs=0.2)     # half health at 92.5 s
    scene = {t["slot"]: (t["in_scene"], t["involved"]) for t in d["tracks"]}
    assert scene == {0: (True, True), 1: (True, False), 2: (True, True), 3: (False, False)}


def test_unknown_account_raises(tmp_path, static):
    with pytest.raises(LookupError):
        death_replays(MATCH, 999, _store(tmp_path, static), static, data_dir=tmp_path)


def test_killer_moves_between_frames(replay):
    killer = next(t for t in replay["deaths"][0]["tracks"] if t["slot"] == 2)
    # moves 10 units per second along x: one frame apart = 10 / FPS units
    assert math.isclose(killer["x"][FPS] - killer["x"][0], 10, abs_tol=1)
