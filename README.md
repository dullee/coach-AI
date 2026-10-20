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
| `python -m backend.analysis.benchmarks build [--files N]` | Build rank-bracket benchmarks from the community data lake (ranked matches since the last major patch). About 10–20 min; run once per patch. |
| `python -m backend.summarize --match-id N --account-id A` | Print the MatchSummary for a player (Free tier: API data only) |
| `python -m backend.summarize --match-id N --account-id A --demo FILE` | Same, Medium tier: parse the replay first and add replay-based issues |
| `python scripts/review_issues.py --account-id A` | Write a review sheet of detected issues for your last 10 matches to `data\review\` |
| `pytest` | Run the tests. Tests that need the fixture replays are skipped if they are missing. |
| `pytest -m network` | Run a few checks against the live community API |

`--account-id` marks the player the report is for. All other players are stored without names or IDs.

## Layout so far (Phases 1–2)

- `backend/ingest/`: community API client (disk cache, throttling, retries) and replay download
- `backend/static_data/`: hero, item, ability and rank data from the API, stored per game version
- `backend/parse/`: Boon wrapper that writes normalized tables to the database and the tick timeline to Parquet
- `backend/db.py`: database schema (SQLite locally; `COACH_DB_URL` can point to PostgreSQL)
- `backend/analysis/`: metrics (one file per family), rank-bracket benchmarks, and 14 rule-based issue detectors
- `backend/summarize/`: builds the MatchSummary JSON for the coaching model (data contracts in `contracts.py`)

Settings come from environment variables: `COACH_DATA_DIR`, `COACH_DB_URL`, `DEADLOCK_API_URL`.
