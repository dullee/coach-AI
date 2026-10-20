from __future__ import annotations

import bz2

import httpx
import pytest
import zstandard

from backend import config
from backend.ingest.cache import DiskCache
from backend.ingest.client import DeadlockApiClient
from backend.ingest.replay import (
    DEMO_MAGIC, InvalidReplay, ReplayUnavailable, decompress_to, detect_format, fetch_replay,
)
from tests.conftest import FIXTURES
from tests.test_client import REAL_SALTS


def real_demo_head(n: int = 2 << 20) -> bytes:
    """First n bytes of a real fixture replay, or a minimal stand-in if missing."""
    path = config.demos_dir() / FIXTURES[0]["file"]
    if path.exists():
        with open(path, "rb") as f:
            return f.read(n)
    return DEMO_MAGIC + b"\x00" * 1024


def api_client(tmp_path, salts: dict) -> DeadlockApiClient:
    return DeadlockApiClient(
        base_url="https://api.test",
        cache=DiskCache(tmp_path / "cache"),
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=salts)),
        sleep=lambda s: None,
    )


def test_detect_format():
    assert detect_format(DEMO_MAGIC + b"x") == "dem"
    assert detect_format(zstandard.ZstdCompressor().compress(b"abc")[:8]) == "zstd"
    assert detect_format(bz2.compress(b"abc")[:8]) == "bz2"
    with pytest.raises(InvalidReplay):
        detect_format(b"<html>no")


@pytest.mark.parametrize("compress", [zstandard.ZstdCompressor().compress, bz2.compress, lambda b: b])
def test_decompress_round_trip(tmp_path, compress):
    raw = real_demo_head()
    src = tmp_path / "in.dem.bz2"
    src.write_bytes(compress(raw))
    dest = tmp_path / "out.dem"
    decompress_to(src, dest)
    assert dest.read_bytes() == raw


def test_decompress_rejects_non_demo(tmp_path):
    src = tmp_path / "in.bz2"
    src.write_bytes(zstandard.ZstdCompressor().compress(b"not a demo at all"))
    with pytest.raises(InvalidReplay):
        decompress_to(src, tmp_path / "out.dem")
    assert not list(tmp_path.glob("*.part"))


def test_fetch_replay_downloads_zstd_file(tmp_path):
    raw = real_demo_head(1 << 20)
    payload = zstandard.ZstdCompressor().compress(raw)  # what Valve actually serves
    requested = []

    def valve(req):
        requested.append(str(req.url))
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(valve)) as http:
        path = fetch_replay(120851860, client=api_client(tmp_path, REAL_SALTS), dest_dir=tmp_path / "demos", http=http)
    assert requested == [REAL_SALTS["demo_url"]]
    assert path.name == "120851860_329453757.dem"
    assert path.read_bytes() == raw
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]  # no temp files left


def test_fetch_replay_reuses_existing_file(tmp_path):
    demos = tmp_path / "demos"
    demos.mkdir()
    (demos / "120851860_329453757.dem").write_bytes(DEMO_MAGIC)

    def fail(req):
        raise AssertionError("should not hit the network")

    client = DeadlockApiClient(base_url="https://api.test", cache=DiskCache(tmp_path / "c"),
                               transport=httpx.MockTransport(fail))
    assert fetch_replay(120851860, client=client, dest_dir=demos).name == "120851860_329453757.dem"


def test_no_replay_salt_means_unavailable(tmp_path):
    # Real shape: most matches have replay_salt/demo_url null.
    salts = {**REAL_SALTS, "replay_salt": None, "demo_url": None}
    with pytest.raises(ReplayUnavailable, match="no replay link"):
        fetch_replay(120851860, client=api_client(tmp_path, salts), dest_dir=tmp_path)


def test_expired_replay_means_unavailable(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404))) as http:
        with pytest.raises(ReplayUnavailable, match="expired"):
            fetch_replay(120851860, client=api_client(tmp_path, REAL_SALTS), dest_dir=tmp_path, http=http)
    assert not list(tmp_path.glob("*.download"))
