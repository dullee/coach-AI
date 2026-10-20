"""Rule-based mistake detectors. run_all() returns Issues sorted by severity."""

from __future__ import annotations

from backend.analysis.issues import benchmark, items, replay
from backend.analysis.issues.base import Context, Detector
from backend.summarize.contracts import Issue

DETECTORS: list[Detector] = [*benchmark.DETECTORS, *items.DETECTORS, *replay.DETECTORS]


BY_ID: dict[str, Detector] = {d.id: d for d in DETECTORS}


def issue_label(issue_id: str) -> str:
    d = BY_ID.get(issue_id)
    return (d.label or d.doc) if d else issue_id


def run_all(ctx: Context) -> list[Issue]:
    found = []
    for d in DETECTORS:
        if d.needs_replay and ctx.replay is None:
            continue
        issue = d.run(ctx)
        if issue is not None:
            found.append(issue)
    return sorted(found, key=lambda i: i.severity, reverse=True)


__all__ = ["BY_ID", "DETECTORS", "Context", "Detector", "issue_label", "run_all"]
