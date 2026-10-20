Project overview
We are building a post-match AI coach for Valve's Deadlock that turns match data into a short, prioritized list of weaknesses and how to fix them. Code does the analysis; a fine-tuned small language model writes the coaching. This keeps the cost per analysis low.
Tier
Input
What the player gets
Free
Steam ID / account ID → community API match stats
Hero win rates, item timing vs. higher-rank players, souls per minute, deaths with death/killer positions (from API death_details), a 3-point coaching summary
Medium
Replay (.dem) fetched automatically by match ID when the API has a replay link (~2 weeks), otherwise uploaded; parsed with Boon
Everything in Free, plus positioning over time and around deaths, aim/awareness from camera angles, lane farming, ability usage, objective participation, fight timing
Premium
Replay + the player's own video recording
Everything in Medium, plus short clips around each death, reviewed by a vision model for decisions and mechanics
Success criteria for the project:
• A player can get a coaching report for a real match in under 2 minutes (Free and Medium).
• The fine-tuned model is rated at least as useful as the untuned base model, and close to a large cloud model, in a blind test (see Evaluation plan).
• Cost per Medium analysis is measured and reported. Target: under $0.01 when using the fine-tuned model.
• The system keeps working after a game patch without code changes to hero or item knowledge.
Constraints and non-goals
Hard constraints:
• Post-match only. No advice, overlays or information shown to the player during a match. The app may record during a match, but it analyzes after.
• No game memory reading, no injection, no automation. Data comes only from the community API (deadlock-api.com), replay files, and the player's own video.
• Patch-proof. Hero, item and ability names and stats are never hard-coded. They are loaded from the community API's static assets and refreshed on a schedule.
• Cost-aware. The LLM never receives raw replays, raw tick data or full videos. It receives a compact JSON summary (target: under 2,000 tokens per request).
• Respect rate limits of the community API. Cache every response.
• Privacy. Only analyze matches for accounts the user has entered themselves. Uploaded videos are deleted after analysis unless the user chooses to keep them. From a replay, store full detail only for the requesting player; the other 11 players are stored as anonymous rows (hero, team, positions, damage) with no names, Steam IDs or chat. Never use the data lake's steam_profiles tables.
Non-goals for this project:
• Live in-match coaching or overlays.
• Support for other games.
• Real payments. Tiers are a feature flag on the user account, not a billing system.
• A mobile app.
Architecture and tech stack
The system is a pipeline: ingest → parse → analyze with code → summarize to JSON → coaching model → report. Each stage is a separate Python module with its own tests, so stages can be built and swapped independently.
Every tier feeds the same analysis engine. Only the compact MatchSummary reaches the coaching model, and player ratings flow back into training data.
Layer
Choice
Notes
Backend API
Python 3.12, FastAPI
One language for parsing, analysis and ML
Replay parsing
Boon (boon-deadlock)
Returns Polars DataFrames: kills, damage, player ticks, items, abilities, objectives
Data processing
Polars
Matches Boon's output format
Match and static data
deadlock-api.com
Match history, player stats, hero/item assets, data lake (data.deadlock-api.com) for benchmarks and training. No API key (keys require a paid Patreon membership); stay within the free per-IP limits listed in docs/phase0-findings.md
Database
PostgreSQL (SQLite allowed for local dev)
Users, matches, metrics, reports, ratings. Exception: the per-tick player timeline (~1.2-1.5M rows/match) is a Parquet file in data/parsed/<match_id>/, referenced from matches.timeline_path
Job queue
Background worker (RQ or Celery with Redis)
Replay parsing and video jobs take longer than a web request
Coaching model (default)
Fine-tuned 7–8B open instruct model (default pick: Qwen3-8B, Apache 2.0), served with Ollama on the developer's RTX 4060 8 GB at 4-bit (vLLM does not run natively on Windows)
Built in Phase 4
Coaching model (baseline / premium)
Claude API
Small model for baseline, larger model for premium deep review
Fine-tuning
Unsloth + QLoRA on Kaggle (~30 GPU-hours/week); local RTX 4060 8 GB as a backup for short runs
Hugging Face datasets format
Video
ffmpeg for clipping and frame sampling; a multimodal model for review
Premium tier only
Frontend
Next.js (React, TypeScript)
Upload page, report page, history, rating buttons
Repository structure
One monorepo, with the backend split by pipeline stage and the ML work kept separate from the app.
deadlock-coach/
├── CLAUDE.md                 # this plan
├── backend/
│   ├── app/                  # FastAPI routes, auth, tier gating
│   ├── ingest/               # community API client + caching
│   ├── parse/                # Boon wrapper → normalized tables
│   ├── static_data/          # hero/item/ability loader, refreshed per patch
│   ├── analysis/
│   │   ├── metrics/          # one file per metric family
│   │   ├── issues/           # rule-based mistake detectors
│   │   └── benchmarks/       # rank-bracket percentiles from dumps
│   ├── summarize/            # builds MatchSummary JSON
│   ├── coach/                # model clients, prompts, RAG, output validation
│   ├── video/                # clip extraction + vision review (premium)
│   └── worker/               # background jobs
├── ml/
│   ├── dataset/              # build training pairs from matches
│   ├── review_tool/          # small UI for team members to edit/rate targets
│   ├── train/                # QLoRA notebooks/scripts
│   └── eval/                 # blind comparison harness + metrics
├── frontend/                 # Next.js app
├── data/                     # gitignored: dumps, demos, cache
└── tests/                    # fixtures.json lists 3+ real match IDs; scripts/fetch_fixtures.py downloads the .dem files into data/demos/ (too big for git, ~300 MB each)
Data contracts
Three JSON shapes connect the stages. They are Pydantic models in backend/summarize/contracts.py (Issue, Strength, MatchSummary) and backend/coach/contracts.py (CoachingReport); every other module codes against them.
Issue — one detected mistake, produced by a rule in analysis/issues/:
{
  "id": "low_souls_per_min_laning",
  "category": "farming",   // farming | survival | combat | teamplay | objectives | mechanics | items | positioning | abilities
  "severity": 0.8,         // 0-1
  "evidence": {"player_value": 412, "bracket_p50": 560, "percentile": 0.12, "unit": "souls/min", "window": "0-10 min",
               "details": {"benchmark_scope": "hero / t05-06", "benchmark_n": 1840}},   // percentile: 1 = best; details: extra quotable facts
  "timestamps_s": [],
  "hero": "<loaded from static data>"
}
MatchSummary — what the coaching model receives (target under 2,000 tokens):
{
  "match_id": 0,
  "patch": "2026-09-29 (client 6796)",
  "analysis_level": "free",   // free = API data only; medium = API data + replay
  "player": {"hero": "...", "rank_bracket": "<bracket compared with>", "bracket_basis": "own_rank",   // own_rank | estimated_from_stats | all_ranks
             "estimated_level": null,   // unranked only: bracket this match's stats match, e.g. "below Initiate-Seeker"
             "result": "loss", "duration_s": 2100},
  "key_stats": {"kda": [4, 7, 9], "souls_per_min": 498, "damage_per_min": 0, "last_hits": 120,
                "kill_participation": 0.5, "ability_casts": {"<ability name>": 12}},   // ability_casts: medium only
  "issues": ["<up to 8 Issue objects, sorted by severity>"],
  "strengths": [{"id": "strong_kill_participation", "label": "...", "evidence": {"...": "same shape as Issue evidence"}}],   // up to 3
  "hero_context": "<short snippet from static data: the hero's abilities and the items held at the end>"
}
CoachingReport — what the model must return (validated; retry once on invalid JSON):
{
  "headline": "one sentence",
  "priorities": [
    {"title": "...", "why": "...", "how_to_fix": "...", "drill": "...", "issue_ids": ["..."]}
  ],
  "strengths": ["..."],
  "next_match_focus": "one concrete thing"
}
Rule: every claim in a CoachingReport must trace back to an issue_id or a key stat. The model explains; it never invents numbers.
Phases 0–3: data to first coaching report
By the end of Phase 3, a command-line run on a real replay produces a valid CoachingReport using a cloud model. No frontend yet.
Phase 0 — Data spike (no app code)
[x] Install Boon, parse 3+ real replays, and list every dataset and column it returns (Demo.available_datasets()).
[x] Confirm whether player_ticks includes view angles / aim direction, and the tick rate.
[x] Find how players obtain their own replay files (Boon docs cover this).
[x] Explore the community API docs: match, player, hero-stats and asset endpoints; rate limits; database dump format and size.
[x] Write docs/phase0-findings.md answering every item in Open questions.
Done when: findings doc exists and the team has updated this plan where the findings disagree with it.
Phase 1 — Ingestion and parsing
[x] ingest/: typed API client with on-disk caching, retries and backoff.
[x] static_data/: load heroes, items and abilities from API assets; store by patch; scheduled refresh.
[x] parse/: wrap Boon (pinned version); load only the core datasets; output normalized tables (player timeline, kills, damage, purchases, abilities, objectives) keyed by match and player; store the game build with each match. Join Boon IDs to static_data/ (never Boon's own name helpers); set BOON_DATA_DIR inside data/.
[x] ingest/: replay fetch by match ID (salts endpoint → download → zstd decompress, despite the .bz2 name), as a background-friendly function.
[x] Store parsed matches in the database.
Done when: python -m backend.parse <file.dem> stores a match in under 30 s, and tests pass on the fixture replays.
Status (2026-10-20): met and confirmed by the developer. 14-18 s per match end to end; 58 tests pass (pytest), including 32 on the 3 fixture replays.
Phase 2 — Analysis engine
[x] metrics/: souls per minute by phase, damage per minute, deaths by phase, item timings, ability usage counts, objective participation, kill participation.
[x] benchmarks/: compute percentiles per hero and rank bracket with filtered DuckDB queries against the remote data lake (match_player is 552 GB; never download it whole). Ranked matches only. 5 brackets of two tiers each (the top tier merged into the bracket below); filter to matches since the last major patch. Details: docs/phase2-notes.md.
[x] issues/: at least 10 rule-based detectors, each with an id, category, severity formula and evidence. Examples: low laning farm vs. bracket, late core item, deaths while far from teammates, low objective participation, unused ultimate in fights.
[x] summarize/: build a MatchSummary under 2,000 tokens.
Done when: every detector has a unit test, and three team members agree that detected issues on 10 real matches are reasonable.
Status (2026-10-20): 14 detectors, all unit-tested (99 tests pass; 2 live-API tests run separately). Human review of 10 real matches still to do (scripts/review_issues.py); how to do it solo is not decided yet.
Phase 3 — Coaching LLM layer (cloud baseline)
[ ] coach/: one interface, CoachModel.generate(summary) -> CoachingReport, with adapters for the Claude API and a local OpenAI-compatible server (vLLM/Ollama).
[ ] System prompt with coaching rules; use prompt caching for the static part.
[ ] Validate output against the schema; retry once; log tokens and cost per call.
[ ] CLI: python -m backend.coach <match_id> prints the report.
Done when: 20 real matches produce valid reports with no invented numbers (spot-checked), and cost per call is logged.
Phases 4–6: fine-tuning, app and premium video
Phase 4 is the project's required fine-tuning component. Phases 4 and 5 can run in parallel once Phase 3 is done.
Phase 4 — Fine-tune the coaching model
Goal: a small open model that writes CoachingReports close to a large model's quality at a fraction of the cost. Fine-tune for coaching style and reasoning, not game facts (facts arrive in hero_context).
[ ] ml/dataset/: build 2,000–3,000 MatchSummaries from dumps and parsed replays, spread across heroes, ranks and win/loss.
[ ] Generate draft CoachingReports for each with the large cloud model (teacher).
[ ] ml/review_tool/: a simple web page where team members see a summary + draft, then accept, edit or reject it. Target: 500+ human-reviewed examples; record who reviewed what.
[ ] Split by match: 80% train, 10% validation, 10% test. The test set is never used for training or prompt tuning.
[ ] ml/train/: QLoRA with Unsloth on a 7–8B instruct model. Log hyperparameters, loss curves and the base model version.
[ ] Export the adapter and serve it locally (vLLM or Ollama) behind the Phase 3 interface.
[ ] Run the Evaluation plan.
Done when: the fine-tuned model passes schema validation on 98%+ of test summaries and the evaluation results are written up.
Phase 5 — App and tiers
[ ] Accounts with a tier field (free / medium / premium); tier set manually for the demo.
[ ] Free: enter Steam ID → pick a recent match → report from API data only.
[ ] Medium: fetch the replay by match ID or upload a .dem → background job → report with replay-based issues.
[ ] Report page: headline, priorities, evidence numbers, strengths, "next match focus".
[ ] Thumbs up/down and an optional comment on each priority, stored for future training.
[ ] History page: issues trending across the player's last 10 matches.
[ ] Per-user daily limits; cost dashboard for the team.
Done when: a new user can sign up, upload a replay and read a report end to end.
Phase 6 — Premium video review
[ ] Accept a video upload plus its replay; ask the player to align one event (e.g. their first death) to sync the timelines.
[ ] Use replay timestamps of deaths and flagged issues to cut 5–10 s clips with ffmpeg.
[ ] Sample 4–6 frames per clip; send frames + that moment's replay context to a multimodal model.
[ ] Merge clip findings into the CoachingReport as an extra section; show each clip next to its comment.
[ ] Delete source video after processing by default.
Done when: a 30-minute match video yields a reviewed clip list in under 10 minutes, with cost per review logged.
Evaluation plan
The fine-tuned model is judged against two baselines on the held-out test set: the same base model without fine-tuning, and the large cloud teacher model. Build the harness in ml/eval/ so it reruns with one command.
Measure
How
Who / what scores it
Schema validity
% of outputs that parse as CoachingReport
Automatic
Grounding
% of numbers and claims that trace to an issue_id or key stat
Automatic check + spot review
Usefulness
1–5 rating per report, models shown in random order with labels hidden
3+ team members who play Deadlock
Specificity
1–5: is the advice concrete and actionable for this match?
Same raters
Preference
Pairwise: which of two reports would you rather receive?
Same raters, 50+ pairs
Cost per report
Tokens × price, or GPU-seconds for the local model
Automatic
Latency
Seconds per report
Automatic
Report the results as a table plus 3 side-by-side example reports. Also report inter-rater agreement so readers can trust the human scores.
Team split and timeline
Current setup: one developer building a prototype, holding all four roles below. Steps that need several people (Phase 2 issue review, Phase 4 human review, evaluation raters) are still to be adjusted for a solo developer.
The original plan assumed four people and about 10 weeks:
Role
Owns
Main phases
Data engineer
ingest/, parse/, static_data/, database
0, 1
Analysis lead
metrics/, issues/, benchmarks/, summarize/
2
ML lead
coach/, ml/ (dataset, review tool, training, evaluation)
3, 4
App lead
frontend/, app/, worker/, video/
5, 6
Everyone reviews training examples in Phase 4 and rates reports in the evaluation.
Rules for the AI developer
1. Work on one phase at a time. Do not start the next phase until the current phase's "Done when" is met and a human has confirmed it.
2. Verify before you assume. If a library field, API endpoint or file format is not confirmed in docs/phase0-findings.md, check it in the code or docs first; never invent it.
3. Never hard-code hero, item or ability names or numbers. Always read them from static_data/.
4. Code against the data contracts. If a contract must change, update the Pydantic model, this plan and the tests in the same change.
5. Write tests with every module, using the real replay fixtures listed in tests/fixtures.json (downloaded into data/demos/ by the fetch script; tests skip with a clear message if the files are missing).
6. Never send raw replays, tick tables or full videos to an LLM. Log token counts and cost for every model call.
7. Keep secrets (API keys) in environment variables; never commit them. data/ stays gitignored.
8. Keep changes small and explained: one feature per commit, with a short note on what changed and how it was tested.
9. When something in this plan is ambiguous or conflicts with what you find, stop and ask instead of guessing.
Open questions (answer in Phase 0)
All answered in docs/phase0-findings.md (section 2). Short answers: view angles yes (64 Hz); replays downloadable ~18 days; API has free per-IP limits, and the API team gave permission to use the data, including for training; badge = tier×10+subrank, enough data except Eternus; no Valve policy found; Qwen3-8B; Kaggle ~30 h/week + local RTX 4060 8 GB.
[x] Does Boon's player_ticks include view angles? If not, aim and crosshair analysis moves to the video tier.
[x] How does a player get the replay file for their own match, and how long is it available?
[x] What are the community API's rate limits and terms of use, and may a student project use the database dumps for training?
[x] How are rank brackets exposed, and are there enough matches per hero per bracket for stable benchmarks?
[x] Has Valve published any rules for third-party Deadlock tools? Re-check before any public release.
[x] Which 7–8B base model has the best licence and quality for fine-tuning at the time you start?
[x] How much free GPU time does the team have (Colab/Kaggle quotas) for training and serving?
Sources
• Boon Deadlock demo parser (PyPI)
• Boon documentation
• Deadlock API
• Deadlock API OpenAPI docs
• Deadlock (video game), Wikipedia