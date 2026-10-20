"""Rank brackets: pairs of rank tiers, built from the ranks in static data.

A badge is tier * 10 + subrank (docs/phase0-findings.md, Q4). Tier 0 is
unranked. Tiers 1..N are grouped in pairs; if the last group would be a
single tier (the top tier has too few matches on its own), it is merged into
the group below.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.static_data import StaticData

ALL = "all"  # pseudo-bracket: every rank together (fallback)


@dataclass(frozen=True)
class Bracket:
    id: str          # e.g. "t01-02"
    label: str       # e.g. "<tier 1 name>-<tier 2 name>"
    tiers: tuple[int, ...]


def brackets(static: StaticData) -> list[Bracket]:
    tiers = sorted(t for t in static.ranks if t > 0)
    groups = [tiers[i:i + 2] for i in range(0, len(tiers), 2)]
    if len(groups) > 1 and len(groups[-1]) == 1:
        single = groups.pop()
        groups[-1] = groups[-1] + single
    out = []
    for g in groups:
        names = [static.ranks[t] for t in (g[0], g[-1])]
        out.append(Bracket(id=f"t{g[0]:02d}-{g[-1]:02d}", label="-".join(dict.fromkeys(names)), tiers=tuple(g)))
    return out


def bracket_of(badge: int | None, static: StaticData) -> Bracket | None:
    """Bracket for a badge; None when unranked or unknown."""
    if not badge:
        return None
    tier = int(badge) // 10
    for b in brackets(static):
        if tier in b.tiers:
            return b
    return None


def next_bracket_up(bracket: Bracket | None, static: StaticData) -> Bracket | None:
    """The bracket above (for "vs higher-rank players" comparisons); the top one maps to itself."""
    if bracket is None:
        return None
    bs = brackets(static)
    i = bs.index(bracket)
    return bs[min(i + 1, len(bs) - 1)]


def tier_to_bracket_map(static: StaticData) -> dict[int, str]:
    return {t: b.id for b in brackets(static) for t in b.tiers}
