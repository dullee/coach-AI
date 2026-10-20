"""late_core_items: core build finished later than higher-rank players on the same hero.

Measure: time of the player's third tier-3+ item, compared with the
distribution for the same hero one bracket up (the top bracket compares with
itself). Same trigger and severity as the other benchmark detectors (bottom
35%), so it fires at a comparable rate.

Evidence details also name the item that came latest relative to the
higher-rank median for that item, which helps the coach be specific.
"""

from __future__ import annotations

from backend.analysis.brackets import next_bracket_up
from backend.analysis.issues.base import ISSUE_BELOW, Context, Detector, benchmark_severity, metric_evidence
from backend.analysis.metrics import METRICS
from backend.analysis.metrics.items import big_item_ids, first_purchases
from backend.summarize.contracts import Issue

METRIC_ID = "third_big_item_s"


def _slowest_item(ctx: Context, bracket_id: str | None) -> dict | None:
    worst = None
    for item_id, t in first_purchases(ctx.player, big_item_ids(ctx.static)).select("item_id", "t").iter_rows():
        bench = ctx.benchmarks.item_timing(ctx.hero_id, bracket_id, item_id)
        if bench and (worst is None or t / bench.p50 > worst["ratio"]):
            worst = {"item_id": item_id, "t": float(t), "p50": bench.p50, "ratio": t / bench.p50}
    return worst


def _run(ctx: Context) -> Issue | None:
    metric = METRICS[METRIC_ID]
    value = ctx.metric_values.get(METRIC_ID)
    target = next_bracket_up(ctx.bracket, ctx.static)
    target_id = target.id if target else None
    dist = ctx.benchmarks.distribution(METRIC_ID, ctx.hero_id, target_id)
    if value is None or dist is None:
        return None
    good = 1 - dist.percentile(value)
    if good >= ISSUE_BELOW:
        return None
    details: dict = {"compared_with": f"same hero, {target.label if target else 'all ranks'}"}
    slow = _slowest_item(ctx, target_id)
    if slow:
        item = ctx.static.items.get(slow["item_id"])
        details.update({"slowest_item": item.name if item else str(slow["item_id"]),
                        "slowest_item_bought_s": slow["t"], "slowest_item_higher_rank_p50_s": round(slow["p50"], 1)})
    return Issue(
        id="late_core_items", category="items", severity=benchmark_severity(good),
        evidence=metric_evidence(metric, value, dist, good, **details),
        timestamps_s=[value], hero=ctx.hero_name,
    )


DETECTORS = [Detector("late_core_items", "items", False, _run,
                      "Third big item finished later than for higher-rank players on this hero.")]
