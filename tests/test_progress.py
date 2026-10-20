"""Progress view, focus follow-up and feedback storage (temporary database, no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.analysis import progress as prog


@pytest.fixture
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'p.db').as_posix()}"
    monkeypatch.setenv("COACH_DB_URL", url)
    return db.get_engine(url)


def _analysis(match_id, hero_id, metrics, issues):
    return {"match_id": match_id, "analysis_level": "free",
            "info": {"hero": f"hero{hero_id}", "hero_id": hero_id, "result": "loss", "date": None, "kda": "1/2/3",
                     "bracket": "t05-06", "rank_bracket": "Mystic-Ritualist"},
            "summary_json": {"issues": [{"id": i, "severity": 0.8, "evidence": {"player_value": 2.0, "unit": "deaths"}} for i in issues]},
            "metrics": metrics}


def _report(engine, match_id, account_id, issue_ids, focus="Farm more."):
    with engine.begin() as conn:
        return conn.execute(db.coaching_reports.insert().values(
            match_id=match_id, account_id=account_id, backend="t", model="t", valid=True, summary_json={},
            report_json={"headline": "h", "strengths": [], "next_match_focus": focus,
                         "priorities": [{"title": "t", "why": "w", "how_to_fix": "f", "drill": "d", "issue_ids": issue_ids}]},
        )).inserted_primary_key[0]


class FakeBench:
    meta = {"built_at": 1.0}

    def distribution(self, metric, hero_id, bracket):
        return SimpleNamespace(p50=700.0)


def test_follow_up_tracks_last_focus(engine, monkeypatch):
    _report(engine, 100, 7, ["low_souls_per_min_laning", "died_with_ultimate_ready"])
    data = {100: _analysis(100, 1, {"souls_per_min_0_10": 550.0}, ["low_souls_per_min_laning", "died_with_ultimate_ready"]),
            200: _analysis(200, 2, {"souls_per_min_0_10": 640.0}, [])}
    monkeypatch.setattr(prog, "analyse_match", lambda c, e, s, b, acc, mid, badge: data[mid])
    client = SimpleNamespace(player_rank=lambda acc: SimpleNamespace(badge=0))
    f = prog.follow_up(client, engine, None, FakeBench(), 7, 200)
    assert f["previous_match_id"] == 100 and f["previous_focus"] == "Farm more." and not f["same_hero"]
    farm, ult = f["items"]
    assert (farm["before"], farm["after"], farm["median"], farm["status"]) == (550.0, 640.0, 700.0, "improved")
    assert ult["status"] == "not detected this match"
    assert prog.follow_up(client, engine, None, FakeBench(), 7, 100) is None   # nothing coached before it


def test_progress_counts_recurring_issues(engine, monkeypatch):
    data = {3: _analysis(3, 1, {}, ["a_issue"]), 2: _analysis(2, 1, {}, ["a_issue", "b_issue"]), 1: _analysis(1, 1, {}, ["b_issue"])}
    monkeypatch.setattr(prog, "player_match_ids", lambda c, e, acc: [3, 2, 1])
    monkeypatch.setattr(prog, "analyse_match", lambda c, e, s, b, acc, mid, badge: data[mid])
    client = SimpleNamespace(player_rank=lambda acc: SimpleNamespace(badge=0))
    p = prog.progress(client, engine, None, FakeBench(), 7)
    assert [m["match_id"] for m in p["matches"]] == [1, 2, 3]          # oldest first
    rec = {r["id"]: r for r in p["recurring"]}
    assert rec["a_issue"]["count"] == 2 and rec["a_issue"]["latest_streak"] == 2
    assert rec["b_issue"]["count"] == 2 and rec["b_issue"]["latest_streak"] == 0
    assert all(len(t["values"]) == 3 for t in p["trends"])


def test_feedback_endpoint(engine):
    from backend.app.main import app

    rid = _report(engine, 300, 7, ["x"])
    c = TestClient(app)
    r = c.post("/api/feedback", json={"report_id": rid, "priority_index": 0, "rating": 1, "comment": " good "})
    assert r.json()["feedback"] == {"0": {"rating": 1, "comment": "good"}}
    r = c.post("/api/feedback", json={"report_id": rid, "priority_index": 0, "rating": -1, "comment": ""})
    assert r.json()["feedback"] == {"0": {"rating": -1, "comment": None}}   # replaced, not duplicated
    assert c.post("/api/feedback", json={"report_id": rid, "priority_index": 0}).json()["feedback"] == {}
    assert c.post("/api/feedback", json={"report_id": rid, "priority_index": 0, "rating": 5}).status_code == 400
    assert c.post("/api/feedback", json={"report_id": 99999, "priority_index": 0, "rating": 1}).status_code == 404
