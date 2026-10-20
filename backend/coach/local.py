"""OpenAI-compatible chat-completions adapters: local servers (Ollama, vLLM) and Gemini.

POST {base_url}/chat/completions with response_format json_schema.
- Local: Ollama serves the OpenAI-compatible API at http://localhost:11434/v1
  and says structured outputs work through `response_format`; vLLM documents
  the json_schema shape. Not yet run against a real local server (Phase 4).
- Gemini: Google's OpenAI-compatible endpoint
  https://generativelanguage.googleapis.com/v1beta/openai/ with the key as a
  Bearer token; its docs show structured output through response_format.
  Free tier available (Google may use free-tier data to improve its products).

If a server rejects the JSON schema (HTTP 400), the adapter falls back to plain
JSON mode once; coach/validate.py checks the result either way.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from backend.coach.base import Completion, CoachModel
from backend.coach.schema import COACHING_REPORT_SCHEMA

log = logging.getLogger(__name__)


class OpenAICompatCoach(CoachModel):
    backend = "openai-compat"
    max_tokens = 4000

    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 http: httpx.Client | None = None, usd_per_gpu_hour: float = 0.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))
        self.headers = headers
        self.usd_per_gpu_hour = usd_per_gpu_hour
        self.schema_mode = True   # switched off if the server rejects json_schema

    def _response_format(self) -> dict:
        if self.schema_mode:
            return {"type": "json_schema",
                    "json_schema": {"name": "CoachingReport", "schema": COACHING_REPORT_SCHEMA, "strict": True}}
        return {"type": "json_object"}

    max_retries = 4          # on 429 / 5xx (free tiers have low per-minute limits)
    max_wait_s = 65.0

    def _post(self, system: str, user: str) -> httpx.Response:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": self._response_format(),
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
        }
        for attempt in range(self.max_retries + 1):
            r = self.http.post(f"{self.base_url}/chat/completions", headers=self.headers, json=body)
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                return r
            try:
                wait = float(r.headers.get("retry-after", ""))
            except ValueError:
                wait = 5.0 * 2 ** attempt
            wait = min(wait, self.max_wait_s)
            log.warning("%s HTTP %s; waiting %.0fs before retrying", self.backend, r.status_code, wait)
            self._sleep(wait)
        return r

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def cost(self, elapsed_s: float, input_tokens: int | None, output_tokens: int | None) -> float | None:
        return elapsed_s * self.usd_per_gpu_hour / 3600

    def complete(self, system: str, user: str) -> Completion:
        start = time.perf_counter()
        r = self._post(system, user)
        if r.status_code == 400 and self.schema_mode:
            log.warning("%s rejected the JSON schema (%s); falling back to JSON mode", self.backend, r.text[:200])
            self.schema_mode = False
            r = self._post(system, user)
        if r.status_code >= 400:
            raise RuntimeError(f"{self.backend} returned HTTP {r.status_code}: {r.text[:300]}")
        elapsed = time.perf_counter() - start
        body = r.json()
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        finish = choice.get("finish_reason")
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        return Completion(
            text=choice["message"].get("content") or "",
            stop_reason="max_tokens" if finish == "length" else finish,
            model=body.get("model", self.model),
            input_tokens=tin, output_tokens=tout,
            cost_usd=self.cost(elapsed, tin, tout),
        )


class LocalCoach(OpenAICompatCoach):
    """The fine-tuned model on a local server. Cost = GPU time x COACH_LOCAL_USD_PER_GPU_HOUR."""

    backend = "local"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 http: httpx.Client | None = None, usd_per_gpu_hour: float | None = None):
        super().__init__(
            model=model or os.environ.get("COACH_LOCAL_MODEL", "coach"),
            base_url=base_url or os.environ.get("COACH_LOCAL_URL", "http://localhost:11434/v1"),
            http=http,
            usd_per_gpu_hour=usd_per_gpu_hour if usd_per_gpu_hour is not None
            else float(os.environ.get("COACH_LOCAL_USD_PER_GPU_HOUR", "0")),
        )


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_DEFAULT_MODEL = "gemini-3.8-flash"   # free tier on the Gemini API pricing page (checked 2026-10-20)


class GeminiCoach(OpenAICompatCoach):
    """Gemini API for free testing. Key: GEMINI_API_KEY. Cost logged as 0 (free tier)."""

    backend = "gemini"
    max_tokens = 16000   # Gemini Flash models think; leave room so the answer isn't cut off

    def __init__(self, model: str | None = None, api_key: str | None = None, http: httpx.Client | None = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
        super().__init__(model=model or os.environ.get("COACH_GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
                         base_url=GEMINI_URL, api_key=key, http=http)

    def cost(self, elapsed_s, input_tokens, output_tokens) -> float | None:
        return 0.0
