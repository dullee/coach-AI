"""Build the MatchSummary the coaching model receives (target: under 2,000 tokens)."""

from __future__ import annotations

import json
import math

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.brackets import bracket_of, brackets
from backend.analysis.level import estimate_level
from backend.analysis.frame import from_metadata
from backend.analysis.issues import Context, run_all
from backend.analysis.issues.base import STRENGTH_ABOVE, good_percentile, metric_evidence
from backend.analysis.metrics import METRICS, compute_all
from backend.analysis.metrics.replay import ability_casts
from backend.analysis.replay_data import ReplayData
from backend.static_data import StaticData
from backend.summarize.contracts import KeyStats, MatchSummary, PlayerInfo, Strength

MAX_ISSUES = 8
MAX_STRENGTHS = 3
TOKEN_TARGET = 2000


def estimate_tokens(summary: MatchSummary) -> int:
    """Conservative estimate (JSON tends to be ~3.5-4 characters per token)."""
    return math.ceil(len(summary.model_dump_json()) / 3.5)


def _strengths(ctx: Context) -> list[Strength]:
    found = []
    for metric in METRICS.values():
        value, dist = ctx.metric_values.get(metric.id), ctx.distribution(metric.id)
        if value is None or dist is None:
            continue
        g = good_percentile(metric, dist, value)
        if g >= STRENGTH_ABOVE:
            found.append((g, Strength(id=f"strong_{metric.id}", label=metric.label,
                                      evidence=metric_evidence(metric, value, dist, g, ctx.static))))
    return [s for _, s in sorted(found, key=lambda x: x[0], reverse=True)[:MAX_STRENGTHS]]


def _hero_context(static: StaticData, hero_id: int, meta_player: dict) -> str:
    hero = static.hero(hero_id)
    abilities = []
    for cls in hero.abilities:
        item = static.item_by_class_name(cls)
        if item:
            abilities.append(f"{item.name} (ultimate)" if item.ability_type == "ultimate" else item.name)
    kept = [i for i in meta_player.get("items") or [] if not i.get("sold_time_s")]
    names = []
    for i in kept:
        item = static.items.get(i["item_id"])
        if item and item.type == "upgrade" and item.name not in names:
            names.append(f"{item.name} (tier {item.tier})" if item.tier else item.name)
    return f"Hero: {hero.name}. Abilities: {', '.join(abilities)}. Items held at the end: {', '.join(names) or 'none'}."


def build_summary(
    meta: dict,
    account_id: int,
    static: StaticData,
    benchmarks: Benchmarks,
    replay: ReplayData | None = None,
    fallback_badge: int | None = None,
) -> MatchSummary:
    """meta = /v1/matches/{id}/metadata match_info. replay = Medium tier data, if any."""
    frame = from_metadata(meta)
    idx = frame["account_id"].to_list().index(account_id) if account_id in frame["account_id"].to_list() else None
    if idx is None:
        raise LookupError(f"account {account_id} did not play in match {meta['match_id']}")
    values = compute_all(frame, static).row(idx, named=True)
    row = frame.slice(idx, 1)
    hero_id = int(row["hero_id"][0])

    badge = row["rank_badge"][0] or (replay.rank_badge if replay else None) or fallback_badge
    bracket = bracket_of(badge, static)
    basis, estimated_level = "own_rank", None
    if bracket is None:
        # Unranked: compare with ranked players who have just better stats.
        est = estimate_level(values, hero_id, static, benchmarks)
        if est is None:
            basis = "all_ranks"
        else:
            bracket, basis = est.compare_with, "estimated_from_stats"
            estimated_level = est.level.label if est.level else f"below {brackets(static)[0].label}"
    ctx = Context(static=static, benchmarks=benchmarks, hero_id=hero_id, bracket=bracket,
                  player=row, metric_values=values, replay=replay)

    meta_player = next(p for p in meta["players"] if p["account_id"] == account_id)
    patch = f"{benchmarks.meta['patch_start'][:10]} (client {static.client_version})"
    return MatchSummary(
        match_id=int(meta["match_id"]),
        patch=patch,
        analysis_level="medium" if replay else "free",
        player=PlayerInfo(
            hero=static.hero_name(hero_id),
            rank_bracket=bracket.label if bracket else "all ranks",
            bracket_basis=basis,
            estimated_level=estimated_level,
            result="win" if row["won"][0] else "loss",
            duration_s=int(meta["duration_s"]),
        ),
        key_stats=KeyStats(
            kda=(int(row["kills"][0]), int(row["deaths"][0]), int(row["assists"][0])),
            souls_per_min=round(values["souls_per_min"], 1),
            damage_per_min=round(values["damage_per_min"], 1),
            last_hits=int(row["last_hits"][0]),
            kill_participation=round(values["kill_participation"], 3) if values["kill_participation"] is not None else None,
            ability_casts=ability_casts(replay, static) if replay else None,
        ),
        issues=run_all(ctx)[:MAX_ISSUES],
        strengths=_strengths(ctx),
        hero_context=_hero_context(static, hero_id, meta_player),
    )


def summary_json(summary: MatchSummary) -> str:
    return json.dumps(summary.model_dump(mode="json", exclude_none=True), indent=1, ensure_ascii=False)


__all__ = ["build_summary", "estimate_tokens", "summary_json", "TOKEN_TARGET"]
