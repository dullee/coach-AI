"""Create small, anonymized test fixtures from real data (run once; output is committed).

    python scripts/make_test_data.py

- tests/data/api/metadata_<match_id>.json: API metadata of the fixture matches,
  trimmed to the fields the analysis reads. Account IDs are replaced with
  1001..1012 in player order (privacy: no real IDs in the repo).
- tests/data/lake_sample.parquet: ~830 random complete matches (~10,000 rows)
  from the local data-lake extract (needs a benchmark build first), account_id
  replaced with a row number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from backend import config  # noqa: E402
from backend.ingest import DeadlockApiClient  # noqa: E402

OUT = config.REPO_ROOT / "tests" / "data"
MATCH_KEYS = ("match_id", "duration_s", "winning_team", "match_mode", "game_mode")
PLAYER_KEYS = ("team", "hero_id", "kills", "deaths", "assists", "net_worth", "last_hits", "denies")
STAT_KEYS = ("time_stamp_s", "net_worth", "creep_kills", "player_damage", "boss_damage", "shots_hit", "shots_missed")
FAKE_ACCOUNT_BASE = 1001


def trim_metadata(meta: dict) -> dict:
    out = {k: meta[k] for k in MATCH_KEYS}
    out["players"] = []
    for n, p in enumerate(meta["players"]):
        rank = (p.get("player_rank_data") or {}).get("initial_display_rank")
        out["players"].append({
            "account_id": FAKE_ACCOUNT_BASE + n,
            **{k: p[k] for k in PLAYER_KEYS},
            "player_rank_data": {"initial_display_rank": rank} if rank else None,
            "stats": [{k: s.get(k) for k in STAT_KEYS} for s in p.get("stats") or []],
            "items": [{k: i.get(k) for k in ("item_id", "game_time_s", "sold_time_s")} for i in p.get("items") or []],
            "death_details": [{"game_time_s": d["game_time_s"]} for d in p.get("death_details") or []],
        })
    return out


def main() -> None:
    fixtures = json.loads((config.REPO_ROOT / "tests" / "fixtures.json").read_text())["replays"]
    (OUT / "api").mkdir(parents=True, exist_ok=True)
    with DeadlockApiClient() as client:
        for f in fixtures:
            meta = trim_metadata(client.match_metadata(f["match_id"]))
            (OUT / "api" / f"metadata_{f['match_id']}.json").write_text(json.dumps(meta), encoding="utf-8")
            print("wrote metadata", f["match_id"])

    extracts = sorted(p for p in (config.data_dir() / "lake_cache" / "match_player").glob("*.parquet")
                      if not p.name.endswith(".tmp.parquet"))
    if not extracts:
        print("no lake extracts; run python -m backend.analysis.benchmarks build first")
        return
    df = pl.concat([pl.read_parquet(p) for p in extracts], how="vertical_relaxed").unique(["match_id", "account_id"])
    # Whole matches (kill participation needs every player of a team).
    full = df.group_by("match_id").len().filter(pl.col("len") == 12)["match_id"]
    picked = full.sample(n=min(10_000 // 12, full.len()), seed=7)
    sample = df.filter(pl.col("match_id").is_in(picked.implode())).with_row_index("account_id_new")
    sample = sample.drop("account_id").rename({"account_id_new": "account_id"}).with_columns(pl.col("account_id").cast(pl.UInt32))
    sample.write_parquet(OUT / "lake_sample.parquet", compression="zstd")
    print(f"wrote lake sample: {sample.height} rows, {(OUT / 'lake_sample.parquet').stat().st_size // 1000} KB")


if __name__ == "__main__":
    main()
