from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.coach.contracts import CoachingReport
from backend.summarize.builder import TOKEN_TARGET, build_summary, estimate_tokens, summary_json
from backend.summarize.contracts import MatchSummary
from tests.conftest import FIXTURES, fixture_metadata

MATCH_IDS = [f["match_id"] for f in FIXTURES]


@pytest.mark.parametrize("match_id", MATCH_IDS)
def test_free_summary_for_every_player(match_id, static, sample_benchmarks):
    meta = fixture_metadata(match_id)
    for p in meta["players"]:
        s = build_summary(meta, p["account_id"], static, sample_benchmarks, fallback_badge=55)
        assert isinstance(s, MatchSummary) and s.analysis_level == "free"
        assert estimate_tokens(s) < TOKEN_TARGET
        assert s.player.hero == static.hero_name(p["hero_id"])
        assert list(s.key_stats.kda) == [p["kills"], p["deaths"], p["assists"]]
        assert [i.severity for i in s.issues] == sorted((i.severity for i in s.issues), reverse=True)
        assert len(s.issues) <= 8 and len(s.strengths) <= 3
        assert s.key_stats.ability_casts is None
        assert all(st.evidence.percentile >= 0.7 for st in s.strengths)
        assert static.hero_name(p["hero_id"]) in s.hero_context
        MatchSummary.model_validate_json(summary_json(s))  # round-trips through JSON


@pytest.mark.replay
def test_medium_summary_uses_the_replay(static, sample_benchmarks, tmp_path):
    from backend import db
    from backend.analysis.replay_data import ReplayData
    from backend.parse import parse_demo, save_match
    from tests.conftest import require_replay

    # The ranked fixture: its replay carries every player's rank (unranked replays have rank 0).
    entry = next(f for f in FIXTURES if f["match_id"] == 120851860)
    path = require_replay(entry)
    engine = db.get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    save_match(parse_demo(path), engine, tmp_path)
    meta = fixture_metadata(entry["match_id"])
    for p in meta["players"]:
        p["player_rank_data"] = None  # force the rank to come from the replay
    for p in meta["players"][:4]:
        replay = ReplayData.load(entry["match_id"], p["hero_id"], engine, tmp_path)
        assert replay.rank_badge
        s = build_summary(meta, p["account_id"], static, sample_benchmarks, replay=replay)
        assert s.analysis_level == "medium"
        assert s.key_stats.ability_casts and all(v > 0 for v in s.key_stats.ability_casts.values())
        assert "unranked" not in s.player.rank_bracket
        assert estimate_tokens(s) < TOKEN_TARGET


def test_unranked_without_fallback_says_so(static, sample_benchmarks):
    meta = fixture_metadata(MATCH_IDS[0])
    s = build_summary(meta, meta["players"][0]["account_id"], static, sample_benchmarks)
    assert "unranked" in s.player.rank_bracket


def test_unknown_account_is_rejected(static, sample_benchmarks):
    with pytest.raises(LookupError):
        build_summary(fixture_metadata(MATCH_IDS[0]), 42, static, sample_benchmarks)


def test_summary_json_has_no_nulls():
    s = MatchSummary.model_validate({
        "match_id": 1, "patch": "x", "analysis_level": "free",
        "player": {"hero": "h", "rank_bracket": "b", "result": "win", "duration_s": 1},
        "key_stats": {"kda": [1, 2, 3], "souls_per_min": 1, "damage_per_min": 1, "last_hits": 1},
        "issues": [], "strengths": [], "hero_context": "c",
    })
    assert "null" not in summary_json(s)


def test_coaching_report_contract():
    good = {"headline": "h", "priorities": [{"title": "t", "why": "w", "how_to_fix": "f", "drill": "d", "issue_ids": ["x"]}],
            "strengths": [], "next_match_focus": "n"}
    CoachingReport.model_validate(good)
    for bad in (
        {**good, "priorities": []},
        {**good, "priorities": [{**good["priorities"][0], "issue_ids": []}]},
        {**good, "extra": 1},
    ):
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(bad)
    assert json.loads(CoachingReport.model_validate(good).model_dump_json())["headline"] == "h"
