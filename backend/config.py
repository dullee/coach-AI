"""Paths and settings, read from environment variables with local-dev defaults."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> list[str]:
    """Read KEY=VALUE lines from the repo's .env (gitignored) into os.environ.

    Variables already set in the shell win. Blank lines and # comments are skipped; quotes around
    a value are removed. Returns the names that were set. COACH_NO_DOTENV=1 turns it off (tests).
    """
    if os.environ.get("COACH_NO_DOTENV") == "1":
        return []
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


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
