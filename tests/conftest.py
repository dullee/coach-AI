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


@pytest.fixture(params=FIXTURES, ids=lambda e: str(e["match_id"]))
def fixture_replay(request) -> Path:
    return require_replay(request.param)


@pytest.fixture
def first_replay() -> Path:
    return require_replay(FIXTURES[0])
