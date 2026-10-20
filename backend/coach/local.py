"""Local model adapter: any OpenAI-compatible server (Ollama, vLLM).

POST {base_url}/chat/completions with response_format json_schema.
Verified from docs: Ollama serves the OpenAI-compatible API at
http://localhost:11434/v1 and says structured outputs work through
`response_format`; the exact json_schema shape below is the OpenAI standard,
which vLLM documents. Not yet run against a real local server: check this in
Phase 4 when the fine-tuned model is served.

Cost: there is no token price for a local model, so cost = GPU time:
latency x COACH_LOCAL_USD_PER_GPU_HOUR (default 0 = not priced).
"""

from __future__ import annotations

import os

import httpx

from backend.coach.base import Completion, CoachModel
from backend.coach.schema import COACHING_REPORT_SCHEMA

DEFAULT_URL = "http://localhost:11434/v1"
MAX_TOKENS = 4000


class LocalCoach(CoachModel):
    backend = "local"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 http: httpx.Client | None = None, usd_per_gpu_hour: float | None = None):
        self.model = model or os.environ.get("COACH_LOCAL_MODEL", "coach")
        self.base_url = (base_url or os.environ.get("COACH_LOCAL_URL", DEFAULT_URL)).rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))
        self.usd_per_gpu_hour = usd_per_gpu_hour if usd_per_gpu_hour is not None else float(
            os.environ.get("COACH_LOCAL_USD_PER_GPU_HOUR", "0"))

    def complete(self, system: str, user: str) -> Completion:
        import time

        start = time.perf_counter()
        r = self.http.post(f"{self.base_url}/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "CoachingReport", "schema": COACHING_REPORT_SCHEMA, "strict": True}},
            "max_tokens": MAX_TOKENS,
            "temperature": 0.3,
        })
        r.raise_for_status()
        elapsed = time.perf_counter() - start
        body = r.json()
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        finish = choice.get("finish_reason")
        return Completion(
            text=choice["message"].get("content") or "",
            stop_reason="max_tokens" if finish == "length" else finish,
            model=body.get("model", self.model),
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            cost_usd=elapsed * self.usd_per_gpu_hour / 3600,
        )
