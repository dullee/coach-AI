"""Checks against the real community API. Off by default; run with:

    pytest -m network

Uses a temporary cache, so it really hits the network (a handful of requests).
"""

from __future__ import annotations

import pytest

from backend.ingest.cache import DiskCache
from backend.ingest.client import DeadlockApiClient
from backend.static_data import refresh, load

pytestmark = pytest.mark.network


@pytest.fixture
def client(tmp_path):
    with DeadlockApiClient(cache=DiskCache(tmp_path / "cache")) as c:
        yield c


def test_static_assets_still_have_expected_shape(client, tmp_path):
    refresh(client, tmp_path / "static")
    sd = load(root=tmp_path / "static")
    assert len(sd.playable_heroes()) >= 30
    assert len(sd.ranks) == 12
    assert any(i.type == "upgrade" and i.cost for i in sd.items.values())


def test_salts_endpoint_shape(client):
    s = client.match_salts(120851860)
    assert s.match_id == 120851860 and s.metadata_url
