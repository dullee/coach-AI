"""Parser tests on the real fixture replays (skipped if data/demos/ is empty)."""

from __future__ import annotations

import subprocess
import sys
import time

import polars as pl
import pytest
from boon import Demo
from sqlalchemy import func, select

from backend import config, db
from backend.parse import ParseError, load_timeline, parse_demo, save_match
from backend.parse.demo import steam64_to_account
from tests.conftest import FIXTURES, require_replay

pytestmark = pytest.mark.replay

PRIVATE_COLUMNS = {"steam_id", "player_name", "text", "chat_type"}


@pytest.fixture(scope="module", params=FIXTURES, ids=lambda e: str(e["match_id"]))
def parsed(request, tmp_path_factory):
    """Parse + store each fixture once; record timing for the 30 s target."""
    path = require_replay(request.param)
    # The requester is whoever is in slot 0 of the demo; read it at test time
    # so no real account ID is written into the repo.
    requester = steam64_to_account(Demo(str(path)).players["steam_id"][0])
    data_dir = tmp_path_factory.mktemp("data")
    engine = db.get_engine(f"sqlite:///{(data_dir / 'test.db').as_posix()}")
    start = time.perf_counter()
    pm = parse_demo(path, requester_account_id=requester)
    save_match(pm, engine, data_dir)
    elapsed = time.perf_counter() - start
    return {"pm": pm, "engine": engine, "data_dir": data_dir, "requester": requester,
            "elapsed": elapsed, "entry": request.param}


def test_parse_and_store_under_30s(parsed):
    assert parsed["elapsed"] < 30, f"took {parsed['elapsed']:.1f}s"


def test_match_info(parsed):
    m = parsed["pm"].match
    assert m["match_id"] == parsed["entry"]["match_id"]
    assert m["tick_rate"] == 64
    assert m["winning_team"] in (0, 1)
    assert m["build"] > 0 and m["boon_version"]
    assert 600 < m["duration_s"] < 4 * 3600


def test_players_and_slots(parsed):
    p = parsed["pm"].players
    assert p["slot"].to_list() == list(range(12))
    assert p["hero_id"].n_unique() == 12
    assert sorted(p["team"].to_list()) == [0] * 6 + [1] * 6


def test_only_requester_has_an_account_id(parsed):
    p = parsed["pm"].players
    req = p.filter(pl.col("is_requester"))
    assert req.height == 1 and req["account_id"][0] == parsed["requester"]
    assert p.filter(~pl.col("is_requester"))["account_id"].null_count() == 11


def test_no_names_steam_ids_or_chat_anywhere(parsed):
    pm = parsed["pm"]
    for name in (*pm.tables, "timeline"):
        cols = set(getattr(pm, name).columns)
        assert not cols & PRIVATE_COLUMNS, f"{name} has {cols & PRIVATE_COLUMNS}"
    assert not hasattr(pm, "chat")
    with parsed["engine"].connect() as conn:
        tables = set(db.metadata.tables)
        assert "chat" not in tables
        assert conn.execute(select(func.count()).select_from(db.match_players)
                            .where(db.match_players.c.account_id.is_not(None))).scalar_one() == 1


def test_ids_join_to_static_data(parsed, static):
    pm = parsed["pm"]
    for hero_id in pm.players["hero_id"]:
        assert static.hero(hero_id).playable
    bought = set(pm.purchases["item_id"].to_list())
    assert bought and bought <= static.items.keys()
    assert set(pm.ability_upgrades["ability_id"].to_list()) <= static.items.keys()
    unknown = {c for c in pm.ability_uses["ability_class"].unique() if static.item_by_class_name(c) is None}
    assert not unknown


def test_events_map_to_slots_and_clock(parsed):
    pm = parsed["pm"]
    duration = pm.match["duration_s"]
    assert pm.kills["victim_slot"].null_count() == 0
    assert pm.kills["game_s"].is_between(-120, duration + 120).all()
    assert pm.purchases["slot"].null_count() == 0
    assert pm.ability_uses["slot"].null_count() == 0
    # Damage between two heroes must map both sides to slots.
    hero_vs_hero = pm.damage.filter(pl.col("attacker_slot").is_not_null() & pl.col("victim_slot").is_not_null())
    assert hero_vs_hero.height > 1000


def test_timeline_is_64hz_per_player(parsed):
    tl = parsed["pm"].timeline
    assert tl["slot"].null_count() == 0 and tl["slot"].n_unique() == 12
    one = tl.filter(pl.col("slot") == 0).sort("tick")
    steps = one["tick"].diff().drop_nulls()
    assert (steps == 1).mean() > 0.95  # gaps only while dead or missing from the demo
    assert one["game_s"].is_sorted()
    assert {"pitch", "yaw", "x", "y", "z", "souls", "health"} <= set(tl.columns)


def test_store_round_trip(parsed):
    pm, engine = parsed["pm"], parsed["engine"]
    mid = pm.match["match_id"]
    with engine.connect() as conn:
        for name, table in db.EVENT_TABLES.items():
            n = conn.execute(select(func.count()).select_from(table).where(table.c.match_id == mid)).scalar_one()
            assert n == getattr(pm, name).height, name
    tl = load_timeline(mid, engine, parsed["data_dir"])
    assert tl.height == pm.timeline.height


def test_saving_twice_replaces(parsed):
    pm, engine = parsed["pm"], parsed["engine"]
    save_match(pm, engine, parsed["data_dir"])
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.kills)).scalar_one() == pm.kills.height
        assert conn.execute(select(func.count()).select_from(db.matches)).scalar_one() == 1


def test_wrong_requester_is_rejected(first_replay):
    with pytest.raises(ParseError, match="did not play"):
        parse_demo(first_replay, requester_account_id=1)


def test_cli_stores_match(first_replay, tmp_path):
    env = {**__import__("os").environ, "COACH_DATA_DIR": str(tmp_path), "BOON_DATA_DIR": str(config.data_dir() / "boon")}
    out = subprocess.run([sys.executable, "-m", "backend.parse", str(first_replay)],
                         capture_output=True, text=True, env=env, cwd=config.REPO_ROOT, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "stored match" in out.stdout
    assert (tmp_path / "coach.db").exists()
    assert list((tmp_path / "parsed").glob("*/timeline.parquet"))
