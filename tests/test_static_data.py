from __future__ import annotations

import json
import shutil
import time

import httpx
import pytest

from backend.ingest.cache import DiskCache
from backend.ingest.client import DeadlockApiClient
from backend.static_data import load, refresh, stored_versions
from tests.conftest import STATIC_FIXTURE


def test_counts_from_real_assets(static):
    assert static.client_version == 6796
    assert len(static.heroes) == 65
    # 43 playable heroes is what /v1/analytics/hero-stats reported in Phase 0.
    assert len(static.playable_heroes()) == 43
    assert len(static.ranks) == 12
    assert {i.type for i in static.items.values()} == {"upgrade", "ability", "weapon"}


def test_rank_names_come_from_data(static):
    assert static.rank_name(104) == f"{static.ranks[10]} 4"
    assert static.rank_name(0) == static.ranks[0]


def test_item_lookup_by_id_and_class_name(static):
    item = next(i for i in static.items.values() if i.type == "upgrade")
    assert static.item(item.id) is item
    assert static.item_by_class_name(item.class_name) is item
    assert static.item_by_class_name("no_such_thing") is None
    assert static.hero_name(999999).startswith("unknown")


def fake_api(tmp_path, versions):
    """Mock API serving the real trimmed assets for any client version."""
    calls = []
    assets = {n: json.loads((STATIC_FIXTURE / f"{n}.json").read_text(encoding="utf-8")) for n in ("heroes", "items", "ranks")}

    def handler(req):
        calls.append(req.url)
        if req.url.path.endswith("client-versions"):
            return httpx.Response(200, json=versions)
        return httpx.Response(200, json=assets[req.url.path.rsplit("/", 1)[-1]])

    client = DeadlockApiClient(base_url="https://api.test", cache=DiskCache(tmp_path / "cache"),
                               transport=httpx.MockTransport(handler), sleep=lambda s: None)
    return client, calls


def test_refresh_stores_newest_version(tmp_path):
    client, calls = fake_api(tmp_path, [6790, 6796, 6801])
    root = tmp_path / "static"
    path = refresh(client, root)
    assert path.name == "6801"
    assert stored_versions(root) == [6801]
    assert all(u.params.get("client_version") == "6801" for u in calls if "assets/" in u.path and "client-versions" not in u.path)
    sd = load(root=root)
    assert sd.client_version == 6801 and len(sd.playable_heroes()) == 43


def test_load_refreshes_when_stale(tmp_path):
    root = tmp_path / "static"
    shutil.copytree(STATIC_FIXTURE, root / "6796")  # meta.json has fetched_at=0: very stale
    client, calls = fake_api(tmp_path, [6796, 6802])
    assert load(root=root, client=client).client_version == 6802
    assert stored_versions(root) == [6796, 6802]
    # Fresh now: no further network calls.
    n = len(calls)
    load(root=root, client=client)
    assert len(calls) == n


def test_load_without_client_never_hits_network(tmp_path):
    root = tmp_path / "static"
    shutil.copytree(STATIC_FIXTURE, root / "6796")
    assert load(root=root).client_version == 6796
    assert load(6796, root=root).client_version == 6796
    with pytest.raises(FileNotFoundError):
        load(root=tmp_path / "empty")


def test_meta_records_fetch_time(tmp_path):
    client, _ = fake_api(tmp_path, [6796])
    path = refresh(client, tmp_path / "s")
    meta = json.loads((path / "meta.json").read_text())
    assert meta["client_version"] == 6796 and abs(meta["fetched_at"] - time.time()) < 60
