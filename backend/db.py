"""Database schema (SQLAlchemy Core). SQLite for local dev, PostgreSQL later.

Event tables live in the database. The per-tick player timeline (~1.2-1.5M
rows per match) is stored as a Parquet file next to the database and
referenced from matches.timeline_path, because row-by-row inserts of that
size would make parsing far slower than the 30 s target.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer,
    MetaData, SmallInteger, String, Table, create_engine, func,
)
from sqlalchemy.engine import Engine

from backend import config

metadata = MetaData()


def _match_fk() -> Column:
    return Column("match_id", BigInteger, ForeignKey("matches.match_id", ondelete="CASCADE"), nullable=False, index=True)


def _event_cols() -> list[Column]:
    return [_match_fk(), Column("tick", Integer, nullable=False), Column("game_s", Float)]


matches = Table(
    "matches", metadata,
    Column("match_id", BigInteger, primary_key=True),
    Column("build", Integer, nullable=False),
    Column("game_mode", SmallInteger),
    Column("map_name", String(64)),
    Column("tick_rate", SmallInteger),
    Column("game_start_tick", Integer),
    Column("game_over_tick", Integer),
    Column("duration_s", Float),
    Column("winning_team", SmallInteger),     # 0 or 1 (community API convention)
    Column("boon_version", String(32)),
    Column("requester_account_id", BigInteger, index=True),
    Column("source_file", String(255)),
    Column("timeline_path", String(512)),      # relative to the data directory
    Column("parsed_at", DateTime(timezone=True), server_default=func.now()),
)

match_players = Table(
    "match_players", metadata,
    _match_fk(),
    Column("slot", SmallInteger, nullable=False),
    Column("hero_id", Integer, nullable=False),
    Column("team", SmallInteger),
    Column("start_lane", SmallInteger),
    Column("rank_badge", SmallInteger),
    Column("is_requester", Boolean, nullable=False),
    Column("account_id", BigInteger),          # only set for the requesting player
    Column("last_hits", Integer),              # final, from the replay's post-match stats
)

# Post-match stat snapshots from the replay (equal to the API's metadata stats).
stat_snapshots = Table(
    "stat_snapshots", metadata,
    _match_fk(),
    Column("slot", SmallInteger, nullable=False),
    Column("time_s", Integer, nullable=False),
    *(Column(c, Integer) for c in (
        "net_worth", "kills", "deaths", "assists", "denies", "creep_kills", "player_damage", "boss_damage", "level")),
)

kills = Table(
    "kills", metadata, *_event_cols(),
    Column("victim_slot", SmallInteger),
    Column("attacker_slot", SmallInteger),     # null = killed by a non-hero
    Column("assister_slots", JSON),
)

damage = Table(
    "damage", metadata, *_event_cols(),
    Column("attacker_slot", SmallInteger),     # null = not a hero
    Column("victim_slot", SmallInteger),
    Column("damage", Integer),
    Column("pre_damage", Float),
    Column("crit_damage", Float),
    Column("hitgroup_id", Integer),
    Column("ability_id", BigInteger),
    Column("is_melee", Boolean),
    Column("victim_health_new", Integer),
    Column("attacker_class", BigInteger),
    Column("victim_class", BigInteger),
    Column("citadel_type", Integer),
)

purchases = Table(
    "purchases", metadata, *_event_cols(),
    Column("slot", SmallInteger),
    Column("item_id", BigInteger),
    Column("change", String(16)),
    Column("upgraded_from_item_ids", JSON),
)

ability_uses = Table(
    "ability_uses", metadata, *_event_cols(),
    Column("slot", SmallInteger),
    Column("ability_class", String(128)),
)

ability_upgrades = Table(
    "ability_upgrades", metadata, *_event_cols(),
    Column("slot", SmallInteger),
    Column("ability_id", BigInteger),
    Column("tier", SmallInteger),
)

objectives = Table(
    "objectives", metadata, *_event_cols(),
    Column("objective_type", String(16)),
    Column("team", SmallInteger),
    Column("lane", SmallInteger),
    Column("health", Integer),
    Column("max_health", Integer),
    Column("phase", SmallInteger),
    Column("x", Float), Column("y", Float), Column("z", Float),
    Column("entity_id", Integer),
)

mid_boss = Table(
    "mid_boss", metadata, *_event_cols(),
    Column("team", SmallInteger),
    Column("event", String(16)),
)

EVENT_TABLES = {
    "players": match_players, "kills": kills, "damage": damage, "purchases": purchases,
    "ability_uses": ability_uses, "ability_upgrades": ability_upgrades,
    "objectives": objectives, "mid_boss": mid_boss, "stat_snapshots": stat_snapshots,
}


def get_engine(url: str | None = None) -> Engine:
    url = url or config.db_url()
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    metadata.create_all(engine)
    return engine
