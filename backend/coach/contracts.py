"""CoachingReport: what the coaching model must return (validated in Phase 3).

Rule: every claim must trace back to an issue_id or a key stat in the
MatchSummary. The model explains; it never invents numbers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Priority(_Contract):
    title: str
    why: str
    how_to_fix: str
    drill: str
    issue_ids: list[str] = Field(min_length=1)


class CoachingReport(_Contract):
    headline: str
    priorities: list[Priority] = Field(min_length=1, max_length=5)
    strengths: list[str] = Field(default_factory=list)
    next_match_focus: str
