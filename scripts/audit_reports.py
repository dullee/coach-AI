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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from backend.coach.contracts import CoachingReport  # noqa: E402
from backend.coach.prompt import PROMPT_VERSION  # noqa: E402
from backend.coach.audit import audit, item_names  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.static_data import load  # noqa: E402
from backend.summarize.contracts import MatchSummary  # noqa: E402

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend")
    ap.add_argument("--model")
    ap.add_argument("--all-versions", action="store_true", help=f"include reports from older prompts (current: {PROMPT_VERSION})")
    args = ap.parse_args()
    static = load()
    names = item_names(static)
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
        problems = audit(s, CoachingReport.model_validate(r["report_json"]), names)
        clean += not problems
        print(f"{match_id} {hero:14s} [{r['backend']}:{r['model']}] " + ("OK" if not problems else " | ".join(problems)))
    print(f"\n{clean}/{len(latest)} reports with no audit findings (prompt {'any' if args.all_versions else PROMPT_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
