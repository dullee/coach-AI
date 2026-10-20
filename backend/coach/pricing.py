"""Price per call. USD per million tokens, Claude API first-party rates
(from the Anthropic model table cached 2026-10-06; re-check before quoting costs).

Haiku 5.5 rates apply to prompts up to 100K tokens (ours are ~2K).
Cache writes are the 5-minute TTL rate. Thinking tokens are billed as output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float | None:
    """None when the model has no known price (e.g. a fallback model not in the table)."""
    p = PRICES.get(model)
    if p is None:
        return None
    return (input_tokens * p.input + output_tokens * p.output
            + cache_write * p.cache_write + cache_read * p.cache_read) / 1_000_000
