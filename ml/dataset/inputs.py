"""Build MatchSummaries for the training set (Phase 4 spike).

    python -m ml.dataset.inputs [--n 400] [--own-account A]

Sources:
- "lake": ranked players from the local data-lake extracts (data/lake_cache/, made by the
  benchmark build), one player per match, spread over heroes, rank brackets and win/loss.
- "fixture": the 36 players of the 3 fixture matches (anonymized metadata in tests/data/api/).
- "own": the developer's own matches (API history + parsed replays), if --own-account is given.

Lake rows have no "sold" times for items, so their hero_context lists items bought during the
match instead of items held at the end (the builder says which). Output:
data/ml/inputs.jsonl, one {"id", "source", "match_id", "summary"} per line.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import polars as pl

from backend import config
from backend.analysis.benchmarks import Benchmarks
from backend.analysis.benchmarks.build import load_rows
from backend.analysis.brackets import tier_to_bracket_map
from backend.static_data import StaticData, load
from backend.summarize.builder import build_summary

OUT = config.data_dir() / "ml" / "inputs.jsonl"


def metadata_from_lake(rows: list[dict]) -> dict:
    """API-shaped match metadata from the data-lake rows of one match (all 12 players)."""
    first = rows[0]
    team = lambda r: int(str(r["team"]).replace("Team", ""))  # noqa: E731
    winner = next((team(r) for r in rows if r["won"]), None)
    players = []
    for r in rows:
        t = r["stats.time_stamp_s"] or []
        stat_cols = {"net_worth": "stats.net_worth", "creep_kills": "stats.creep_kills", "player_damage": "stats.player_damage",
                     "boss_damage": "stats.boss_damage", "shots_hit": "stats.shots_hit", "shots_missed": "stats.shots_missed"}
        stats = [{"time_stamp_s": ts, **{k: (r[c] or [0] * len(t))[i] for k, c in stat_cols.items()}} for i, ts in enumerate(t)]
        players.append({
            "account_id": r["account_id"], "team": team(r), "hero_id": r["hero_id"],
            "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"], "net_worth": r["net_worth"],
            "last_hits": r["last_hits"], "denies": r["denies"],
            "player_rank_data": {"initial_display_rank": r["player_rank_initial_display_rank"]}
            if r["player_rank_initial_display_rank"] else None,
            "stats": stats,
            # no sold_time_s in the extract -> the summary builder labels these "items bought"
            "items": [{"item_id": i, "game_time_s": g} for i, g in zip(r["items.item_id"] or [], r["items.game_time_s"] or [])],
            "death_details": [{"game_time_s": g} for g in (r["death_details.game_time_s"] or [])],
        })
    return {"match_id": first["match_id"], "duration_s": first["duration_s"], "winning_team": winner, "players": players}


def pick_lake_players(df: pl.DataFrame, static: StaticData, n: int, seed: int) -> list[tuple[int, int]]:
    """(match_id, account_id) pairs: one per match, hero / bracket / result spread out greedily."""
    rng = random.Random(seed)
    full = df.group_by("match_id").len().filter(pl.col("len") == 12)["match_id"].to_list()
    rng.shuffle(full)
    tier_map = tier_to_bracket_map(static)
    hero_cap = max(3, round(2 * n / max(1, len(static.playable_heroes()))))
    n_brackets = len(set(tier_map.values()))
    bracket_cap = round(1.3 * n / max(1, n_brackets))
    by_match = {m: g for m, g in df.filter(pl.col("match_id").is_in(full[: n * 20])).group_by("match_id")}
    heroes, brackets_used, results = Counter(), Counter(), Counter()
    picked = []
    for m in full[: n * 20]:
        rows = by_match.get((m,)) if (m,) in by_match else by_match.get(m)
        if rows is None:
            continue
        cand = rows.sample(fraction=1.0, shuffle=True, seed=rng.randint(0, 10**9)).to_dicts()
        for r in cand:
            badge = r["player_rank_initial_display_rank"] or 0
            b = tier_map.get(badge // 10)
            want_win = results[True] <= results[False]
            if b is None or heroes[r["hero_id"]] >= hero_cap or brackets_used[b] >= bracket_cap or bool(r["won"]) != want_win:
                continue
            heroes[r["hero_id"]] += 1
            brackets_used[b] += 1
            results[bool(r["won"])] += 1
            picked.append((m, r["account_id"]))
            break
        if len(picked) >= n:
            break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.dataset.inputs")
    ap.add_argument("--n", type=int, default=400, help="number of data-lake players")
    ap.add_argument("--own-account", type=int, help="also include this account's matches")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    static, bench = load(), Benchmarks.load()
    extracts = sorted(p for p in (config.data_dir() / "lake_cache" / "match_player").glob("*.parquet")
                      if not p.name.endswith(".tmp.parquet"))
    if not extracts:
        print("no data-lake extracts; run python -m backend.analysis.benchmarks build first")
        return 1
    df = load_rows(extracts)
    picks = pick_lake_players(df, static, args.n, args.seed)
    rows_by_match = {m: g.to_dicts() for (m,), g in df.filter(pl.col("match_id").is_in([m for m, _ in picks])).group_by("match_id")}

    out: list[dict] = []
    for m, acc in picks:
        meta = metadata_from_lake(rows_by_match[m])
        s = build_summary(meta, acc, static, bench)
        out.append({"id": f"lake-{m}-{s.player.hero}", "source": "lake", "match_id": m, "summary": s.model_dump(mode="json", exclude_none=True)})

    for f in sorted((config.REPO_ROOT / "tests" / "data" / "api").glob("metadata_*.json")):
        meta = json.loads(f.read_text(encoding="utf-8"))
        for p in meta["players"]:
            s = build_summary(meta, p["account_id"], static, bench)
            out.append({"id": f"fixture-{meta['match_id']}-{s.player.hero}", "source": "fixture",
                        "match_id": meta["match_id"], "summary": s.model_dump(mode="json", exclude_none=True)})

    if args.own_account:
        from backend.analysis.progress import player_match_ids
        from backend.db import get_engine
        from backend.ingest import DeadlockApiClient, NotFound, RateLimited
        from backend.summarize.sources import match_data

        engine = get_engine()
        with DeadlockApiClient() as client:
            badge = client.player_rank(args.own_account).badge or None
            for m in player_match_ids(client, engine, args.own_account):
                try:
                    meta, replay = match_data(client, m, args.own_account, engine)
                except (NotFound, RateLimited):
                    continue
                s = build_summary(meta, args.own_account, static, bench, replay, badge)
                out.append({"id": f"own-{m}", "source": "own", "match_id": m, "summary": s.model_dump(mode="json", exclude_none=True)})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for row in out:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    src = Counter(r["source"] for r in out)
    heroes = Counter(r["summary"]["player"]["hero"] for r in out)
    brackets_ = Counter(r["summary"]["player"]["rank_bracket"] for r in out)
    results = Counter(r["summary"]["player"]["result"] for r in out)
    levels = Counter(r["summary"]["analysis_level"] for r in out)
    print(f"wrote {len(out)} summaries to {OUT}\n sources {dict(src)}\n results {dict(results)}  levels {dict(levels)}\n"
          f" {len(heroes)} heroes (max {max(heroes.values())} per hero)\n brackets {dict(brackets_)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
