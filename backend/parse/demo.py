"""Wrap Boon: turn a .dem file into normalized, privacy-safe tables.

Conventions for every output table:
- match_id on every row; "slot" (0-11) identifies a player within the match.
  Boon keys events by hero_id; each hero appears once per match, so hero_id
  maps to exactly one slot.
- team uses the community API's convention: 0 or 1 (Boon's team_num 2/3 minus 2).
- game_s = on-screen match clock in seconds (0 at game start, pauses removed).

Privacy (CLAUDE.md): only the requesting player keeps an account_id. The
other 11 players are anonymous slots; no names, Steam IDs or chat are kept.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path

import polars as pl

from backend import config

# Boon downloads its own name catalog on first use. Keep it inside data/
# (gitignored) instead of the home folder. Must be set before boon is imported.
os.environ.setdefault("BOON_DATA_DIR", str(config.data_dir() / "boon"))

from boon import Demo  # noqa: E402

STEAM64_BASE = 76561197960265728  # SteamID64 = account_id + this (verified 12/12 in Phase 0)

# Only the datasets the analysis needs. Loading all of them takes 30-40 s;
# these take ~5-6 s (docs/phase0-findings.md, section 3).
CORE_DATASETS = (
    "player_ticks", "kills", "damage", "abilities", "ability_upgrades",
    "objectives", "mid_boss", "world_ticks", "item_purchases",
)

# player_ticks columns dropped for privacy.
TIMELINE_DROP = ("steam_id",)


class ParseError(Exception):
    pass


@dataclass
class ParsedMatch:
    match: dict
    players: pl.DataFrame
    timeline: pl.DataFrame
    kills: pl.DataFrame
    damage: pl.DataFrame
    purchases: pl.DataFrame
    ability_uses: pl.DataFrame
    ability_upgrades: pl.DataFrame
    objectives: pl.DataFrame
    mid_boss: pl.DataFrame
    stat_snapshots: pl.DataFrame
    tables: tuple[str, ...] = field(
        default=(
            "players", "kills", "damage", "purchases", "ability_uses",
            "ability_upgrades", "objectives", "mid_boss", "stat_snapshots",
        ),
        repr=False,
    )


# Post-match stat snapshots recorded in the replay (Boon Demo.summary()). Verified
# equal to the community API's match metadata stats at every snapshot, so a
# replay alone can feed the benchmark comparisons.
SNAPSHOT_COLUMNS = ("net_worth", "kills", "deaths", "assists", "denies", "creep_kills", "player_damage", "boss_damage", "level")


def account_to_steam64(account_id: int) -> int:
    return int(account_id) + STEAM64_BASE


def steam64_to_account(steam_id: int) -> int:
    return int(steam_id) - STEAM64_BASE


def _team(col: str) -> pl.Expr:
    """Boon team_num 2/3 -> 0/1; anything else (0 = none/neutral) -> null."""
    return pl.when(pl.col(col).is_in([2, 3])).then(pl.col(col) - 2).otherwise(None).cast(pl.Int8)


def parse_demo(path: str | Path, requester_account_id: int | None = None) -> ParsedMatch:
    """Parse a replay. requester_account_id is the player who asked for the report.

    Without it, every player is stored anonymously.
    """
    path = Path(path)
    demo = Demo(str(path))
    if demo.match_id is None:
        raise ParseError(f"{path.name} has no match ID (partial or custom demo)")
    match_id = int(demo.match_id)

    # ---- players and slots (checked before the slow dataset load) -----------
    raw_players = demo.players
    hero_ids = raw_players["hero_id"].to_list()
    if len(set(hero_ids)) != len(hero_ids):
        raise ParseError("a hero appears twice; slot mapping by hero_id would be ambiguous")

    requester_steam = account_to_steam64(requester_account_id) if requester_account_id is not None else None
    if requester_steam is not None and requester_steam not in raw_players["steam_id"].to_list():
        raise ParseError(f"account {requester_account_id} did not play in match {match_id}")

    demo.load(*CORE_DATASETS)

    summary = demo.summary()
    final_last_hits = dict(zip(summary["last_hits"]["hero_id"].to_list(), summary["last_hits"]["last_hits"].to_list()))

    players = (
        raw_players.with_row_index("slot")
        .with_columns(
            pl.col("slot").cast(pl.Int8),
            _team("team_num").alias("team"),
            (pl.col("steam_id") == requester_steam).fill_null(False).alias("is_requester")
            if requester_steam is not None
            else pl.lit(False).alias("is_requester"),
        )
        .with_columns(
            pl.when(pl.col("is_requester"))
            .then(pl.col("steam_id").cast(pl.Int64) - STEAM64_BASE)
            .otherwise(None)
            .alias("account_id"),
        )
        .select(
            pl.lit(match_id).alias("match_id"),
            "slot", "hero_id", "team", "start_lane",
            pl.col("rank").alias("rank_badge"),
            "is_requester", "account_id",
            pl.col("hero_id").replace_strict(final_last_hits, default=None, return_dtype=pl.Int64).alias("last_hits"),
        )
    )
    slot_of = dict(zip(players["hero_id"].to_list(), players["slot"].to_list()))

    stat_snapshots = summary["snapshots"].select(
        pl.lit(match_id).alias("match_id"),
        pl.col("hero_id").replace_strict(slot_of, default=None, return_dtype=pl.Int8).alias("slot"),
        pl.col("snapshot_time_s").alias("time_s"),
        *SNAPSHOT_COLUMNS,
    ).drop_nulls("slot")

    def to_slot(col: str, alias: str) -> pl.Expr:
        # hero_id 0 means "not a hero" (troopers, towers, ...) -> null slot.
        return pl.col(col).replace_strict(slot_of, default=None, return_dtype=pl.Int8).alias(alias)

    # ---- tick -> match clock -------------------------------------------------
    clock = pl.DataFrame(
        {
            "tick": pl.Series(range(demo.total_ticks + 1), dtype=pl.Int64),
            "game_s": [demo.tick_to_match_seconds(t) for t in range(demo.total_ticks + 1)],
        },
        schema={"tick": pl.Int64, "game_s": pl.Float64},
    )

    def with_clock(df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(pl.col("tick").cast(pl.Int64)).join(clock, on="tick", how="left")

    def stamp(df: pl.DataFrame, *cols: pl.Expr | str) -> pl.DataFrame:
        return with_clock(df).select(pl.lit(match_id).alias("match_id"), "tick", "game_s", *cols)

    # ---- events ----------------------------------------------------------
    kills = stamp(
        demo.kills.with_columns(
            to_slot("victim_hero_id", "victim_slot"),
            to_slot("attacker_hero_id", "attacker_slot"),
            pl.col("assister_hero_ids")
            .list.eval(pl.element().replace_strict(slot_of, default=None, return_dtype=pl.Int8))
            .list.drop_nulls()
            .alias("assister_slots"),
        ),
        "victim_slot", "attacker_slot", "assister_slots",
    )

    damage = stamp(
        demo.damage.with_columns(
            to_slot("attacker_hero_id", "attacker_slot"),
            to_slot("victim_hero_id", "victim_slot"),
        ),
        "attacker_slot", "victim_slot", "damage", "pre_damage", "crit_damage",
        "hitgroup_id", "ability_id", "is_melee", "victim_health_new",
        "attacker_class", "victim_class", "citadel_type",
    )

    purchases = stamp(
        demo.item_purchases.with_columns(to_slot("hero_id", "slot")),
        "slot", pl.col("ability_id").alias("item_id"), "change",
        pl.col("upgraded_from_ability_ids").alias("upgraded_from_item_ids"),
    )

    ability_uses = stamp(
        demo.abilities.with_columns(to_slot("hero_id", "slot")),
        "slot", pl.col("ability").alias("ability_class"),
    )

    ability_upgrades = stamp(
        demo.ability_upgrades.with_columns(to_slot("hero_id", "slot")),
        "slot", "ability_id", "tier",
    )

    objectives = stamp(
        demo.objectives.with_columns(_team("team_num").alias("team")),
        "objective_type", "team", "lane", "health", "max_health", "phase",
        "x", "y", "z", "entity_id",
    )

    mid_boss = stamp(demo.mid_boss.with_columns(_team("team_num").alias("team")), "team", "event")

    timeline = with_clock(
        demo.player_ticks.drop(*TIMELINE_DROP).with_columns(to_slot("hero_id", "slot"))
    ).select(pl.lit(match_id).alias("match_id"), "slot", "game_s", pl.all().exclude("match_id", "slot", "game_s"))

    winner = demo.winning_team_num
    match = {
        "match_id": match_id,
        "build": int(demo.build),
        "game_mode": int(demo.game_mode),
        "map_name": demo.map_name,
        "tick_rate": int(demo.tick_rate),
        "game_start_tick": demo.game_start_tick,
        "game_over_tick": demo.game_over_tick,
        "duration_s": demo.regulation_seconds,
        "winning_team": winner - 2 if winner in (2, 3) else None,
        "boon_version": pkg_version("boon-deadlock"),
        "requester_account_id": requester_account_id,
        "source_file": path.name,
    }
    return ParsedMatch(
        match=match, players=players, timeline=timeline, kills=kills, damage=damage,
        purchases=purchases, ability_uses=ability_uses, ability_upgrades=ability_upgrades,
        objectives=objectives, mid_boss=mid_boss, stat_snapshots=stat_snapshots,
    )
