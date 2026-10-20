"""CoachModel: one interface for every model backend.

generate(summary) sends the static system prompt + the summary, validates the
answer (schema, issue ids, no invented numbers) and retries once with the
problems listed. Every call's tokens, cost and latency are recorded.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.coach.contracts import CoachingReport
from backend.coach.prompt import SYSTEM_PROMPT, user_message
from backend.coach.validate import validate
from backend.summarize.contracts import MatchSummary

MAX_ATTEMPTS = 2  # first try + one retry (CLAUDE.md, Phase 3)


@dataclass
class Completion:
    text: str
    stop_reason: str | None
    model: str                   # model that actually answered (can differ after a fallback)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None
    request_id: str | None = None


@dataclass
class CallRecord:
    backend: str
    model: str
    attempt: int
    input_tokens: int | None
    output_tokens: int | None
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: float | None
    latency_s: float
    stop_reason: str | None
    valid: bool
    problems: list[str]
    request_id: str | None = None


@dataclass
class CoachResult:
    report: CoachingReport | None
    valid: bool
    calls: list[CallRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # what was still wrong after the last attempt

    @property
    def cost_usd(self) -> float | None:
        costs = [c.cost_usd for c in self.calls]
        return None if any(c is None for c in costs) else sum(costs)

    @property
    def latency_s(self) -> float:
        return sum(c.latency_s for c in self.calls)


class CoachRefusal(Exception):
    def __init__(self, calls: list[CallRecord], detail: str = ""):
        super().__init__(f"the model declined to answer {detail}".strip())
        self.calls = calls


class CoachModel(ABC):
    backend: str = "base"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str) -> Completion:
        """One model call. Must return the raw answer text and usage."""

    def generate(self, summary: MatchSummary) -> CoachResult:
        summary_json = summary.model_dump_json(exclude_none=True)
        calls: list[CallRecord] = []
        feedback: list[str] | None = None
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            start = time.perf_counter()
            out = self.complete(SYSTEM_PROMPT, user_message(summary_json, feedback))
            latency = time.perf_counter() - start
            if out.stop_reason == "refusal":
                calls.append(self._record(out, attempt, latency, False, ["model declined (refusal)"]))
                raise CoachRefusal(calls)
            last = validate(out.text, summary)
            problems = last.feedback()
            if out.stop_reason == "max_tokens":
                problems.insert(0, "The answer was cut off (max_tokens).")
            ok = last.ok and out.stop_reason != "max_tokens"
            calls.append(self._record(out, attempt, latency, ok, problems))
            if ok:
                return CoachResult(last.report, True, calls)
            feedback = problems
        return CoachResult(last.report if last else None, False, calls, problems=feedback or [])

    def _record(self, out: Completion, attempt: int, latency: float, valid: bool, problems: list[str]) -> CallRecord:
        return CallRecord(self.backend, out.model, attempt, out.input_tokens, out.output_tokens,
                          out.cache_write_tokens, out.cache_read_tokens, out.cost_usd, round(latency, 3),
                          out.stop_reason, valid, problems, out.request_id)
