from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import config
from backend.static_data import StaticData

TESTS = Path(__file__).resolve().parent
FIXTURES = json.loads((TESTS / "fixtures.json").read_text(encoding="utf-8"))["replays"]
STATIC_FIXTURE = TESTS / "data" / "static" / "6796"


def replay_path(entry: dict) -> Path:
    return config.demos_dir() / entry["file"]


def require_replay(entry: dict) -> Path:
    path = replay_path(entry)
    if not path.exists():
        pytest.skip(f"fixture replay {entry['file']} missing; run: python scripts/fetch_fixtures.py")
    return path


@pytest.fixture(scope="session")
def static() -> StaticData:
    """Trimmed copy of real API assets (client version 6796)."""
    return StaticData.load_dir(STATIC_FIXTURE)


API_FIXTURES = TESTS / "data" / "api"
LAKE_SAMPLE = TESTS / "data" / "lake_sample.parquet"


def fixture_metadata(match_id: int) -> dict:
    """Real API metadata for a fixture match, trimmed, with fake account IDs 1001-1012."""
    return json.loads((API_FIXTURES / f"metadata_{match_id}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def lake_frame(static):
    """~830 real ranked matches from the data lake, as a player-match frame with brackets."""
    import polars as pl

    from backend.analysis.benchmarks.build import add_bracket
    from backend.analysis.frame import from_lake

    return add_bracket(from_lake(pl.read_parquet(LAKE_SAMPLE)), static)


@pytest.fixture(scope="session")
def sample_benchmarks(static, lake_frame):
    """Benchmarks built from the lake sample with the real build code."""
    from backend.analysis.benchmarks import Benchmarks
    from backend.analysis.benchmarks.build import compute_item_benchmarks, compute_metric_benchmarks

    return Benchmarks(compute_metric_benchmarks(lake_frame, static), compute_item_benchmarks(lake_frame, static),
                      {"patch_start": "2026-09-29T20:25:11Z", "files": []})


@pytest.fixture(params=FIXTURES, ids=lambda e: str(e["match_id"]))
def fixture_replay(request) -> Path:
    return require_replay(request.param)


@pytest.fixture
def first_replay() -> Path:
    return require_replay(FIXTURES[0])
