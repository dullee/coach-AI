"""Claude API adapter (baseline: Claude Haiku 5.5; premium / teacher: Claude Opus 5.5).

- The static system prompt carries cache_control, so repeat calls read it from
  the prompt cache (minimum cacheable prefix on these models: 512 tokens).
- Structured outputs (output_config.format) constrain the answer to the
  CoachingReport JSON schema; coach/validate.py still checks it.
- Adaptive thinking with an explicit effort level (both models default to
  "medium"; set explicitly so a default change can't alter cost).
- Claude Opus 5.5 / Sonnet 5.5 requests opt into server-side refusal
  fallbacks ("default" mode). Claude Haiku 5.5 has no server-side fallback,
  so a refusal surfaces as CoachRefusal.
Credentials: ANTHROPIC_API_KEY (or an `ant auth login` profile). Never in code.
"""

from __future__ import annotations

import os

import anthropic

from backend.coach.base import Completion, CoachModel
from backend.coach.pricing import cost_usd
from backend.coach.schema import COACHING_REPORT_SCHEMA

DEFAULT_MODEL = "claude-haiku-5-5"   # CLAUDE.md: small model for the baseline
PREMIUM_MODEL = "claude-opus-5-5"    # larger model for premium deep review / Phase 4 teacher
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MODELS_WITH_SERVER_FALLBACK = {"claude-opus-5-5", "claude-sonnet-5-5"}
MAX_TOKENS = 16000                   # room for adaptive thinking plus the report


def has_credentials() -> bool:
    """Cheap check before calling: an API key / auth token env var, or an `ant auth login` profile."""
    from pathlib import Path

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


NO_CREDENTIALS = ("no Anthropic credentials found. Create an API key at https://console.anthropic.com "
                  "and set it for this terminal, e.g. in PowerShell:  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"")


class ClaudeCoach(CoachModel):
    backend = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client: anthropic.Anthropic | None = None):
        self.model = model or os.environ.get("COACH_CLAUDE_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def request_params(self, system: str, user: str) -> dict:
        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": COACHING_REPORT_SCHEMA},
            },
        }

    def complete(self, system: str, user: str) -> Completion:
        params = self.request_params(system, user)
        if self.model in MODELS_WITH_SERVER_FALLBACK:
            response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            response = self.client.messages.create(**params)
        text = next((b.text for b in response.content if b.type == "text"), "")
        u = response.usage
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        served = getattr(response, "model", None) or self.model
        return Completion(
            text=text, stop_reason=response.stop_reason, model=served,
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_write_tokens=cache_write, cache_read_tokens=cache_read,
            cost_usd=cost_usd(served, u.input_tokens, u.output_tokens, cache_write, cache_read),
            request_id=getattr(response, "_request_id", None),
        )
