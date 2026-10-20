"""Compare coaching models on the held-out test summaries (Evaluation plan, automatic part).

    python -m ml.eval.run_eval --models local:coach local:coach-base pollinations:deepseek/deepseek-v4.1-flash

Each model is run through the normal coach (validation + one retry) on every summary in
data/ml/dataset/<version>/test_summaries.jsonl. Measured per model:
- schema validity on the first try and after the retry
- grounding: share of reports with no number missing from the summary
- audit: share of reports with no audit findings (backend/coach/audit.py)
- latency, tokens, cost (USD for Claude; GPU time for local; unknown for Pollen-billed)
Writes data/ml/eval/<timestamp>.md (results table + 3 side-by-side example reports) and .json.
Human ratings (usefulness, specificity, pairwise preference) come later, on the same outputs.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from backend import config
from backend.coach.audit import audit, item_names
from backend.coach.base import CoachRefusal
from backend.coach.factory import make_coach
from backend.coach.render import render
from backend.coach.validate import find_ungrounded
from backend.static_data import load
from backend.summarize.contracts import MatchSummary
from ml.dataset.generate import read_jsonl


def evaluate(spec: str, rows: list[dict], names: set[str]) -> dict:
    backend, _, model = spec.partition(":")
    coach = make_coach(backend, model or None)
    per = []
    for i, row in enumerate(rows, 1):
        summary = MatchSummary.model_validate(row["summary"])
        start = time.perf_counter()
        try:
            res = coach.generate(summary)
            err = None
        except (CoachRefusal, RuntimeError) as e:
            res, err = None, str(e)[:200]
        rec = {"id": row["id"], "error": err, "latency_s": round(time.perf_counter() - start, 2)}
        if res is not None:
            rec.update({"valid": res.valid, "first_try": res.valid and len(res.calls) == 1,
                        "calls": len(res.calls), "cost_usd": res.cost_usd,
                        "tokens_out": sum(c.output_tokens or 0 for c in res.calls),
                        "report": res.report.model_dump(mode="json") if res.report else None})
            if res.report:
                rec["ungrounded"] = find_ungrounded(res.report, summary)
                rec["audit"] = audit(summary, res.report, names)
        per.append(rec)
        print(f"  [{spec}] {i}/{len(rows)} valid={rec.get('valid')} {rec['latency_s']}s", flush=True)
    n = len(per)
    ok = [r for r in per if r.get("report")]
    costs = [r["cost_usd"] for r in per if r.get("cost_usd") is not None]
    return {
        "model": spec, "n": n,
        "valid_first_try": sum(1 for r in per if r.get("first_try")) / n,
        "valid_after_retry": sum(1 for r in per if r.get("valid")) / n,
        "grounded": sum(1 for r in ok if not r.get("ungrounded")) / n,
        "audit_clean": sum(1 for r in ok if not r.get("audit")) / n,
        "errors": sum(1 for r in per if r.get("error")),
        "latency_s_mean": sum(r["latency_s"] for r in per) / n,
        "cost_usd_per_report": (sum(costs) / len(costs)) if costs else None,
        "rows": per,
    }


def write_report(results: list[dict], rows: list[dict], out: Path) -> None:
    pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731
    lines = [f"# Coach model comparison ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"{len(rows)} held-out test summaries (never used for training or prompt tuning).", "",
             "| Model | Valid (1st try) | Valid (after retry) | No invented numbers | Audit clean | Errors | Latency | Cost / report |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        cost = "unknown" if r["cost_usd_per_report"] is None else f"${r['cost_usd_per_report']:.5f}"
        lines.append(f"| {r['model']} | {pct(r['valid_first_try'])} | {pct(r['valid_after_retry'])} | {pct(r['grounded'])} | "
                     f"{pct(r['audit_clean'])} | {r['errors']} | {r['latency_s_mean']:.1f}s | {cost} |")
    lines += ["", "## Example reports, side by side", ""]
    by_model = [{x["id"]: x for x in r["rows"]} for r in results]
    examples = [row for row in rows if all(m.get(row["id"], {}).get("report") for m in by_model)][:3]
    for row in examples:
        s = MatchSummary.model_validate(row["summary"])
        lines += [f"### {row['id']}", ""]
        for r, m in zip(results, by_model):
            from backend.coach.base import CoachResult
            from backend.coach.contracts import CoachingReport
            rep = CoachResult(CoachingReport.model_validate(m[row["id"]]["report"]), True)
            lines += [f"**{r['model']}**", "", "```", render(s, rep), "```", ""]
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.eval.run_eval")
    ap.add_argument("--models", nargs="+", required=True, help="backend:model, e.g. local:coach")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    rows = read_jsonl(config.data_dir() / "ml" / "dataset" / args.version / "test_summaries.jsonl")
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no test summaries; run python -m ml.dataset.export first")
        return 1
    names = item_names(load())
    results = [evaluate(spec, rows, names) for spec in args.models]
    out_dir = config.data_dir() / "ml" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out_dir / f"{stamp}.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    write_report(results, rows, out_dir / f"{stamp}.md")
    for r in results:
        print(f"{r['model']}: valid {r['valid_after_retry']:.0%} (1st try {r['valid_first_try']:.0%}), "
              f"grounded {r['grounded']:.0%}, audit-clean {r['audit_clean']:.0%}, {r['latency_s_mean']:.1f}s")
    print(f"report: {out_dir / (stamp + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
