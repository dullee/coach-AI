"""A stored replay can stand in for API metadata (replay-only matches)."""

from __future__ import annotations

import httpx
import polars as pl
import pytest

from backend import db
from backend.analysis.frame import from_metadata
from backend.analysis.metrics import compute_all
from backend.analysis.replay_data import ReplayData
from backend.analysis.replay_meta import metadata_from_replay
from backend.ingest.cache import DiskCache
from backend.ingest.client import DeadlockApiClient, RateLimited
from backend.parse import parse_demo, save_match
from backend.summarize.sources import match_data
from tests.conftest import FIXTURES, fixture_metadata, require_replay

RANKED = next(f for f in FIXTURES if f["match_id"] == 120851860)


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    path = require_replay(RANKED)
    tmp = tmp_path_factory.mktemp("rm")
    engine = db.get_engine(f"sqlite:///{(tmp / 't.db').as_posix()}")
    save_match(parse_demo(path), engine, tmp)
    return engine, tmp


@pytest.mark.replay
def test_replay_metadata_matches_api_metrics(stored, static):
    engine, tmp = stored
    api = fixture_metadata(RANKED["match_id"])
    hero = api["players"][0]["hero_id"]
    rmeta = metadata_from_replay(ReplayData.load(RANKED["match_id"], hero, engine, tmp))
    assert rmeta["duration_s"] == api["duration_s"] and rmeta["winning_team"] == api["winning_team"]

    by_hero_api = {p["hero_id"]: i for i, p in enumerate(api["players"])}
    by_hero_rep = {p["hero_id"]: i for i, p in enumerate(rmeta["players"])}
    m_api, m_rep = compute_all(from_metadata(api), static), compute_all(from_metadata(rmeta), static)
    for h in by_hero_api:
        a, r = m_api.row(by_hero_api[h], named=True), m_rep.row(by_hero_rep[h], named=True)
        for metric in ("souls_per_min_0_10", "souls_per_min_10_20", "souls_per_min", "last_hits_0_10",
                       "damage_per_min", "kill_participation", "deaths_0_10", "deaths_per_10_min",
                       "objective_damage_per_min"):
            # The replay's last snapshot can miss events in the final seconds (a soul or two
            # of net worth; once in this match, the last kill), so allow a small difference there.
            if metric == "souls_per_min":
                assert r[metric] == pytest.approx(a[metric], rel=1e-3), (h, metric)
            elif metric == "kill_participation":
                assert r[metric] == pytest.approx(a[metric], abs=0.07), (h, metric)
            else:
                assert r[metric] == pytest.approx(a[metric], rel=1e-6), (h, metric)
        assert r["accuracy"] is None  # shots are not in the replay
        # Item times come from a different source (tick vs. API seconds): allow a few seconds.
        if a["first_big_item_s"] is not None:
            assert abs(r["first_big_item_s"] - a["first_big_item_s"]) <= 3, h


@pytest.mark.replay
def test_sources_fall_back_to_replay_when_api_lacks_the_match(stored, tmp_path):
    engine, data_dir = stored
    # Mark one player as the requester, as python -m backend.parse --account-id would.
    with engine.begin() as conn:
        conn.execute(db.match_players.update().where(db.match_players.c.slot == 0).values(is_requester=True, account_id=777))

    def api(req):
        if req.url.params.get("disable_steam") == "true":
            return httpx.Response(404, json={"status": 404, "error": "Match metadata not found."})
        raise AssertionError("must not use the Steam quota when a replay is stored")

    client = DeadlockApiClient(base_url="https://api.test", cache=DiskCache(tmp_path / "c"),
                               transport=httpx.MockTransport(api), sleep=lambda s: None)
    import backend.analysis.replay_data as rdmod
    orig = rdmod.ReplayData.load_for_account.__func__
    rdmod.ReplayData.load_for_account = classmethod(lambda cls, m, a, e, d=None: orig(cls, m, a, e, data_dir))
    try:
        meta, replay = match_data(client, RANKED["match_id"], 777, engine)
    finally:
        rdmod.ReplayData.load_for_account = classmethod(orig)
    assert meta["source"] == "replay" and replay is not None
    assert any(p["account_id"] == 777 for p in meta["players"])


def test_long_retry_after_fails_fast(tmp_path):
    sleeps = []
    client = DeadlockApiClient(
        base_url="https://api.test", cache=DiskCache(tmp_path / "c"), sleep=sleeps.append,
        transport=httpx.MockTransport(lambda req: httpx.Response(
            429, json={"status": 429, "error": {"type": "IP", "quota": {"limit": 3, "period": 3600}, "next_request_in": 3278}})),
    )
    with pytest.raises(RateLimited) as err:
        client.match_metadata(1, allow_steam=True)
    assert err.value.retry_after_s == 3278
    assert all(s < 1 for s in sleeps)  # only the tiny throttle, no long wait


def test_snapshot_table_exists():
    assert "stat_snapshots" in db.metadata.tables
    assert pl is not None
