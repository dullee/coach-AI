"""Rule-based mistake detectors. run_all() returns Issues sorted by severity."""

from __future__ import annotations

from backend.analysis.issues import benchmark, items, replay
from backend.analysis.issues.base import Context, Detector
from backend.summarize.contracts import Issue

DETECTORS: list[Detector] = [*benchmark.DETECTORS, *items.DETECTORS, *replay.DETECTORS]


def run_all(ctx: Context) -> list[Issue]:
    found = []
    for d in DETECTORS:
        if d.needs_replay and ctx.replay is None:
            continue
        issue = d.run(ctx)
        if issue is not None:
            found.append(issue)
    return sorted(found, key=lambda i: i.severity, reverse=True)


__all__ = ["DETECTORS", "Context", "Detector", "run_all"]
