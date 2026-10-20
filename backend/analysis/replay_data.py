"""Replay tables for one player, loaded from what backend.parse stored."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl
from sqlalchemy import select
from sqlalchemy.engine import Engine

from backend import config, db


@dataclass
class ReplayData:
    match_id: int
    slot: int                 # the player being analysed
    team: int
    players: pl.DataFrame     # slot, hero_id, team, rank_badge, account_id, last_hits, ...
    kills: pl.DataFrame
    ability_uses: pl.DataFrame
    objectives: pl.DataFrame
    timeline: pl.DataFrame    # the player's team only (enough for every replay metric)
    purchases: pl.DataFrame | None = None
    stat_snapshots: pl.DataFrame | None = None
    match: dict | None = None  # row of the matches table

    @property
    def rank_badge(self) -> int | None:
        r = self.players.filter(pl.col("slot") == self.slot)["rank_badge"][0]
        return int(r) if r else None

    @classmethod
    def load(cls, match_id: int, hero_id: int, engine: Engine, data_dir: Path | None = None) -> ReplayData:
        """hero_id picks the player (each hero appears once per match)."""
        data_dir = Path(data_dir or config.data_dir())

        def table(t) -> pl.DataFrame:
            with engine.connect() as conn:
                rows = conn.execute(select(t).where(t.c.match_id == match_id)).mappings().all()
            return pl.DataFrame([dict(r) for r in rows], infer_schema_length=None) if rows else pl.DataFrame(
                schema={c.name: pl.Null for c in t.columns})

        players = table(db.match_players)
        if players.is_empty():
            raise LookupError(f"match {match_id} is not in the database; run python -m backend.parse first")
        me = players.filter(pl.col("hero_id") == hero_id)
        if me.is_empty():
            raise LookupError(f"hero {hero_id} is not in stored match {match_id}")
        slot, team = int(me["slot"][0]), int(me["team"][0])
        with engine.connect() as conn:
            match = dict(conn.execute(select(db.matches).where(db.matches.c.match_id == match_id)).mappings().one())
        team_slots = players.filter(pl.col("team") == team)["slot"].to_list()
        timeline = pl.scan_parquet(data_dir / match["timeline_path"]).filter(pl.col("slot").is_in(team_slots)).collect()
        return cls(match_id, slot, team, players, table(db.kills), table(db.ability_uses), table(db.objectives),
                   timeline, table(db.purchases), table(db.stat_snapshots), match)

    @classmethod
    def load_for_account(cls, match_id: int, account_id: int, engine: Engine, data_dir: Path | None = None) -> ReplayData:
        """The stored replay of a match parsed with --account-id for this player."""
        with engine.connect() as conn:
            hero = conn.execute(select(db.match_players.c.hero_id).where(
                (db.match_players.c.match_id == match_id) & (db.match_players.c.account_id == account_id))).scalar()
        if hero is None:
            raise LookupError(f"no stored replay of match {match_id} parsed for account {account_id}")
        return cls.load(match_id, hero, engine, data_dir)
