# Phase 2 notes: analysis engine

Date: 2026-10-20. These are facts verified while building Phase 2, plus the design choices that follow from them.

## Verified facts

1. **Benchmarks must come from ranked matches.**
   - In the data lake, `average_badge` is only set for `match_mode = 'Ranked'`; it is null for unranked games.
   - Each ranked row also has the player's own rank (`player_rank_initial_display_rank`). It was set on 100% of rows checked.
   - Brackets therefore use the player's own badge.
2. **Match metadata often has no lobby rank.** `average_badge_team0/1` was 0 for all 3 fixture matches, including the ranked one. The player's bracket comes from, in order:
   - `player_rank_data.initial_display_rank` in the metadata
   - the replay's `players.rank`, which is only set in ranked matches (unranked replays have rank 0 for all 12 players)
   - `/v1/players/{id}/rank` (the player's current rank)
3. **Replay counters do not equal API counters.** Same player, same moment (match 120851860):

   | Stat | API | Replay |
   |---|---|---|
   | Net worth at 9:00 | 5,230 | 3,702 (`gold_net_worth`) |
   | Last hits at 3:00 | 8 (`creep_kills`) | 13 (`last_hits`) |

   Benchmarks are built from API data, so **every benchmark comparison uses the match's API metadata**, even in the Medium tier. The replay is used only for things the API doesn't have.
4. **Replay timers use a server clock.** `death_time`, `ultimate_cooldown_start` and `ultimate_cooldown_end` are the match clock plus a fixed offset per match (73.4 s and 99.5 s in two fixtures; constant across 12 deaths each). The offset is measured from deaths.
5. **The ultimate timer names are misleading.** At the cast tick, `ultimate_cooldown_end` equals the cast time and `ultimate_cooldown_start` equals the time the ultimate is ready again. The code uses "ready at = the larger of the two".
6. **Static data marks ultimates.** For every playable hero, the hero's `signature1-4` entries are its abilities, and exactly one has `ability_type = "ultimate"` (43/43 heroes).
7. **Some abilities are passives with 0 casts** (for example, a weapon-based signature ability). The static data has no reliable passive flag, so abilities with 0 casts are left out of `ability_casts` rather than reported as unused.
8. **Data lake files:**
   - "base" files (~3 GB, one per range of a million match IDs) plus small "delta"/"residual" update files.
   - Base files in different folders can share a file name, so local extracts are named by their full path.
   - Rows overlap between base and update files, so they are deduplicated on (match_id, account_id).
   - Reading the needed columns of one base file takes about 1.5 minutes.

## Design

- **One frame, one set of metric functions.** Benchmark rows and a user's match are converted to the same "player-match frame" (`analysis/frame.py`). The same vectorized metric code runs on both, so the comparison is like for like.
- **Brackets:** rank tiers in pairs, with the top tier merged into the bracket below. Names come from static data.
- **Benchmarks:**
  - p05–p95 per metric for each (hero, bracket), each hero overall, and each bracket overall (all heroes).
  - Lookup order is hero + bracket, then hero, then all heroes in the bracket, with at least 200 matches per scope.
  - Item timings: first-purchase p25/p50/p75 per (hero, bracket, item) for tier-3+ items, needing at least 50 buys.
- **Detectors (14):**
  - 10 compare one metric with the bracket. An issue fires when the player is in the bottom 35%; severity runs from 0.33 to 1.0 at the 5th percentile.
  - `late_core_items` compares the time of the player's **third** tier-3+ item with the same hero one bracket up, using the same bottom-35% rule. The first version compared each item with that item's median, which flagged 75–78% of real players on at least one item (build order varies), so it was replaced.
  - 3 replay rules, calibrated on the fixtures:
    - "Alone" means no living teammate within 3,000 units, about the 85th percentile of nearest-ally distance at deaths.
    - Wasted ultimate: at least 2 deaths with the ultimate ready for at least 20 s. In the fixtures, 29% of deaths with a trained ultimate happened while it was ready, so a single one is normal; the first rule (1 death, 10 s) flagged 22 of 36 players, the final rule flags 9.
    - "Present" at an objective means alive and within 3,000 units.
- **Check on the 3 fixture matches (36 player-matches):**
  - 1–8 issues per player (median 6); the largest summary is about 1,060 tokens.
  - Two of the matches are unranked, so for this check those players were placed in an assumed bracket. That inflates the rates. The real review uses each player's own rank.
- **Strengths:** metrics where the player is in the top 30%; the top 3 are kept.
- **Kill participation** = (kills + assists) / enemy deaths. Using team kills as the denominator gave values above 100% on real data, because assists also count when enemies die to troopers or towers.

## Known limits

- Replay-only detectors have no rank benchmark yet: their thresholds are fixed rules. The data lake has no replay data, so a benchmark would need many parsed replays per bracket.
- Death and killer positions are available in the Free tier but not yet used by a detector. Map-aware rules, such as "died deep in enemy territory", would need map geometry from `/v1/assets/map`.
- The token count is an estimate (JSON characters ÷ 3.5). Phase 3 will log real counts from the model's tokenizer.
