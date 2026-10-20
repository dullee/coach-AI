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
    players: pl.DataFrame     # slot, hero_id, team, rank_badge, ...
    kills: pl.DataFrame
    ability_uses: pl.DataFrame
    objectives: pl.DataFrame
    timeline: pl.DataFrame    # the player's team only (enough for every replay metric)

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
            rel = conn.execute(select(db.matches.c.timeline_path).where(db.matches.c.match_id == match_id)).scalar_one()
        team_slots = players.filter(pl.col("team") == team)["slot"].to_list()
        timeline = pl.scan_parquet(data_dir / rel).filter(pl.col("slot").is_in(team_slots)).collect()
        return cls(match_id, slot, team, players, table(db.kills), table(db.ability_uses), table(db.objectives), timeline)
