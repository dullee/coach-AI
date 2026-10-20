"""Send one tiny request to a coaching backend and print the raw reply.

    python scripts/backend_check.py --backend pollinations [--model M]
    python scripts/backend_check.py --backend gemini

Use it when reports fail with empty or odd answers: it shows exactly what the
service sent back (error messages, balance problems, unexpected fields).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.coach.factory import BackendUnavailable, make_coach  # noqa: E402
from backend.coach.local import OpenAICompatCoach  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["gemini", "pollinations", "local"])
    ap.add_argument("--model")
    args = ap.parse_args()
    try:
        coach = make_coach(args.backend, args.model)
    except BackendUnavailable as e:
        print(f"error: {e}")
        return 1
    assert isinstance(coach, OpenAICompatCoach)
    tests = {
        "plain text": {"messages": [{"role": "user", "content": "Reply with the single word OK."}]},
        "JSON answer": {"messages": [{"role": "system", "content": "Answer with one JSON object, nothing else."},
                                     {"role": "user", "content": 'Return {"answer": "OK"}.'}]},
    }
    for name, payload in tests.items():
        body = {"model": coach.model, "max_tokens": 2000, **payload, **coach.extra_body}
        start = time.perf_counter()
        r = coach.http.post(f"{coach.base_url}/chat/completions", headers=coach.headers, json=body)
        print(f"\n=== {name}: HTTP {r.status_code} in {time.perf_counter() - start:.1f}s")
        try:
            print(json.dumps(r.json(), indent=1)[:1500])
        except ValueError:
            print(r.text[:1500])
    if args.backend == "pollinations":
        r = coach.http.get(f"{coach.base_url.removesuffix('/v1')}/account/balance", headers=coach.headers)
        print(f"\n=== Pollen balance: HTTP {r.status_code}\n{r.text[:500]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
