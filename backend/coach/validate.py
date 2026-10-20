"""Check a model's answer: valid CoachingReport JSON, known issue ids, no invented numbers.

Grounding rule (CLAUDE.md): every number in the report must come from the
MatchSummary. Allowed transformations of a summary number v:
- rounding (relative tolerance 1%, absolute 0.5);
- shares (0..1) written as percentages;
- seconds written as minutes (m:ss, or whole/decimal minutes).
Small counts 0-3 are always allowed ("3 priorities", "one of 2 deaths" read
as digits), and numbers inside identifiers are ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from backend.coach.contracts import CoachingReport
from backend.summarize.contracts import MatchSummary

ALWAYS_ALLOWED = {0.0, 1.0, 2.0, 3.0}
MMSS = re.compile(r"\b(\d{1,3}):([0-5]\d)\b")
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")


@dataclass
class Validation:
    report: CoachingReport | None
    errors: list[str] = field(default_factory=list)       # schema/id problems -> must retry
    ungrounded: list[str] = field(default_factory=list)   # numbers not in the summary

    @property
    def ok(self) -> bool:
        return self.report is not None and not self.errors and not self.ungrounded

    def feedback(self) -> list[str]:
        out = list(self.errors)
        if self.ungrounded:
            out.append("These numbers are not in the MatchSummary (copy numbers from it, rounded at most; "
                       "never calculate new ones): " + ", ".join(self.ungrounded))
        return out


def _summary_numbers(obj) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out += [float(m.replace(",", "")) for m in NUMBER.findall(obj)]
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _summary_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += _summary_numbers(v)
    return out


def _forms(values: list[float]) -> tuple[set[float], set[float]]:
    """(plain number forms, second counts usable for m:ss)."""
    plain, seconds = set(ALWAYS_ALLOWED), set()
    for v in values:
        plain.add(v)
        if 0 <= v <= 1:
            plain.add(v * 100)
        if v >= 60:
            seconds.add(v)
            plain.add(v / 60)
        if v.is_integer() and 0 < v <= 90:
            seconds.add(v * 60)   # a minute value written as m:ss ("0-10 min" -> "by 10:00")
    return plain, seconds


def _close(x: float, candidates: set[float]) -> bool:
    return any(abs(x - c) <= max(0.5, 0.01 * abs(c)) for c in candidates)


def report_texts(report: CoachingReport) -> list[str]:
    texts = [report.headline, report.next_match_focus, *report.strengths]
    for p in report.priorities:
        texts += [p.title, p.why, p.how_to_fix, p.drill]
    return texts


def find_ungrounded(report: CoachingReport, summary: MatchSummary) -> list[str]:
    data = summary.model_dump(mode="json")
    plain, seconds = _forms(_summary_numbers(data))
    ids = sorted({i.id for i in summary.issues} | {s.id for s in summary.strengths}, key=len, reverse=True)
    bad: list[str] = []
    for text in report_texts(report):
        for i in ids:
            text = text.replace(i, " ")
        for m in MMSS.finditer(text):
            secs = int(m.group(1)) * 60 + int(m.group(2))
            if not any(abs(secs - s) <= 1 for s in seconds):
                bad.append(m.group(0))
        text = MMSS.sub(" ", text)
        for raw in NUMBER.findall(text):
            if not _close(float(raw.replace(",", "")), plain):
                bad.append(raw)
    return sorted(set(bad), key=bad.index)


def validate(text: str, summary: MatchSummary) -> Validation:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        return Validation(None, [f"The answer was not valid JSON ({e})."])
    try:
        report = CoachingReport.model_validate(data)
    except ValidationError as e:
        return Validation(None, [f"The answer does not match the CoachingReport schema: {e.errors()[:3]}"])

    errors = []
    issue_ids = {i.id for i in summary.issues}
    allowed = issue_ids | {s.id for s in summary.strengths} | {"key_stats"}
    for n, p in enumerate(report.priorities, 1):
        unknown = [i for i in p.issue_ids if i not in allowed]
        if unknown:
            errors.append(f"Priority {n} lists issue ids that are not in the summary: {unknown}.")
        if issue_ids and not set(p.issue_ids) & issue_ids:
            errors.append(f"Priority {n} must list at least one id from issues.")
    if issue_ids and len(report.priorities) > min(3, len(issue_ids)):
        errors.append(f"Write at most {min(3, len(issue_ids))} priorities.")
    return Validation(report, errors, find_ungrounded(report, summary))
