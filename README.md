# Deadlock post-match coach

Turns a Deadlock match into a short, prioritized list of things to fix.
The full plan is in [CLAUDE.md](CLAUDE.md), and the data findings are in [docs/phase0-findings.md](docs/phase0-findings.md).

## Setup (Windows, Python 3.12)

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts\fetch_fixtures.py         # test replays -> data\demos\
.venv\Scripts\python -m backend.static_data refresh    # hero/item/rank data -> data\static\
```

Replays expire on Valve's servers after about 2–4 weeks. Keep a backup copy of `data\demos\`.

## Commands

| Command | What it does |
|---|---|
| `python -m backend.parse <file.dem> [--account-id N]` | Parse a replay and store it in `data\coach.db` |
| `python -m backend.parse --match-id N [--account-id N]` | Download the replay first (if the API has a link), then parse it |
| `python -m backend.static_data refresh` | Download the newest hero, item and rank data. Run this daily. |
| `python -m backend.static_data show` | Show which data versions are stored |
| `pytest` | Run the tests. Tests that need the fixture replays are skipped if they are missing. |
| `pytest -m network` | Run a few checks against the live community API |

`--account-id` marks the player the report is for. All other players are stored without names or IDs.

## Layout so far (Phase 1)

- `backend/ingest/`: community API client (disk cache, throttling, retries) and replay download
- `backend/static_data/`: hero, item, ability and rank data from the API, stored per game version
- `backend/parse/`: Boon wrapper that writes normalized tables to the database and the tick timeline to Parquet
- `backend/db.py`: database schema (SQLite locally; `COACH_DB_URL` can point to PostgreSQL)

Settings come from environment variables: `COACH_DATA_DIR`, `COACH_DB_URL`, `DEADLOCK_API_URL`.
