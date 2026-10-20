# Phase 0 findings: data spike

Date: 2026-10-20 · Boon `boon-deadlock` 0.18.0 · Python 3.12 · Game build 10932

This doc answers the Open questions in CLAUDE.md and lists where the findings disagree with the plan.
Everything marked **Verified** was run or fetched during this spike. Everything marked **Unverified** still needs a person to confirm it.

---

## 1. What we did

1. Installed Boon 0.18.0 in a throwaway virtualenv.
2. Found 3 recent public normal-mode matches through the community API, downloaded their replays from Valve's replay servers, and parsed all three with Boon.
3. Listed every Boon dataset and column, timed the parse, and checked the camera-angle columns for real values.
4. Read the community API's OpenAPI spec (`https://api.deadlock-api.com/openapi.json`), called the main endpoints, and read the data-lake manifest.
5. Checked how long replays stay downloadable by probing replay URLs of different ages.

Replays used (stored in `data/demos/`, which is gitignored):

| Match ID | Length | Uncompressed size | Ranks in lobby (badge) | Player-tick rows |
|---|---|---|---|---|
| 120851860 | 27:23 | 298 MB | 101–111 | 1,248,179 |
| 120849259 | 27:41 | 288 MB | not checked | 1,189,903 |
| 120849236 | 35:50 | 372 MB | not checked | 1,508,395 |

All three replays: tick rate 64, all 12 players present, `game_mode = 1` (normal), map name `start`.

---

## 2. Answers to the open questions

### Q1. Does Boon's `player_ticks` include view angles? — **Yes (Verified)**

- `player_ticks` has `pitch`, `yaw` and `roll` columns, documented as *camera* angles.
- They hold real values. On match 120851860: `yaw` has 2,048 distinct values in 0–360° (≈0.18° steps), `pitch` has 998 distinct values in 0–360° (looking up is stored wrapped, e.g. −10° shows as 350°), and `roll` is always 0.
- Rows exist for **every tick** (64 per second) for each living player. Each player has about 96k–135k rows per match.

**What this means:** basic aim and awareness analysis (where the player was looking when they died, how fast they turned toward an attacker, and so on) *can* stay in the Medium (replay) tier. It does not need to move to video.

**Caveats:**
- This is the camera direction, not proof of where shots landed. Hits come from the `damage` table (`hitgroup_id`, `crit_damage`), and the API match stats include `shots_hit` and `shots_missed`.
- Before building aim metrics, confirm the angle conventions (which way yaw 0° points, and the pitch sign) against a known moment in a replay. This is not verified yet.

### Q2. How does a player get the replay for their own match, and how long is it available? — **Answered (partly Verified)**

**Two routes:**

