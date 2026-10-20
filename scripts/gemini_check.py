"""Quick Gemini connectivity check: which request shapes work, and how fast.

    python scripts/gemini_check.py

Needs GEMINI_API_KEY. Sends 3 tiny requests (plain text, JSON mode, JSON
schema) and prints status and time for each, so a slow or rejected shape
shows up without running a full coaching report.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from backend.coach.local import GEMINI_DEFAULT_MODEL, GEMINI_URL  # noqa: E402

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("set GEMINI_API_KEY first")
        return 1
    model = os.environ.get("COACH_GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    shapes = {
        "plain text": {},
        "JSON mode": {"response_format": {"type": "json_object"}},
        "JSON schema": {"response_format": {"type": "json_schema",
                                            "json_schema": {"name": "T", "schema": SCHEMA, "strict": True}}},
    }
    with httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0)) as http:
        for name, extra in shapes.items():
            body = {"model": model, "max_tokens": 2000, "reasoning_effort": "low",
                    "messages": [{"role": "user", "content": 'Reply with JSON {"answer": "OK"}.'}], **extra}
            start = time.perf_counter()
            try:
                r = http.post(f"{GEMINI_URL}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
                took = time.perf_counter() - start
                if r.status_code == 200:
                    msg = r.json()["choices"][0]["message"].get("content", "")
                    print(f"{name:12s} OK   {took:5.1f}s  {msg[:60]!r}")
                else:
                    print(f"{name:12s} HTTP {r.status_code} {took:5.1f}s  {r.text[:200]}")
            except httpx.TimeoutException:
                print(f"{name:12s} TIMEOUT after {time.perf_counter() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
