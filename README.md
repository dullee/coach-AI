# Deadlock post-match coach

Turns a Deadlock match into a short, prioritized list of things to fix.
The full plan is in [CLAUDE.md](CLAUDE.md), and the data findings are in [docs/phase0-findings.md](docs/phase0-findings.md).

## Try the prototype

Copy `.env.example` to `.env` and fill in one key (`.env` is gitignored and read automatically):
`GEMINI_API_KEY` (free: https://aistudio.google.com/apikey), `POLLINATIONS_API_KEY` (default model
openai/gpt-6-luna, runs on quest Pollen) or `ANTHROPIC_API_KEY` (Claude directly). Keys set in the
terminal (`$env:GEMINI_API_KEY = "..."`) still work and take priority.
```
.venv\Scripts\python -m backend.app      # opens http://127.0.0.1:8000
```

Enter your account ID (the number in your Steam friend code), click **Load my matches**, pick a match and
click **Get report**. Replays you downloaded in Deadlock (match history -> Download Replay) are found
automatically and add replay-based issues; you can also upload a `.dem`. **My progress** shows recurring issues and stat trends across your
last 10 matches; each report shows how you did on the previous report's focus, and you can rate
each priority (useful / not useful, optional comment). Reports also show your souls over time against the median of your
bracket, and a map of where you died (alone or with your team when a replay is available).
Open a report directly with `http://127.0.0.1:8000/?account=<id>&match=<match id>`. Gemini's free tier can take a
few minutes per report.

## Moving to another PC

Code is in git; the `data` folder is not (it holds replays that Valve no longer serves).

**On this PC**
```
git push                                                   # code
.venv\Scripts\python scripts\data_bundle.py pack           # -> coach-data.zip (~1.4 GB)
```
Upload `coach-data.zip` to cloud storage (Google Drive, OneDrive, ...). Add `--with-lake` to also
pack the data-lake extracts (+0.5 GB; only needed to rebuild benchmarks or training inputs offline).

**On the new PC** (Windows, Python 3.12)
```
git clone https://github.com/dullee/coach-AI.git
cd coach-AI
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts\data_bundle.py unpack <path to coach-data.zip>
.venv\Scripts\python -m pytest                            # should pass
.venv\Scripts\python -m backend.app
```
API keys are not in the bundle or the repo: copy `.env.example` to `.env` on the new PC and fill
them in again. Training runs on Kaggle, so it doesn't matter
which PC prepares the dataset.

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
| `python -m backend.coach <match_id> --account-id A [--demo FILE]` | Write a coaching report. `--backend claude` (default, needs `ANTHROPIC_API_KEY`; `--model claude-opus-5-5` for the larger model), `--backend gemini` (free testing, needs `GEMINI_API_KEY`), `--backend local` (OpenAI-compatible server) |
| `python scripts/phase3_check.py --account-id A [--backend gemini]` | Phase 3 check: 20 reports, validated, with total cost, written to `data\review\phase3_reports.md` |
| `python -m backend.app` | Prototype web app at http://127.0.0.1:8000 |
| `pytest` | Run the tests. Tests that need the fixture replays are skipped if they are missing. |
| `pytest -m network` | Run a few checks against the live community API |

`--account-id` marks the player the report is for. All other players are stored without names or IDs.

## Layout so far (Phases 1–3)

- `backend/ingest/`: community API client (disk cache, throttling, retries) and replay download
- `backend/static_data/`: hero, item, ability and rank data from the API, stored per game version
- `backend/parse/`: Boon wrapper that writes normalized tables to the database and the tick timeline to Parquet
- `backend/db.py`: database schema (SQLite locally; `COACH_DB_URL` can point to PostgreSQL)
- `backend/analysis/`: metrics (one file per family), rank-bracket benchmarks, and 14 rule-based issue detectors
- `backend/summarize/`: builds the MatchSummary JSON for the coaching model (data contracts in `contracts.py`)
- `backend/coach/`: coaching model interface (Claude API and local adapters), prompt, output validation (schema, issue ids, invented numbers), cost logging

Settings for the coach: `COACH_BACKEND` (claude, gemini, pollinations or local), `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `COACH_GEMINI_MODEL` (default `gemini-3.8-flash`), `POLLINATIONS_API_KEY`, `COACH_POLLINATIONS_MODEL` (default `openai/gpt-6-luna`), `COACH_CLAUDE_MODEL` (default `claude-haiku-5-5`), `COACH_LOCAL_URL` (default `http://localhost:11434/v1`), `COACH_LOCAL_MODEL`, `COACH_LOCAL_USD_PER_GPU_HOUR`.

Settings come from environment variables: `COACH_DATA_DIR`, `COACH_DB_URL`, `DEADLOCK_API_URL`.
