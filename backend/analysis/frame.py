"""The player-match frame: one row per player per match, same columns whatever the source.

Benchmark rows come from the data lake's match_player table; a user's match
comes from /v1/matches/{id}/metadata. Both are turned into this frame so the
exact same metric code runs on both (docs/phase0-findings.md: replay counters
differ from API counters, so benchmark comparisons always use API data).
"""

from __future__ import annotations

import polars as pl

# data lake column -> frame column
LAKE_COLUMNS: dict[str, str] = {
    "match_id": "match_id",
    "account_id": "account_id",
    "team": "team",
    "hero_id": "hero_id",
    "won": "won",
    "duration_s": "duration_s",
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "net_worth": "net_worth",
    "last_hits": "last_hits",
    "denies": "denies",
    "player_rank_initial_display_rank": "rank_badge",
    "average_badge": "lobby_badge",
    "stats.time_stamp_s": "st_t",
    "stats.net_worth": "st_nw",
    "stats.creep_kills": "st_creep",
    "stats.player_damage": "st_dmg",
    "stats.boss_damage": "st_boss",
    "stats.shots_hit": "st_hit",
    "stats.shots_missed": "st_miss",
    "items.item_id": "it_id",
    "items.game_time_s": "it_t",
    "death_details.game_time_s": "death_t",
}

# metadata stats[] key -> frame list column
_STAT_KEYS = {
    "time_stamp_s": "st_t", "net_worth": "st_nw", "creep_kills": "st_creep",
    "player_damage": "st_dmg", "boss_damage": "st_boss", "shots_hit": "st_hit", "shots_missed": "st_miss",
}

LIST_COLS = ("st_t", "st_nw", "st_creep", "st_dmg", "st_boss", "st_hit", "st_miss", "it_id", "it_t", "death_t")

SCHEMA: dict[str, pl.DataType] = {
    "match_id": pl.Int64, "account_id": pl.Int64, "team": pl.Int8, "hero_id": pl.Int32, "won": pl.Boolean,
    "duration_s": pl.Int32, "kills": pl.Int32, "deaths": pl.Int32, "assists": pl.Int32,
    "net_worth": pl.Int64, "last_hits": pl.Int32, "denies": pl.Int32,
    "rank_badge": pl.Int32, "lobby_badge": pl.Int32,
    **{c: pl.List(pl.Int64) for c in LIST_COLS},
}


def with_team_kills(df: pl.DataFrame) -> pl.DataFrame:
    """Adds team_kills and enemy_deaths. enemy_deaths >= team_kills because enemies
    also die to troopers and towers, and players get assists on those deaths."""
    deaths = df.group_by("match_id", "team").agg(pl.col("deaths").sum().alias("enemy_deaths"))
    deaths = deaths.with_columns((1 - pl.col("team")).cast(pl.Int8).alias("team"))
    return df.with_columns(pl.col("kills").sum().over("match_id", "team").alias("team_kills")).join(
        deaths, on=["match_id", "team"], how="left", maintain_order="left")


def from_lake(df: pl.DataFrame) -> pl.DataFrame:
    """Rows read from the data lake (column names as in LAKE_COLUMNS keys)."""
    out = df.rename({k: v for k, v in LAKE_COLUMNS.items() if k in df.columns})
    if out.schema.get("team") == pl.String or out.schema.get("team") == pl.Categorical:
        out = out.with_columns(pl.col("team").cast(pl.String).str.replace("Team", "").cast(pl.Int8, strict=False))
    return with_team_kills(out.select([pl.col(c).cast(t) for c, t in SCHEMA.items()]))


def from_metadata(meta: dict) -> pl.DataFrame:
    """All 12 players of one match from /v1/matches/{id}/metadata (match_info)."""
    rows = []
    for p in meta["players"]:
        stats = sorted(p.get("stats") or [], key=lambda s: s["time_stamp_s"])
        items = p.get("items") or []
        rank = (p.get("player_rank_data") or {}).get("initial_display_rank")
        row = {
            "match_id": meta["match_id"],
            "account_id": p["account_id"],
            "team": p["team"],
            "hero_id": p["hero_id"],
            "won": p["team"] == meta["winning_team"],
            "duration_s": meta["duration_s"],
            "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "net_worth": p["net_worth"], "last_hits": p["last_hits"], "denies": p["denies"],
            "rank_badge": rank or None,
            "lobby_badge": None,
            "it_id": [i["item_id"] for i in items],
            "it_t": [i["game_time_s"] for i in items],
            "death_t": [d["game_time_s"] for d in p.get("death_details") or []],
        }
        for key, col in _STAT_KEYS.items():
            row[col] = [s.get(key) or 0 for s in stats]
        rows.append(row)
    return with_team_kills(pl.DataFrame(rows, schema=SCHEMA))
