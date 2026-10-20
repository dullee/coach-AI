"""Paths and settings, read from environment variables with local-dev defaults."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Root for everything gitignored: cache, replays, parsed tables, static data."""
    return Path(os.environ.get("COACH_DATA_DIR", REPO_ROOT / "data"))


def db_url() -> str:
    return os.environ.get("COACH_DB_URL", f"sqlite:///{(data_dir() / 'coach.db').as_posix()}")


def api_base_url() -> str:
    return os.environ.get("DEADLOCK_API_URL", "https://api.deadlock-api.com")


def cache_dir() -> Path:
    return data_dir() / "cache" / "api"


def demos_dir() -> Path:
    return data_dir() / "demos"


def static_dir() -> Path:
    return data_dir() / "static"


def parsed_dir() -> Path:
    return data_dir() / "parsed"
