"""Fine-tuning pipeline plumbing with fake models (no network, no GPU)."""

from __future__ import annotations

import json
import sys

import polars as pl
import pytest

from backend.coach.base import CoachModel, Completion
from backend.coach.prompt import SYSTEM_PROMPT
from backend.summarize.builder import build_summary
from ml.dataset import export as export_mod
from ml.dataset import generate as gen_mod
from ml.dataset.inputs import metadata_from_lake
from tests.conftest import LAKE_SAMPLE


class FakeCoach(CoachModel):
    """Writes a minimal valid report that cites the first issue."""
    backend, model = "fake", "teacher-1"

    def complete(self, system, user):
        s = json.loads(user.split("MatchSummary:\n", 1)[1].split("\n\nYour previous")[0])
        ids = [i["id"] for i in s["issues"]][:1] or ["key_stats"]
        report = {"headline": "Solid effort.", "priorities": [{"title": "Focus", "why": "It matters.",
                  "how_to_fix": "Practice.", "drill": "Repeat.", "issue_ids": ids}],
                  "strengths": [], "next_match_focus": "Keep going."}
        return Completion(json.dumps(report), "stop", self.model, 100, 50, cost_usd=0.0)


def test_metadata_from_lake_builds_valid_summaries(static, sample_benchmarks):
    df = pl.read_parquet(LAKE_SAMPLE)
    match_id = df.group_by("match_id").len().filter(pl.col("len") == 12)["match_id"][0]
    rows = df.filter(pl.col("match_id") == match_id).to_dicts()
    meta = metadata_from_lake(rows)
    assert len(meta["players"]) == 12 and meta["winning_team"] in (0, 1)
    s = build_summary(meta, rows[0]["account_id"], static, sample_benchmarks)
    assert s.match_id == match_id and "Items bought during the match" in s.hero_context
    assert list(s.key_stats.kda) == [rows[0]["kills"], rows[0]["deaths"], rows[0]["assists"]]


@pytest.fixture
def ml_dir(tmp_path, monkeypatch, static, sample_benchmarks):
    df = pl.read_parquet(LAKE_SAMPLE)
    matches = df.group_by("match_id").len().filter(pl.col("len") == 12)["match_id"].to_list()[:30]
    inputs = []
    for m in matches:
        rows = df.filter(pl.col("match_id") == m).to_dicts()
        s = build_summary(metadata_from_lake(rows), rows[0]["account_id"], static, sample_benchmarks)
        inputs.append({"id": f"lake-{m}", "source": "lake", "match_id": m, "summary": s.model_dump(mode="json", exclude_none=True)})
    inp = tmp_path / "inputs.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in inputs), encoding="utf-8")
    monkeypatch.setattr(gen_mod, "INPUTS", inp)
    monkeypatch.setattr(export_mod, "INPUTS", inp)
    monkeypatch.setenv("COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gen_mod, "make_coach", lambda b, m: FakeCoach())
    monkeypatch.setattr(export_mod, "load", lambda: static)   # temp data dir has no static data
    return tmp_path, inputs


def test_generate_export_and_eval(ml_dir, monkeypatch):
    tmp, inputs = ml_dir
    monkeypatch.setattr(sys, "argv", ["generate", "--workers", "2", "--limit", "20"])
    assert gen_mod.main() == 0
    teacher = gen_mod.teacher_file("fake", "teacher-1")
    assert len(gen_mod.read_jsonl(teacher)) == 20
    monkeypatch.setattr(sys, "argv", ["generate", "--workers", "2"])           # resume: only the rest
    assert gen_mod.main() == 0
    results = gen_mod.read_jsonl(teacher)
    assert len(results) == 30 and len({r["id"] for r in results}) == 30 and all(r["valid"] for r in results)

    monkeypatch.setattr(sys, "argv", ["export", "--teacher-file", str(teacher), "--version", "t"])
    assert export_mod.main() == 0
    out = tmp / "ml" / "dataset" / "t"
    manifest = json.loads((out / "manifest.json").read_text())
    assert sum(manifest["kept"].values()) + sum(manifest["dropped"].values()) == 30
    ex = json.loads((out / "train.jsonl").read_text().splitlines()[0])
    roles = [m["role"] for m in ex["messages"]]
    assert roles == ["system", "user", "assistant"] and ex["messages"][0]["content"] == SYSTEM_PROMPT
    assert json.loads(ex["messages"][2]["content"])["headline"] == "Solid effort."
    for f in ("train_qlora.py", "Modelfile", "Modelfile.base"):
        assert (out / f).exists()
    # Splits are by match and deterministic; test inputs are all listed for evaluation.
    test_ids = {json.loads(line)["id"] for line in (out / "test_summaries.jsonl").read_text().splitlines()}
    assert test_ids == {r["id"] for r in inputs if export_mod.split_of(r["match_id"]) == "test"}

    from ml.eval import run_eval
    monkeypatch.setattr(run_eval, "make_coach", lambda b, m: FakeCoach())
    rows = [{"id": r["id"], "summary": r["summary"]} for r in inputs[:5]]
    res = run_eval.evaluate("fake:teacher-1", rows, set())
    assert res["n"] == 5 and res["valid_after_retry"] == 1.0 and res["grounded"] == 1.0
    run_eval.write_report([res], rows, tmp / "eval.md")
    assert "| fake:teacher-1 | 100% |" in (tmp / "eval.md").read_text()


def test_split_is_stable():
    assert {export_mod.split_of(m) for m in range(1000)} == {"train", "val", "test"}
    assert export_mod.split_of(123456) == export_mod.split_of(123456)
    share = sum(export_mod.split_of(m) == "test" for m in range(100000, 110000)) / 10000
    assert 0.08 < share < 0.12


def test_model_output_with_empty_think_block_validates(static, sample_benchmarks):
    from backend.coach.validate import validate
    from tests.conftest import fixture_metadata

    meta = fixture_metadata(120849236)
    s = build_summary(meta, meta["players"][0]["account_id"], static, sample_benchmarks)
    text = FakeCoach().complete("", "MatchSummary:\n" + s.model_dump_json(exclude_none=True)).text
    assert validate("<think>\n\n</think>\n\n" + text, s).ok