1. **In-game (manual):** Profile → *View all match history* → pick the match → *Download Replay*. Source: [esports.gg guide](https://esports.gg/guides/deadlock/how-to-watch-and-download-your-match-replay-in-deadlock). **Unverified** by us in-game; one team member should try it and note where the file is saved.
2. **Through the community API (automatic):** `GET /v1/matches/{match_id}/salts` returns a `demo_url` such as
   `http://replay389.valve.net/1422450/120851860_329453757.dem.bz2`. **Verified**: we downloaded 3 replays this way.

**Findings from the API route (Verified):**
- **Not every match has a replay link.** Only 1 of the first 6 recent matches we checked had a `replay_salt`; the rest returned `demo_url: null`. The API says it fetches "many matches without salts".
- **The `.dem.bz2` files are actually zstd-compressed**, not bzip2 (they start with the zstd magic bytes `28 B5 2F FD`). Python's `bz2` module fails on them; the `zstandard` package works.
- **Size:** 185–242 MB compressed, 288–372 MB uncompressed. Each download took about 40 s.

**How long replays are available (Verified, with an estimate):**

| Match age | Replay URL status |
|---|---|
| 1.9, 2.9, 4.2, 6.6, 8.9, 10.2, 12.0, 13.0, 16.0, 17.8 days | 200 (available) |
| about 28–35 days (age estimated from match ID) | 404 (gone) |

So replays were available for at least **18 days**, and were gone by about **4 weeks**. The age for the "gone" matches is an estimate. The API no longer had metadata for those matches, so we worked it out from how fast match IDs grow (about 600k IDs per day). Valve does not publish a retention policy.

**What this means:** the Medium tier should ask players to upload a replay, or analyse within about 2 weeks of the match. Showing "replay may have expired" after ~14 days is a safe default.

### Q3. What are the community API's rate limits and terms of use? May a student project use the database dumps for training? — **Limits answered; terms unclear**

**Rate limits (Verified from the OpenAPI spec; per IP without an API key):**

| Endpoint | Limit per IP |
|---|---|
| `/v1/analytics/*` (hero-stats, item-stats, and so on), shared | 200 req/min |
| `/v1/matches/{id}/metadata` | 100 req/s from cache, 100 req/10 s from S3, **3 req/hour when it has to fetch from Steam** |
| `/v1/matches/{id}/salts` | 100 req/s from DB, **10 req/30 min from Steam** |
| `/v1/matches/metadata` (bulk) | 30 req/min |
| `/v1/players/{id}/match-history` | 100 req/s; 10 req/hour if it has to refresh from Steam |
| `/v1/players/hero-stats` | 100 req/s |
| `/v1/sql` (**deprecated**) | 2 req/min, 20 req/hour (we hit this limit during the spike) |
| `/v1/matches/demo/query` | 200 req/hour |

- API keys raise most limits. Busy endpoints can return `503` with a `Retry-After` header; the client must respect it.
- Analytics responses are cached on their side for 6–12 hours, so our cache can be at least that long.
- Pass `disable_steam=true` on metadata and salts calls so we never use up the very small "from Steam" quota by accident.
- Match history is only complete if the player's account is friends with one of the API's Steam bots. Otherwise it returns only the matches the API already has stored.

**Terms and licence (Verified as far as public docs go):**
- The API's code is MIT licensed. The data lake is marked "isAccessibleForFree".
- **No written terms cover using the data for ML training**, research or student projects. The privacy page doesn't cover it either.
- The API lets players delete their data and opt out (Steam login required), so data we copy can go stale against their wishes.
- **Action:** email `info@deadlock-api.com` before Phase 4. Describe the project and ask whether training on match data is OK. Record the answer here.

**"Database dumps" are now a data lake (Verified)**, which changes the plan (see section 3):
- Hourly-updated Parquet files with a DuckLake catalog at `https://data.deadlock-api.com`, plus a read-only MCP server at `/v1/mcp`.
- Tables: `match_player` (381M rows, **552 GB**, 169 files), `match_salts` (29M rows, 0.5 GB), `leaderboard`, `hero_leaderboard`, `steam_profiles`, `steam_profile_observed_names`.
- `match_player` has per-player-per-match rows with KDA, net worth, hero, lane, badges, `death_details` (with positions), objectives, mid-boss and more.
- `steam_profiles` contains real names and friend lists. **We should not use these tables** (privacy constraint).

### Q4. How are rank brackets exposed, and are there enough matches per hero per bracket? — **Yes, except at the very top (Verified)**

**How ranks are shown:**
- 12 tiers from `/v1/assets/ranks`: 0 Obscurus (unranked), 1 Initiate, 2 Seeker, 3 Acolyte, 4 Sentinel, 5 Mystic, 6 Ritualist, 7 Emissary, 8 Oracle, 9 Phantom, 10 Ascendant, 11 Eternus.
- A **badge** number = tier × 10 + subrank (1–6). Example: 104 = Ascendant 4.
- Where badges appear:
  - Match metadata: `average_badge_team0/1` and per-player `player_rank_data`.
  - Boon `players.rank`: the same packed number.
  - `/v1/players/{account_id}/rank`.
  - Analytics filters `min_average_badge` / `max_average_badge`, and `bucket=avg_badge`.
- Tier names and images come from the API, so nothing has to be hard-coded.

**Sample sizes:** matches per hero over the default last 30 days, from `/v1/analytics/hero-stats`, 43 playable heroes:

| Badge range | Fewest matches for any hero | Median per hero |
|---|---|---|
| 11–26 (Initiate–Seeker) | 9,992 | 27,630 |
| 31–46 (Acolyte–Sentinel) | 11,533 | 29,676 |
| 51–66 (Mystic–Ritualist) | 10,257 | 24,920 |
| 71–86 (Emissary–Oracle) | 9,240 | 26,578 |
| 91–106 (Phantom–Ascendant) | 4,752 | 17,309 |
| 111–116 (Eternus) | **288** | 1,894 |

**Recommendation:** use about 6 brackets of two tiers each, as in the table. Merge Eternus into the top bracket, or give it a 60-day window.

**Caveats:**
- `hero-stats` returns **totals** (sums of kills, souls and so on), not percentiles. A median or p25/p75 needs per-player rows from the data lake's `match_player` table.
- The 30-day window covers matches from before the latest patch. Filter by patch date using `/v1/patches`.

### Q5. Has Valve published rules for third-party Deadlock tools? — **No written rules found (Unverified, re-check before release)**

- We found **no Valve policy page** for Deadlock third-party tools, overlays or stat trackers.
- **Precedent from 2024:** Valve rate-limited match data access and stat-tracking sites went down for a while. The site admins said Valve felt it was too early in the beta. Source: [esports.gg](https://esports.gg/news/deadlock/valve-halts-deadlock-stat-tracking-website).
- **Precedent from Dota 2:** Valve disabled tools that gave in-match advantages.
- Our post-match-only, no-memory-reading design avoids both problems.
- **Action:** check Valve's Deadlock news and Steam pages again before any public release.

### Q6. Which 7–8B base model has the best licence and quality for fine-tuning? — **Recommendation: Qwen3-8B Instruct (partly Verified)**

| Model | Licence | Notes |
|---|---|---|
| **Qwen3-8B** | Apache 2.0 | Most often recommended in this size class; well supported by Unsloth. **Recommended default.** |
| IBM Granite 4.x 8B | Apache 2.0 | Good alternative; long context. |
| Llama 3.1 8B Instruct | Llama Community Licence (not open source; has naming and attribution rules) | Largest fine-tuning ecosystem, but the licence is weaker. |
| Mistral 7B Instruct | Apache 2.0 | Older; behind newer models on benchmarks. |
| Gemma 4 | Apache 2.0 (since April 2026) | **No ~8B dense size:** the family is E2B, E4B, 26B MoE and 31B. E4B is an option if GPU time is very short. |

Sources: [Gemma 4 licence coverage](https://the-decoder.com/googles-gemma-4-is-now-available-with-apache-2-0-licensing-for-the-first-time/), [awesome-open-weight-models](https://github.com/phlx0/awesome-open-weight-models), [labellerr comparison](https://www.labellerr.com/blog/best-small-language-models-under-10b-parameters/). Benchmark numbers come from third-party blogs.

**Action:** before Phase 4 starts, re-check the model cards on Hugging Face and confirm Unsloth supports the chosen model. Newer releases may exist by then.

### Q7. How much free GPU time does the team have? — **Rough estimates only; each person must check their account**

| Platform | Session limit | Weekly GPU quota | GPU |
|---|---|---|---|
| Kaggle | 12 h (GPU) | Shown in account settings; users report **about 30 h/week**, and it varies | T4 ×2 or P100 |
| Google Colab (free) | Up to 12 h, with idle timeouts | **Not published**, "dynamic"; third parties say 15–30 h/week | Usually T4, not guaranteed |

Sources: [gpuperhour.com](https://gpuperhour.com/blog/free-cloud-gpus-and-credits), [hivenet Colab guide](https://www.hivenet.com/post/google-colaboratory-gpu-complete-guide-to-free-cloud-gpu-access-and-limitations).

**Rough feasibility:** QLoRA on an 8B model with 2–3k short examples on one T4 should take a few hours per run. Four Kaggle accounts give the team about 120 GPU-hours per week, which is enough for several runs.

**Serving:** free notebooks cannot host a model permanently. The demo needs a team member's local GPU running Ollama (an 8B model at 4-bit needs about 6 GB VRAM) or a small paid instance. **Each team member should record their Kaggle quota and local GPU here.**

---

## 3. Boon datasets and columns (Verified on 3 replays)

`Demo.available_datasets()` returns 22 datasets. Row counts are from match 120849259.

| Dataset | Rows | Columns |
|---|---|---|
| `abilities` | 1,058 | tick, hero_id, ability |
| `ability_upgrades` | 115 | tick, hero_id, ability_id, tier |
| `ability_ticks` | 12,268 | tick, hero_id, ability_id, slot, cooldown_start, cooldown_end, remaining_charges, charge_recharge_start, charge_recharge_end |
| `chat` | 9 | tick, hero_id, text, chat_type |
| `mid_boss` | 1 | tick, team_num, event |
| `objectives` | 3,861 | tick, objective_type, team_num, lane, health, max_health, phase, x, y, z, entity_id, entity_serial |
| `player_ticks` | 1,189,903 | tick, steam_id, ammo_fraction, hero_id, x, y, z, pitch, yaw, roll, in_regen_zone, in_item_shop, death_time, last_spawn_time, respawn_time, health, max_health, barrier, lifestate, souls, spent_souls, in_combat_end_time, in_combat_last_damage_time, in_combat_start_time, player_damage_dealt_end_time, player_damage_dealt_last_damage_time, player_damage_dealt_start_time, player_damage_taken_end_time, player_damage_taken_last_damage_time, player_damage_taken_start_time, time_revealed_by_npc, build_id, is_alive, has_rebirth, has_rejuvenator, has_ultimate_trained, health_regen, ultimate_cooldown_start, ultimate_cooldown_end, ap_net_worth, gold_net_worth, denies, hero_damage, hero_healing, objective_damage, self_healing, kill_streak, last_hits, level, kills, deaths, assists, boon_count, upgrade_ids (54 columns) |
| `world_ticks` | 110,085 | tick, is_paused, next_midboss |
| `kills` | 66 | tick, victim_hero_id, attacker_hero_id, assister_hero_ids |
| `damage` | 44,995 | tick, damage, pre_damage, victim_hero_id, attacker_hero_id, victim_health_new, hitgroup_id, crit_damage, attacker_class, victim_class, victim_entity_id, ability_id, damage_type, citadel_type, damage_flags, is_melee, melee_type, damage_absorbed, victim_shield_new, victim_shield_max, server_tick |
| `flex_slots` | 6 | tick, team_num |
| `item_purchases` | 177 | tick, steam_id, hero_id, ability_id, change, upgraded_from_ability_ids |
| `troopers` | 3,965,536 | tick, trooper_type, team_num, lane, health, max_health, x, y, z, entity_id |
| `neutrals` | 44,108 | tick, team_num, health, max_health, x, y, z, entity_id |
| `breakables` | 263 | tick, event, entity_id, entity_serial, subclass_id, subclass_name, team_num, x, y, z |
| `sinners_sacrifice` | 105 | tick, event, entity_id, entity_serial, attacker_hero_id, damage, health, max_health, team_num, x, y, z |
| `stat_modifier_events` | 98 | tick, hero_id, stat_type, amount |
| `active_modifiers` | 187,988 | tick, hero_id, event, serial, modifier_id, ability_id, duration, caster_hero_id, stacks |
| `urn` | 18 | tick, event, hero_id, team_num, x, y, z |
| `rift` | 3 | rift_num, announce_tick, active_tick, capture_tick, expire_tick, winning_team, lane, x, y, z |
| `street_brawl_ticks` / `street_brawl_rounds` | — | Street Brawl mode only (`game_mode = 4`); raise `NotStreetBrawlError` otherwise |

**Also available:**
- `Demo.players`: player_name, steam_id, hero_id, team_num, start_lane, rank.
- Match properties: `match_id`, `build`, `tick_rate`, `game_start_tick`, `game_over_tick`, `winning_team_num`, and the `tick_to_match_seconds()` helpers.
- `Demo.summary()`: the post-match details as 6 tables (snapshots, last_hits, objectives, damage, healing, gold_sources). Snapshot stats are taken about every 3 minutes for each player.
- Helper functions: `teamfights()`, `kill_participation()`, `time_dead()`, `in_combat()`, `enemy_minimap_ticks()`.

**Parse speed (Verified):**

| What we loaded | Time per match |
|---|---|
| Core datasets (player_ticks, kills, damage, abilities, ability_upgrades, objectives, mid_boss, world_ticks, flex_slots, item_purchases) | **5.1–6.4 s** |
| All 20 datasets | 30–41 s (`active_modifiers`, `urn` and `troopers` are the slow ones) |

The Phase 1 "under 30 s" target is easy if Phase 1 loads only the core datasets.

**ID mapping to the community API (Verified on match 120851860):**

| Boon field | Matches API asset | Result |
|---|---|---|
| `players.hero_id` | `/v1/assets/heroes` `id` | 12 / 12 |
| `item_purchases.ability_id` | `/v1/assets/items` `id` | 88 / 88 |
| `ability_upgrades.ability_id` | `/v1/assets/items` `id` | 46 / 46 |
| `abilities.ability` (string) | `/v1/assets/items` `class_name` | 60 / 60 |

So **all names and stats can come from the community API**, as the patch-proof rule requires.

**Filter heroes on `player_selectable == true`.** The API lists 65 heroes, but only 43 are playable.

---

## 4. Where findings disagree with CLAUDE.md (needs a team decision)

1. **Replay fixtures can't go in git as-is.** Each replay is about 300 MB, so 3+ fixtures in `tests/` is about 1 GB.
   - Proposal: keep replays in `data/demos/` (gitignored), and add a script that downloads the fixture match IDs listed in `tests/fixtures.json`. Small trimmed tables (Parquet) can be committed for fast unit tests.
   - Alternative: Git LFS.
2. **"Database dumps" are now a 552 GB data lake**, and the `/v1/sql` endpoint is deprecated (2 req/min).
   - `benchmarks/` should run filtered DuckDB queries against the remote DuckLake (only the needed columns, a date range, and game_mode = 1).
   - Do not download it all. Update the "Match and static data" row and Phase 2 wording.
3. **Medium tier: "uploaded replay" should also allow "fetch replay by match ID".**
   - Automatic fetch only works when the API has a replay salt, and only for about the first 2–3 weeks.
   - Upload stays the main path; fetch-by-ID is an optional shortcut.
   - Downloads are about 200 MB, so this must be a background job.
4. **The Free tier can do more than the plan says.**
   - API match metadata already includes item purchase times (`items[].game_time_s`), stats about every 3 minutes (souls by source, damage, shots hit/missed, healing), and `death_details` with death and killer **positions**.
   - Death positions and item timing could move from Medium to Free. That leaves Medium for tick-level analysis: positioning over time, view angles, ability usage and fight timing.
5. **Aim/awareness stays in Medium, not video.** View angles exist at 64 Hz (Q1).
6. **Boon brings its own name catalog.**
   - On first use, Boon downloads `boon-data` JSON from GitHub into `~/.boon/<version>/`, used by its `*_names()` helpers.
   - Proposal: never use Boon's name helpers. Join Boon's IDs to our `static_data/` from the community API (shown to work above).
   - Set `BOON_DATA_DIR` inside `data/` so CI and workers don't write to the home folder.
7. **Patches can break the parser.** Boon's docs say replays from before the "City Never Sleeps" update need Boon ≤ 0.10 and newer ones need ≥ 0.11.
   - "Patch-proof" holds for hero and item knowledge, but parsing depends on Boon keeping up.
   - Proposal: pin the Boon version, re-run the fixture tests after each game patch, and store `build` with every parsed match.
8. **Privacy: replays contain all 12 players** (names, Steam IDs, chat).
   - The parser should store only the requesting player's data, plus anonymous aggregates for teammates and enemies (positions, damage).
   - Drop `chat` and other players' names.
9. **Rank brackets.** Use about 6 brackets of two tiers each (Q4). Eternus is too small on its own.

---

## 5. Action items before Phase 1

- [ ] Team: decide on the 9 points in section 4 and update CLAUDE.md.
- [ ] Email `info@deadlock-api.com` about training on their data (Q3), and consider requesting an API key.
- [ ] One team member: download a replay in-game and note the file location and format (Q2).
- [ ] Confirm the yaw/pitch conventions on a known moment in a replay (Q1).
- [ ] Each team member: record their Kaggle/Colab quota and local GPU (Q7).
- [ ] Re-check Valve's third-party tool rules before any public release (Q5).

## Reproducing this spike

- Replay URL: `GET https://api.deadlock-api.com/v1/matches/{match_id}/salts?disable_steam=true` → `demo_url`.
- Download it, then decompress with the `zstandard` package even though the file ends in `.bz2`.
- Parse: `from boon import Demo; d = Demo(path); d.load("player_ticks", "kills", "damage", ...)`.
