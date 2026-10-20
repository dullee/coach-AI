from __future__ import annotations

import json
import time

import httpx
import pytest

from backend.ingest.cache import DiskCache
from backend.ingest.client import ApiError, DeadlockApiClient, NotFound

# Real response from GET /v1/matches/120851860/salts (captured in Phase 0).
REAL_SALTS = {
    "match_id": 120851860, "cluster_id": 389, "metadata_salt": 2016681213, "replay_salt": 329453757,
    "metadata_url": "http://replay389.valve.net/1422450/120851860_2016681213.meta.bz2",
    "demo_url": "http://replay389.valve.net/1422450/120851860_329453757.dem.bz2",
}


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s

    def clock(self) -> float:
        return self.now


def make_client(tmp_path, handler, **kw):
    ft = FakeTime()
    client = DeadlockApiClient(
        base_url="https://api.test",
        cache=DiskCache(tmp_path / "cache"),
        transport=httpx.MockTransport(handler),
        sleep=ft.sleep,
        clock=ft.clock,
        **kw,
    )
    return client, ft


def test_cache_hit_avoids_second_request(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=[1, 2, 3])

    client, _ = make_client(tmp_path, handler)
    assert client.get_json("/v1/x", {"a": 1}, max_age_s=60) == [1, 2, 3]
    assert client.get_json("/v1/x", {"a": 1}, max_age_s=60) == [1, 2, 3]
    assert len(calls) == 1
    # Different params are a different cache entry.
    client.get_json("/v1/x", {"a": 2}, max_age_s=60)
    assert len(calls) == 2


def test_stale_cache_is_refetched(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"n": len(calls)})

    client, _ = make_client(tmp_path, handler)
    client.get_json("/v1/x", max_age_s=60)
    # Age the cache entry by two minutes.
    key = DiskCache.key("/v1/x", {})
    f = client.cache._file(key)
    entry = json.loads(f.read_text())
    entry["fetched_at"] = time.time() - 120
    f.write_text(json.dumps(entry))
    assert client.get_json("/v1/x", max_age_s=60) == {"n": 2}
    # max_age_s=None accepts any age.
    assert client.get_json("/v1/x", max_age_s=None) == {"n": 2}
    assert len(calls) == 2


def test_429_respects_retry_after_header(tmp_path):
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": 1})])
    client, ft = make_client(tmp_path, lambda req: next(responses))
    assert client.get_json("/v1/info", max_age_s=0) == {"ok": 1}
    assert 7.0 in ft.sleeps


def test_429_uses_next_request_in_from_body(tmp_path):
    # Shape of the API's real 429 body (seen during Phase 0 on /v1/sql).
    body = {"status": 429, "error": {"type": "IP", "quota": {"limit": 2, "period": 60}, "next_request_in": 11}}
    responses = iter([httpx.Response(429, json=body), httpx.Response(200, json=[])])
    client, ft = make_client(tmp_path, lambda req: next(responses))
    client.get_json("/v1/info", max_age_s=0)
    assert 11.0 in ft.sleeps


def test_503_backs_off_then_gives_up(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(503, text="busy")

    client, ft = make_client(tmp_path, handler, max_retries=3)
    with pytest.raises(ApiError) as err:
        client.get_json("/v1/info", max_age_s=0)
    assert err.value.status == 503
    assert len(calls) == 4  # first try + 3 retries
    backoffs = [s for s in ft.sleeps if s >= 1.0]
    assert backoffs == sorted(backoffs) and len(backoffs) == 3


def test_404_is_not_retried(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(404, json={"status": 404, "error": "Match metadata not found."})

    client, _ = make_client(tmp_path, handler)
    with pytest.raises(NotFound):
        client.match_metadata(1)
    assert len(calls) == 1


def test_network_error_is_retried(tmp_path):
    attempts = iter([httpx.ConnectError("boom"), None])

    def handler(req):
        exc = next(attempts)
        if exc:
            raise exc
        return httpx.Response(200, json=REAL_SALTS)

    client, _ = make_client(tmp_path, handler)
    assert client.match_salts(120851860).demo_url == REAL_SALTS["demo_url"]


def test_match_endpoints_never_ask_steam(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.url)
        if "salts" in req.url.path:
            return httpx.Response(200, json=REAL_SALTS)
        return httpx.Response(200, json={"match_info": {"match_id": 5, "players": []}})

    client, _ = make_client(tmp_path, handler)
    assert client.match_metadata(5) == {"match_id": 5, "players": []}  # match_info unwrapped
    client.match_salts(120851860)
    assert all(u.params.get("disable_steam") == "true" for u in seen)


def test_own_match_falls_back_to_steam_only_when_missing(tmp_path):
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        if req.url.params.get("disable_steam") == "true":
            return httpx.Response(404, json={"status": 404, "error": "Match metadata not found."})
        return httpx.Response(200, json={"match_info": {"match_id": 7, "players": []}})

    client, _ = make_client(tmp_path, handler)
    assert client.own_match_metadata(7)["match_id"] == 7
    assert seen == [{"disable_steam": "true"}, {}]


def test_throttle_spaces_analytics_calls(tmp_path):
    client, ft = make_client(tmp_path, lambda req: httpx.Response(200, json=[]))
    client.get_json("/v1/analytics/hero-stats", {"a": 1}, max_age_s=0)
    client.get_json("/v1/analytics/item-stats", {"a": 1}, max_age_s=0)
    assert ft.sleeps and abs(ft.sleeps[0] - 0.31) < 1e-6


def test_typed_models(tmp_path):
    entry = {
        "account_id": 1, "match_id": 2, "hero_id": 3, "hero_level": 20, "start_time": 1700000000,
        "game_mode": 1, "match_mode": 1, "player_team": 0, "player_kills": 4, "player_deaths": 7,
        "player_assists": 9, "denies": 10, "net_worth": 30000, "last_hits": 150, "match_duration_s": 2100,
        "match_result": 0, "objectives_mask_team0": 0, "objectives_mask_team1": 0, "player_match_outcome": 1,
    }

    def handler(req):
        if req.url.path.endswith("match-history"):
            return httpx.Response(200, json=[entry])
        return httpx.Response(200, json={"badge": 104, "rank": 10, "subrank": 4, "last_match": None})

    client, _ = make_client(tmp_path, handler)
    [m] = client.match_history(1)
    assert (m.match_id, m.player_deaths, m.ranked_display_badge) == (2, 7, None)
    assert client.player_rank(1).badge == 104
