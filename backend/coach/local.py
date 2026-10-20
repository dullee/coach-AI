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

import json
import logging
import os
import time

import httpx

from backend.coach.base import Completion, CoachModel
from backend.coach.schema import COACHING_REPORT_SCHEMA

log = logging.getLogger(__name__)


def message_text(message: dict) -> str:
    """Answer text from an OpenAI-style message: a string, or a list of content parts."""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


class OpenAICompatCoach(CoachModel):
    backend = "openai-compat"
    max_tokens = 4000
    timeout_s = 300.0
    extra_body: dict = {}    # backend-specific request fields

    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 http: httpx.Client | None = None, usd_per_gpu_hour: float = 0.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = http or httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=10.0))
        self.headers = headers
        self.usd_per_gpu_hour = usd_per_gpu_hour
        self.schema_mode = True   # switched off if the server rejects json_schema

    # "json_schema" (default), or "none" for servers/models without response_format:
    # then the schema goes into the system prompt and validate() strips ``` fences.
    output_mode = "json_schema"

    def _response_format(self) -> dict | None:
        if self.output_mode == "none":
            return None
        if self.schema_mode:
            return {"type": "json_schema",
                    "json_schema": {"name": "CoachingReport", "schema": COACHING_REPORT_SCHEMA, "strict": True}}
        return {"type": "json_object"}

    send_temperature = True
    max_retries = 4          # on 429 / 5xx (free tiers have low per-minute limits)
    max_wait_s = 65.0

    def _post(self, system: str, user: str) -> httpx.Response:
        fmt = self._response_format()
        if fmt is None:
            system = (f"{system}\n\nAnswer with one JSON object, nothing else, matching this JSON schema:\n"
                      f"{json.dumps(COACHING_REPORT_SCHEMA)}")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": self.max_tokens,
            **({"response_format": fmt} if fmt else {}),
            **({"temperature": 0.3} if self.send_temperature else {}),
            **self.extra_body,
        }
        timeouts = 0
        for attempt in range(self.max_retries + 1):
            log.info("asking %s:%s (attempt %d)", self.backend, self.model, attempt + 1)
            try:
                r = self.http.post(f"{self.base_url}/chat/completions", headers=self.headers, json=body)
            except httpx.TimeoutException as e:
                timeouts += 1
                if timeouts >= 2:
                    raise RuntimeError(f"{self.backend} did not answer within {self.timeout_s:.0f}s twice; "
                                       f"the server may be overloaded, try again later") from e
                log.warning("%s did not answer within %.0fs; retrying once", self.backend, self.timeout_s)
                continue
            except httpx.TransportError as e:
                raise RuntimeError(f"could not reach {self.base_url} ({e})") from e
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
        if r.status_code == 400 and self.schema_mode and self.output_mode != "none":
            log.warning("%s rejected the JSON schema (%s); falling back to JSON mode", self.backend, r.text[:200])
            self.schema_mode = False
            r = self._post(system, user)
        if r.status_code >= 400:
            raise RuntimeError(f"{self.backend} returned HTTP {r.status_code}: {r.text[:300]}")
        elapsed = time.perf_counter() - start
        body = r.json()
        if not body.get("choices"):
            raise RuntimeError(f"{self.backend} returned no answer: {json.dumps(body)[:400]}")
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        finish = choice.get("finish_reason")
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        text = message_text(choice.get("message") or {})
        if not text.strip():
            # A gateway can answer 200 with nothing in it (seen on Pollinations: 0 tokens in/out).
            # That is not a bad report from the model, so don't spend the validation retry on it.
            raise RuntimeError(f"{self.backend} returned an empty answer (tokens in/out: {tin}/{tout}). "
                               f"Raw response: {json.dumps(body)[:400]}")
        return Completion(
            text=text,
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
    max_tokens = 8000    # Gemini Flash models think; leave room so the answer isn't cut off
    timeout_s = 300.0    # the free tier can be slow: one real call took 277 s
    # Thinking can't be turned off on Gemini 3 models; "low" keeps it short
    # (values from Google's OpenAI-compatibility docs: minimal, low, medium, high).
    extra_body = {"reasoning_effort": os.environ.get("COACH_GEMINI_REASONING", "low")}

    def __init__(self, model: str | None = None, api_key: str | None = None, http: httpx.Client | None = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
        super().__init__(model=model or os.environ.get("COACH_GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
                         base_url=GEMINI_URL, api_key=key, http=http)

    def cost(self, elapsed_s, input_tokens, output_tokens) -> float | None:
        return 0.0


POLLINATIONS_URL = "https://gen.pollinations.ai/v1"
# Closest quest-Pollen match to Claude Haiku 5.5 in the live catalog (checked 2026-10-21):
# "Efficient reasoning for focused, high-volume tasks", 0.10/0.50 Pollen per M tokens
# (Haiku 5.5: 0.075/0.375, but paid_only), supports response_format, 99.8% healthy.
POLLINATIONS_DEFAULT_MODEL = "openai/gpt-6-luna"

_catalog: dict[str, dict] | None = None


def pollinations_catalog(http: httpx.Client) -> dict[str, dict]:
    """Model id/alias -> catalog entry from GET /v1/models (no key needed). Cached per process."""
    global _catalog
    if _catalog is None:
        try:
            r = http.get(f"{POLLINATIONS_URL}/models", timeout=10.0)
            entries = r.json().get("data", []) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            entries = []
        _catalog = {}
        for m in entries:
            for name in [m.get("id"), *(m.get("aliases") or [])]:
                if name:
                    _catalog[name] = m
    return _catalog


class PollinationsCoach(OpenAICompatCoach):
    """Models through Pollinations' OpenAI-compatible gateway, for prototype testing.

    Verified from https://gen.pollinations.ai/docs and the live model catalog:
    - POST {POLLINATIONS_URL}/chat/completions, key as a Bearer token (POLLINATIONS_API_KEY,
      a secret sk_ key from enter.pollinations.ai/keys).
    - Each model lists its supported_parameters; this adapter sends response_format (JSON
      schema) and temperature only when the model supports them, otherwise the schema goes
      into the system prompt. paid_only models need bought Pollen; others run on quest Pollen.
    - Prices are in Pollen, not USD, so cost_usd is left unknown here.
    A third-party gateway: use it for testing only, not for Phase 3 cost numbers or Phase 4
    teacher data.
    """

    backend = "pollinations"
    max_tokens = 8000
    timeout_s = 180.0

    def __init__(self, model: str | None = None, api_key: str | None = None, http: httpx.Client | None = None,
                 catalog: dict[str, dict] | None = None):
        key = api_key or os.environ.get("POLLINATIONS_API_KEY")
        if not key:
            raise ValueError("set POLLINATIONS_API_KEY (secret key from https://enter.pollinations.ai/keys)")
        super().__init__(model=model or os.environ.get("COACH_POLLINATIONS_MODEL", POLLINATIONS_DEFAULT_MODEL),
                         base_url=POLLINATIONS_URL, api_key=key, http=http)
        entry = (catalog if catalog is not None else pollinations_catalog(self.http)).get(self.model, {})
        params = set(entry.get("supported_parameters") or [])
        self.paid_only = bool(entry.get("paid_only"))
        # Unknown model (catalog unreachable): be conservative, schema in the prompt.
        self.output_mode = "json_schema" if "response_format" in params else "none"
        self.send_temperature = "temperature" in params
        effort = os.environ.get("COACH_POLLINATIONS_REASONING", "low")
        self.extra_body = {"reasoning_effort": effort} if "reasoning_effort" in params else {}

    def cost(self, elapsed_s, input_tokens, output_tokens) -> float | None:
        return None   # billed in Pollen, not USD
