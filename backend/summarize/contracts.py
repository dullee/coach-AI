"""Data contracts between analysis, summarize and coach (see CLAUDE.md, Data contracts).

If a field changes here, update CLAUDE.md and the tests in the same commit.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Contract):
    """The numbers behind an Issue or Strength. The coach may only quote these."""

    player_value: float | None
    bracket_p50: float | None = None
    percentile: float | None = Field(None, ge=0, le=1, description="player's percentile in the bracket (1 = best)")
    unit: str
    window: str
    details: dict[str, float | int | str] = Field(default_factory=dict)


class Issue(_Contract):
    """One detected mistake, produced by a rule in analysis/issues/."""

    id: str
    category: Literal["farming", "survival", "combat", "teamplay", "objectives", "mechanics", "items", "positioning", "abilities"]
    severity: float = Field(ge=0, le=1)
    evidence: Evidence
    timestamps_s: list[float] = Field(default_factory=list)
    hero: str


class Strength(_Contract):
    """A metric where the player beat the bracket median (top 2-3 go in the summary)."""

    id: str
    label: str
    evidence: Evidence


class PlayerInfo(_Contract):
    hero: str
    rank_bracket: str   # the bracket the player is compared with
    bracket_basis: Literal["own_rank", "estimated_from_stats", "all_ranks"] = "own_rank"
    # Unranked players only: the bracket this match's stats match ("below <lowest>" if under it).
    estimated_level: str | None = None
    result: Literal["win", "loss"]
    duration_s: int


class KeyStats(_Contract):
    kda: tuple[int, int, int]
    souls_per_min: float
    damage_per_min: float
    last_hits: int
    kill_participation: float | None = None
    ability_casts: dict[str, int] | None = None  # replay only: ability name -> casts


class MatchSummary(_Contract):
    """What the coaching model receives (target: under 2,000 tokens)."""

    match_id: int
    patch: str
    analysis_level: Literal["free", "medium"]
    player: PlayerInfo
    key_stats: KeyStats
    issues: list[Issue] = Field(max_length=10)
    strengths: list[Strength] = Field(max_length=3)
    hero_context: str
