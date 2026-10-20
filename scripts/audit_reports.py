"""Audit stored coaching reports against their MatchSummaries (spot-check helper).

    python scripts/audit_reports.py [--backend gemini] [--model M] [--all-versions]

Checks every latest report per (match, hero), beyond what validation enforces:
- ungrounded numbers (same rule as coach/validate.py)
- numbers with 2+ decimals (not rounded for readability)
- issues compared with another bracket (e.g. late_core_items, one bracket up)
  whose priority text doesn't name that bracket
- unranked players: headline doesn't name the comparison bracket
- top two issues not covered by any priority
- ability/item names that aren't in hero_context; "use X to ..." explanations
- free-tier reports talking about positioning, ultimate timing or ability casts
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from backend.coach.contracts import CoachingReport  # noqa: E402
from backend.coach.prompt import PROMPT_VERSION  # noqa: E402
from backend.coach.validate import find_ungrounded, report_texts  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.static_data import load  # noqa: E402
from backend.summarize.contracts import MatchSummary  # noqa: E402

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend")
    ap.add_argument("--model")
    ap.add_argument("--all-versions", action="store_true", help=f"include reports from older prompts (current: {PROMPT_VERSION})")
    args = ap.parse_args()
    static = load()
    item_names = {i.name for i in static.items.values() if i.type in ("upgrade", "ability") and len(i.name) > 3}
    t = db.coaching_reports
    q = t.select().where(t.c.valid).order_by(t.c.id)
    if args.backend:
        q = q.where(t.c.backend == args.backend)
    if args.model:
        q = q.where(t.c.model == args.model)
    if not args.all_versions:
        q = q.where(t.c.prompt_version == PROMPT_VERSION)
    with get_engine().connect() as conn:
        rows = conn.execute(q).mappings().all()
    latest = {(r["match_id"], r["summary_json"]["player"]["hero"]): r for r in rows}
    clean = 0
    for (match_id, hero), r in latest.items():
        s = MatchSummary.model_validate(r["summary_json"])
        problems = audit(s, CoachingReport.model_validate(r["report_json"]), item_names)
        clean += not problems
        print(f"{match_id} {hero:14s} [{r['backend']}:{r['model']}] " + ("OK" if not problems else " | ".join(problems)))
    print(f"\n{clean}/{len(latest)} reports with no audit findings (prompt {'any' if args.all_versions else PROMPT_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
