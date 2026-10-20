"""Coach layer tests with a fake Claude client (no network, no API key)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from backend import db
from backend.coach.base import CoachRefusal
from backend.coach.claude import FALLBACK_BETA, ClaudeCoach
from backend.coach.local import LocalCoach
from backend.coach.pricing import cost_usd
from backend.coach.prompt import SYSTEM_PROMPT
from backend.coach.render import render
from backend.coach.store import save_result
from backend.coach.validate import find_ungrounded, validate
from backend.coach.contracts import CoachingReport
from backend.summarize.builder import build_summary
from tests.conftest import FIXTURES, fixture_metadata


@pytest.fixture(scope="module")
def summary(static, sample_benchmarks):
    meta = fixture_metadata(FIXTURES[0]["match_id"])
    # Pick a player with at least 2 issues so priorities have something to cite.
    for p in meta["players"]:
        s = build_summary(meta, p["account_id"], static, sample_benchmarks, fallback_badge=55)
        if len(s.issues) >= 2:
            return s
    raise AssertionError("no player with 2+ issues in fixture")


def good_report(s) -> dict:
    i0, i1 = s.issues[0], s.issues[1]
    v = i0.evidence.player_value
    return {
        "headline": "A tough match, but two clear things to work on.",
        "priorities": [
            {"title": "Fix the biggest gap", "why": f"You were at {round(v)} {i0.evidence.unit}.",
             "how_to_fix": "Play safer in lane and keep farming.", "drill": "Watch the minimap every few seconds.",
             "issue_ids": [i0.id]},
            {"title": "Second thing", "why": "This also held you back.", "how_to_fix": "Group with your team.",
             "drill": "Stay near a teammate after 10 minutes.", "issue_ids": [i1.id]},
        ],
        "strengths": [],
        "next_match_focus": "Focus on the first priority.",
    }


class FakeMessages:
    def __init__(self, answers, stop="end_turn", model="claude-haiku-5-5"):
        self.answers = list(answers)
        self.calls = []
        self.stop = stop
        self.model = model

    def create(self, **kw):
        self.calls.append(kw)
        text = self.answers.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop, model=self.model, _request_id="req_test",
            usage=SimpleNamespace(input_tokens=900, output_tokens=700, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=1100),
        )


def fake_client(answers, **kw):
    msgs = FakeMessages(answers, **kw)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs)), msgs


# ---- request shape ------------------------------------------------------------------

def test_request_uses_cache_structured_output_and_effort(summary):
    client, msgs = fake_client([json.dumps(good_report(summary))])
    result = ClaudeCoach(model="claude-haiku-5-5", client=client).generate(summary)
    assert result.valid and len(result.calls) == 1
    kw = msgs.calls[0]
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["system"][0]["text"] == SYSTEM_PROMPT and kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "medium"
    assert kw["thinking"] == {"type": "adaptive"}
    assert "fallbacks" not in kw and "betas" not in kw   # Haiku: no server-side fallback
    assert summary.model_dump_json(exclude_none=True) in kw["messages"][0]["content"]


def test_opus_opts_into_server_side_fallback(summary):
    client, msgs = fake_client([json.dumps(good_report(summary))], model="claude-opus-5-5")
    ClaudeCoach(model="claude-opus-5-5", client=client).generate(summary)
    assert msgs.calls[0]["fallbacks"] == "default" and msgs.calls[0]["betas"] == [FALLBACK_BETA]


def test_system_prompt_is_static_and_long_enough_to_cache():
    import re

    import backend.coach.prompt as prompt_module
    # No dates, times or unfilled template fields: any per-request text would break the cache.
    assert not re.search(r"20\d\d-\d\d-\d\d|\{[a-z_]+\}", SYSTEM_PROMPT)
    assert prompt_module.SYSTEM_PROMPT is SYSTEM_PROMPT  # a module constant, built once
    # ~4 characters per token: the 512-token cache minimum needs ~2,000+ characters.
    assert len(SYSTEM_PROMPT) > 3000


# ---- validation and retry ----------------------------------------------------------------

def test_invalid_json_is_retried_once_with_feedback(summary):
    client, msgs = fake_client(["not json", json.dumps(good_report(summary))])
    result = ClaudeCoach(client=client).generate(summary)
    assert result.valid and len(result.calls) == 2
    assert not result.calls[0].valid and result.calls[1].valid
    assert "rejected" in msgs.calls[1]["messages"][0]["content"]
    assert result.calls[0].cost_usd == pytest.approx(cost_usd("claude-haiku-5-5", 900, 700, 0, 1100))


def test_invented_number_triggers_retry_and_is_reported(summary):
    bad = good_report(summary)
    bad["priorities"][0]["why"] = "You were 987654 souls behind."
    client, msgs = fake_client([json.dumps(bad), json.dumps(bad)])
    result = ClaudeCoach(client=client).generate(summary)
    assert not result.valid and len(result.calls) == 2
    assert any("987654" in p for p in result.problems)
    assert "987654" in msgs.calls[1]["messages"][0]["content"]  # feedback names the number


def test_unknown_issue_id_is_rejected(summary):
    bad = good_report(summary)
    bad["priorities"][0]["issue_ids"] = ["made_up_issue"]
    v = validate(json.dumps(bad), summary)
    assert not v.ok and any("made_up_issue" in e for e in v.errors)


def test_refusal_raises_with_call_record(summary):
    client, _ = fake_client(["{}"], stop="refusal")
    with pytest.raises(CoachRefusal) as err:
        ClaudeCoach(client=client).generate(summary)
    assert len(err.value.calls) == 1 and err.value.calls[0].stop_reason == "refusal"


# ---- grounding rules ------------------------------------------------------------------------

def _report_with(summary, text):
    r = good_report(summary)
    r["priorities"][1]["why"] = text
    return CoachingReport.model_validate(r)


def test_grounding_allows_rounding_percentages_and_times(summary):
    i = summary.issues[0]
    e = i.evidence
    texts = [f"{round(e.player_value)} {e.unit}", f"about {e.player_value:.1f}"]
    if e.percentile is not None:
        texts.append(f"better than {round(e.percentile * 100)}% of players")
    texts.append(f"a {summary.player.duration_s // 60}:{summary.player.duration_s % 60:02d} match")
    texts.append(f"{round(summary.player.duration_s / 60)} minutes")
    texts.append("your 3 priorities")
    for t in texts:
        assert find_ungrounded(_report_with(summary, t), summary) == [], t


def test_grounding_rejects_computed_numbers(summary):
    e = summary.issues[0].evidence
    if e.bracket_p50 is None:
        pytest.skip("first issue has no bracket median")
    diff = abs(e.bracket_p50 - e.player_value)
    if any(abs(diff - n) <= max(0.5, 0.01 * n) for n in [e.player_value, e.bracket_p50]):
        pytest.skip("difference happens to equal a summary number")
    bad = find_ungrounded(_report_with(summary, f"{diff:.0f} less than the median"), summary)
    assert bad and find_ungrounded(_report_with(summary, "at 99:59"), summary) == ["99:59"]


# ---- storage, rendering, local backend -----------------------------------------------------------

def test_results_and_calls_are_logged(summary, tmp_path):
    engine = db.get_engine(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    client, _ = fake_client(["oops", json.dumps(good_report(summary))])
    coach = ClaudeCoach(client=client)
    result = coach.generate(summary)
    rid = save_result(engine, coach, summary, 1001, result)
    with engine.connect() as conn:
        calls = conn.execute(db.coach_calls.select().where(db.coach_calls.c.report_id == rid)).mappings().all()
        rep = conn.execute(db.coaching_reports.select().where(db.coaching_reports.c.id == rid)).mappings().one()
    assert len(calls) == 2 and [c["attempt"] for c in calls] == [1, 2]
    assert all(c["cost_usd"] > 0 and c["input_tokens"] == 900 for c in calls)
    assert rep["valid"] and rep["report_json"]["headline"] and rep["summary_json"]["match_id"] == summary.match_id
    assert rep["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls))
    assert "1." in render(summary, result)


def test_local_backend_speaks_openai_chat_completions(summary):
    seen = []

    def server(req):
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={
            "model": body["model"],
            "choices": [{"message": {"content": json.dumps(good_report(summary))}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1800, "completion_tokens": 600},
        })

    coach = LocalCoach(model="qwen-coach", base_url="http://local/v1",
                       http=httpx.Client(transport=httpx.MockTransport(server)), usd_per_gpu_hour=0.5)
    result = coach.generate(summary)
    assert result.valid and result.calls[0].input_tokens == 1800
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert result.calls[0].cost_usd >= 0


def _gemini(summary, handler):
    from backend.coach.local import GEMINI_DEFAULT_MODEL, GEMINI_URL, GeminiCoach

    coach = GeminiCoach(api_key="test-key", http=httpx.Client(transport=httpx.MockTransport(handler)))
    coach._sleep = lambda s: None
    return coach, GEMINI_URL, GEMINI_DEFAULT_MODEL


def _ok(summary, body):
    return httpx.Response(200, json={
        "model": body["model"],
        "choices": [{"message": {"content": json.dumps(good_report(summary))}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 900},
    })


def test_gemini_backend_sends_key_and_schema(summary):
    seen = []

    def server(req):
        seen.append(req)
        return _ok(summary, json.loads(req.content))

    coach, url, model = _gemini(summary, server)
    result = coach.generate(summary)
    assert result.valid and result.cost_usd == 0.0 and coach.backend == "gemini"
    req = seen[0]
    assert str(req.url) == f"{url}/chat/completions"
    assert req.headers["authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == model and body["response_format"]["type"] == "json_schema"


def test_gemini_falls_back_to_json_mode_if_schema_rejected(summary):
    formats = []

    def server(req):
        body = json.loads(req.content)
        formats.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "Invalid JSON schema"}})
        return _ok(summary, body)

    coach, _, _ = _gemini(summary, server)
    assert coach.generate(summary).valid
    assert formats == ["json_schema", "json_object"]


def test_gemini_retries_rate_limits(summary):
    replies = iter([httpx.Response(429, headers={"retry-after": "3"}), None])

    def server(req):
        r = next(replies)
        return r if r is not None else _ok(summary, json.loads(req.content))

    coach, _, _ = _gemini(summary, server)
    waits = []
    coach._sleep = waits.append
    assert coach.generate(summary).valid and waits == [3.0]


def test_backend_factory_reports_missing_keys(monkeypatch):
    from backend.coach.factory import BackendUnavailable, make_coach

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable, match="GEMINI_API_KEY"):
        make_coach("gemini")
    with pytest.raises(BackendUnavailable, match="unknown backend"):
        make_coach("nope")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert make_coach("gemini").backend == "gemini"
    assert make_coach("local").backend == "local"


def test_pricing():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert cost_usd("unknown-model", 1, 1) is None
