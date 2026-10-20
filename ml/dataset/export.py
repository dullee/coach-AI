"""Turn teacher results into the training dataset (Phase 4 spike).

    python -m ml.dataset.export [--teacher-file PATH] [--version v1]

Keeps only reports that passed validation AND the audit (backend/coach/audit.py), splits by
match ID into train / validation / test (80 / 10 / 10, deterministic), and writes chat-format
JSONL ({"messages": [system, user, assistant]}) to data/ml/dataset/<version>/ plus
test_summaries.jsonl for evaluation. The test split is never used for training or prompt tuning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from backend import config
from backend.coach.audit import audit, item_names
from backend.coach.contracts import CoachingReport
from backend.coach.prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from backend.static_data import load
from backend.summarize.contracts import MatchSummary
from ml.dataset.generate import DEFAULT_TEACHER, INPUTS, read_jsonl, teacher_file


def split_of(match_id: int) -> str:
    """80/10/10 by match, stable across runs (all players of a match land in the same split)."""
    h = int(hashlib.sha256(str(match_id).encode()).hexdigest(), 16) % 100
    return "test" if h < 10 else "val" if h < 20 else "train"


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.dataset.export")
    ap.add_argument("--teacher-file", type=Path, default=teacher_file(*DEFAULT_TEACHER))
    ap.add_argument("--version", default="v1")
    args = ap.parse_args()

    inputs = {r["id"]: r for r in read_jsonl(INPUTS)}
    results = {r["id"]: r for r in read_jsonl(args.teacher_file) if r.get("prompt_version") == PROMPT_VERSION}
    names = item_names(load())
    out_dir = config.data_dir() / "ml" / "dataset" / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {s: open(out_dir / f"{s}.jsonl", "w", encoding="utf-8") for s in ("train", "val", "test")}
    test_summaries = open(out_dir / "test_summaries.jsonl", "w", encoding="utf-8")
    kept, dropped = Counter(), Counter()
    for rid, inp in inputs.items():
        split = split_of(inp["match_id"])
        summary = MatchSummary.model_validate(inp["summary"])
        if split == "test":   # every test input is kept for evaluation, whatever the teacher did
            test_summaries.write(json.dumps({"id": rid, "source": inp["source"], "summary": inp["summary"]}, ensure_ascii=False) + "\n")
        res = results.get(rid)
        if res is None:
            dropped["no teacher result"] += 1
            continue
        if not res["valid"] or not res["report"]:
            dropped["invalid"] += 1
            continue
        report = CoachingReport.model_validate(res["report"])
        findings = audit(summary, report, names)
        if findings:
            dropped["audit findings"] += 1
            continue
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message(summary.model_dump_json(exclude_none=True))},
                    {"role": "assistant", "content": json.dumps(res["report"], ensure_ascii=False)}]
        files[split].write(json.dumps({"id": rid, "messages": messages}, ensure_ascii=False) + "\n")
        kept[split] += 1
    for f in (*files.values(), test_summaries):
        f.close()
    manifest = {"version": args.version, "teacher_file": args.teacher_file.name, "prompt_version": PROMPT_VERSION,
                "kept": dict(kept), "dropped": dict(dropped), "inputs": len(inputs)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    # Everything Kaggle needs in one folder: the training script and the Ollama Modelfiles.
    for f in ("train_qlora.py", "Modelfile", "Modelfile.base"):
        shutil.copy2(config.REPO_ROOT / "ml" / "train" / f, out_dir / f)
    print(json.dumps(manifest, indent=1))
    print(f"dataset in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
