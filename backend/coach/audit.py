"""Audit a CoachingReport against its MatchSummary (beyond what validation enforces).

Used by scripts/audit_reports.py (spot-checks) and ml/ (filtering teacher data, evaluation).
Checks: ungrounded numbers, unrounded numbers, a higher-bracket comparison whose text doesn't
name that bracket, unranked headline without the comparison bracket, top two issues not
covered, ability/item names not in hero_context, "use X to ..." explanations, free-tier
reports talking about positioning, ultimate timing or ability casts.
"""

from __future__ import annotations

import re

from backend.coach.contracts import CoachingReport
from backend.coach.validate import find_ungrounded, report_texts
from backend.static_data import StaticData
from backend.summarize.contracts import MatchSummary

EXPLAINS = re.compile(r"\b(?:use|activate|cast|press|pop)\s+([A-Z][\w']+(?:\s[A-Z][\w']+)*)\s+to\b")
FREE_TOPICS = re.compile(r"\b(positioning|position yourself|ultimate (?:ready|available)|ability casts?)\b", re.I)
UNROUNDED = re.compile(r"\b\d+\.\d{2,}\b")


def audit(summary: MatchSummary, report: CoachingReport, item_names: set[str]) -> list[str]:
    problems = []
    text = " ".join(report_texts(report))
    if bad := find_ungrounded(report, summary):
        problems.append(f"ungrounded numbers {bad}")
    if unrounded := UNROUNDED.findall(text):
        problems.append(f"not rounded: {unrounded}")
    issues = {i.id: i for i in summary.issues}
    for p in report.priorities:
        ptext = " ".join([p.title, p.why, p.how_to_fix, p.drill])
        for iid in p.issue_ids:
            scope = str(issues[iid].evidence.details.get("benchmark_scope", "")) if iid in issues else ""
            label = scope.split(", ")[-1] if ", " in scope else ""
            if label and label not in (summary.player.rank_bracket, "all ranks") and label not in ptext:
                problems.append(f"'{p.title}': {iid} is compared with {label}, text doesn't say so")
    if summary.player.bracket_basis == "estimated_from_stats" and summary.player.rank_bracket not in report.headline:
        problems.append(f"headline doesn't name the comparison bracket ({summary.player.rank_bracket})")
    covered = {i for p in report.priorities for i in p.issue_ids}
    if missing := [i.id for i in summary.issues[:2] if i.id not in covered]:
        problems.append(f"top issues not covered: {missing}")
    if foreign := sorted(n for n in item_names if re.search(rf"\b{re.escape(n)}\b", text) and n not in summary.hero_context):
        problems.append(f"names not in hero_context: {foreign}")
    if explains := [m.group(0) for m in EXPLAINS.finditer(text)]:
        problems.append(f"explains abilities/items: {explains}")
    if summary.analysis_level == "free" and (m := FREE_TOPICS.search(text)):
        problems.append(f"free-tier report mentions '{m.group(0)}'")
    return problems


def item_names(static: StaticData) -> set[str]:
    return {i.name for i in static.items.values() if i.type in ("upgrade", "ability") and len(i.name) > 3}
