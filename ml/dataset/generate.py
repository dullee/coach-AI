"""Generate teacher reports for every training input (Phase 4 spike).

    python -m ml.dataset.generate [--backend pollinations] [--model deepseek/deepseek-v4.1-flash] [--workers 4]

Teacher (decided 2026-10-21): DeepSeek V4.1 Flash via Pollinations (quest Pollen; weights
reported MIT-licensed, which allows distillation; verify the Hugging Face licence and
Pollinations' terms before publishing a model trained on this data).

Every input goes through the normal coach (validation + one retry). Results are appended to
data/ml/teacher/<tag>.jsonl as they finish, so the run can be stopped and resumed: inputs that
already have a result are skipped (use --retry-failed to redo the invalid ones).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend import config
from backend.coach.base import CoachRefusal
from backend.coach.factory import BackendUnavailable, make_coach
from backend.coach.prompt import PROMPT_VERSION
from backend.summarize.contracts import MatchSummary

INPUTS = config.data_dir() / "ml" / "inputs.jsonl"
DEFAULT_TEACHER = ("pollinations", "deepseek/deepseek-v4.1-flash")


def teacher_file(backend: str, model: str) -> Path:
    tag = f"{backend}__{model}".replace("/", "_").replace(":", "_")
    return config.data_dir() / "ml" / "teacher" / f"{tag}.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_one(coach, row: dict) -> dict:
    summary = MatchSummary.model_validate(row["summary"])
    base = {"id": row["id"], "source": row["source"], "match_id": row["match_id"],
            "teacher": f"{coach.backend}:{coach.model}", "prompt_version": PROMPT_VERSION}
    try:
        result = coach.generate(summary)
    except CoachRefusal:
        return {**base, "valid": False, "report": None, "problems": ["refused"], "calls": 1, "latency_s": None}
    except Exception as e:  # network/gateway errors: recorded, retried on --retry-failed
        return {**base, "valid": False, "report": None, "problems": [f"error: {e}"[:300]], "calls": 0, "latency_s": None}
    return {**base, "valid": result.valid, "report": result.report.model_dump(mode="json") if result.report else None,
            "problems": result.problems, "calls": len(result.calls), "latency_s": round(result.latency_s, 1),
            "tokens_in": sum(c.input_tokens or 0 for c in result.calls),
            "tokens_out": sum(c.output_tokens or 0 for c in result.calls)}


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.dataset.generate")
    ap.add_argument("--backend", default=DEFAULT_TEACHER[0])
    ap.add_argument("--model", default=DEFAULT_TEACHER[1])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="only this many new inputs (for a quick test)")
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    try:
        coach = make_coach(args.backend, args.model)
    except BackendUnavailable as e:
        print(f"error: {e}")
        return 1
    inputs = read_jsonl(INPUTS)
    if not inputs:
        print(f"no inputs at {INPUTS}; run python -m ml.dataset.inputs first")
        return 1
    out = teacher_file(coach.backend, coach.model)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {r["id"]: r for r in read_jsonl(out) if r.get("prompt_version") == PROMPT_VERSION}
    todo = [r for r in inputs if r["id"] not in done or (args.retry_failed and not done[r["id"]]["valid"])]
    if args.limit:
        todo = todo[: args.limit]
    print(f"teacher {coach.backend}:{coach.model} · {len(done)} already done · {len(todo)} to go · {args.workers} workers")

    lock = threading.Lock()
    start, ok, n = time.perf_counter(), 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, open(out, "a", encoding="utf-8") as fh:
        futures = [pool.submit(run_one, coach, row) for row in todo]
        for fut in as_completed(futures):
            res = fut.result()
            with lock:
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
                ok += res["valid"]
                if n % 10 == 0 or n == len(todo):
                    rate = (time.perf_counter() - start) / n
                    print(f"  {n}/{len(todo)} done, {ok} valid, ~{rate * (len(todo) - n) / 60:.0f} min left", flush=True)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
