"""Plain-text rendering of a CoachingReport for the terminal."""

from __future__ import annotations

import textwrap

from backend.coach.base import CoachResult
from backend.summarize.contracts import MatchSummary


def _wrap(label: str, text: str) -> str:
    return textwrap.fill(text, width=100, initial_indent=f"   {label}", subsequent_indent=" " * (3 + len(label)))


def render(summary: MatchSummary, result: CoachResult) -> str:
    r = result.report
    p = summary.player
    k, d, a = summary.key_stats.kda
    lines = [f"Match {summary.match_id}: {p.hero}, {p.result}, {p.duration_s // 60}:{p.duration_s % 60:02d} "
             f"| KDA {k}/{d}/{a} | compared with {p.rank_bracket}", ""]
    if r is None:
        return "\n".join(lines + ["No usable report: " + "; ".join(result.problems)])
    lines += [textwrap.fill(r.headline, 100), ""]
    for n, pr in enumerate(r.priorities, 1):
        lines += [f"{n}. {pr.title}", _wrap("Why: ", pr.why), _wrap("Fix: ", pr.how_to_fix),
                  _wrap("Drill: ", pr.drill), ""]
    if r.strengths:
        lines.append("Strengths:")
        lines += [textwrap.fill(s, 100, initial_indent="   - ", subsequent_indent="     ") for s in r.strengths]
        lines.append("")
    lines.append(textwrap.fill("Next match: " + r.next_match_focus, 100))
    if not result.valid:
        lines += ["", "WARNING, report failed validation: " + "; ".join(result.problems)]
    return "\n".join(lines)
