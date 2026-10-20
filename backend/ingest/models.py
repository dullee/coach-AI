"""Typed shapes for the community API responses we rely on.

Fields and required/optional status come from the API's OpenAPI spec
(https://api.deadlock-api.com/openapi.json). Extra fields are kept
(extra="allow") so nothing is silently dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MatchSalts(_ApiModel):
    """Schema MatchSaltsResponse. demo_url is null when the API has no replay salt."""

    match_id: int
    cluster_id: int | None = None
    metadata_salt: int | None = None
    replay_salt: int | None = None
    metadata_url: str | None = None
    demo_url: str | None = None


class MatchHistoryEntry(_ApiModel):
    """Schema PlayerMatchHistoryEntry (required fields only, plus ranked badge)."""

    account_id: int
    match_id: int
    hero_id: int
    hero_level: int
    start_time: int
    game_mode: int
    match_mode: int
    player_team: int
    player_kills: int
    player_deaths: int
    player_assists: int
    denies: int
    net_worth: int
    last_hits: int
    match_duration_s: int
    match_result: int
    objectives_mask_team0: int
    objectives_mask_team1: int
    player_match_outcome: int
    ranked_display_badge: int | None = None


class PlayerRank(_ApiModel):
    """Schema RankResponse. badge = tier * 10 + subrank; 0 means unranked."""

    badge: int
    rank: int
    subrank: int


class Rank(_ApiModel):
    """Schema Rank (one of the 12 tiers)."""

    tier: int
    name: str
    color: str
