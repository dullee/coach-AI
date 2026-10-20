"""Community API client (deadlock-api.com) with caching, throttling and retries.

Rate limits are the free per-IP limits in docs/phase0-findings.md (Q3).
We never use an API key and always pass disable_steam=true on the
match endpoints, so we can't use up the tiny "fetch from Steam" quotas.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from backend import config
from backend.ingest.cache import DiskCache
from backend.ingest.models import MatchHistoryEntry, MatchSalts, PlayerRank, Rank

log = logging.getLogger(__name__)

# Minimum seconds between requests, per endpoint group (first matching prefix wins).
# Set a bit below each published per-IP limit.
THROTTLE: list[tuple[str, float]] = [
    ("/v1/sql", 31.0),                  # 2 req/min
    ("/v1/matches/metadata", 2.1),      # bulk: 30 req/min
    ("/v1/analytics/", 0.31),           # shared: 200 req/min
    ("/v1/matches/demo/", 18.1),        # 200 req/hour
]
DEFAULT_INTERVAL = 0.05                 # other endpoints allow ~100 req/s

# How long cached responses stay fresh (seconds). None = forever.
HOUR = 3600.0
TTL_ASSETS = 24 * HOUR       # static data; also refreshed per patch
TTL_ANALYTICS = 6 * HOUR     # the API itself caches these 6-12 h
TTL_PLAYER = 0.25 * HOUR     # match history / rank change after every game
TTL_FOREVER = None           # finished matches never change

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int, path: str, body: str = ""):
        super().__init__(f"HTTP {status} for {path}: {body[:200]}")
        self.status = status
        self.path = path


class NotFound(ApiError):
    pass


class DeadlockApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        cache: DiskCache | None = None,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        max_wait_s: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cache = cache or DiskCache(config.cache_dir())
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.max_wait_s = max_wait_s
        self._sleep = sleep
        self._clock = clock
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            base_url=base_url or config.api_base_url(),
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "deadlock-coach (student project)"},
            follow_redirects=True,
        )
        self.network_calls = 0  # for tests and logging

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DeadlockApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- core ---------------------------------------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, max_age_s: float | None) -> Any:
        """GET a JSON endpoint, using the disk cache when fresh enough."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = DiskCache.key(path, params)
        cached = self.cache.get(key, max_age_s)
        if cached is not None:
            return cached
        body = self._fetch(path, params)
        self.cache.set(key, body, path=path)
        return body

    def _throttle(self, path: str) -> None:
        group, interval = next(((p, s) for p, s in THROTTLE if path.startswith(p)), ("*", DEFAULT_INTERVAL))
        with self._lock:
            wait = self._last_call.get(group, -1e18) + interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last_call[group] = self._clock()

    def _fetch(self, path: str, params: dict[str, Any]) -> Any:
        attempt = 0
        while True:
            self._throttle(path)
            try:
                self.network_calls += 1
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                if attempt >= self.max_retries:
                    raise
                delay = self._backoff(attempt)
                log.warning("network error on %s (%s); retry in %.1fs", path, e, delay)
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    raise NotFound(404, path, resp.text)
                if resp.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                    raise ApiError(resp.status_code, path, resp.text)
                delay = self._retry_after(resp) or self._backoff(attempt)
                log.warning("HTTP %s on %s; retry in %.1fs", resp.status_code, path, delay)
            self._sleep(min(delay, self.max_wait_s))
            attempt += 1

    def _backoff(self, attempt: int) -> float:
        # Exponential backoff with jitter: ~1, 2, 4, 8 s ...
        return self.backoff_base_s * (2**attempt) * (1 + random.random() * 0.25)

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        header = resp.headers.get("retry-after")
        if header:
            try:
                return max(float(header), 0.5)
            except ValueError:
                pass
        # The API's 429 body includes {"error": {"next_request_in": seconds}}.
        try:
            secs = resp.json()["error"]["next_request_in"]
            return max(float(secs), 0.5)
        except (ValueError, KeyError, TypeError):
            return None

    # ---- typed endpoints ------------------------------------------------

    def client_versions(self) -> list[int]:
        return self.get_json("/v1/assets/client-versions", max_age_s=TTL_ASSETS)

    def heroes(self, client_version: int | None = None) -> list[dict]:
        return self.get_json("/v1/assets/heroes", {"client_version": client_version}, max_age_s=TTL_ASSETS)

    def items(self, client_version: int | None = None) -> list[dict]:
        """Items, abilities and weapons (field "type": upgrade / ability / weapon)."""
        return self.get_json("/v1/assets/items", {"client_version": client_version}, max_age_s=TTL_ASSETS)

    def ranks(self, client_version: int | None = None) -> list[Rank]:
        raw = self.get_json("/v1/assets/ranks", {"client_version": client_version}, max_age_s=TTL_ASSETS)
        return [Rank.model_validate(r) for r in raw]

    def match_metadata(self, match_id: int) -> dict:
        raw = self.get_json(f"/v1/matches/{match_id}/metadata", {"disable_steam": "true"}, max_age_s=TTL_FOREVER)
        return raw["match_info"] if "match_info" in raw else raw

    def match_salts(self, match_id: int) -> MatchSalts:
        # Not cached forever: the API may learn a replay salt later.
        raw = self.get_json(f"/v1/matches/{match_id}/salts", {"disable_steam": "true"}, max_age_s=HOUR)
        return MatchSalts.model_validate(raw)

    def match_history(self, account_id: int) -> list[MatchHistoryEntry]:
        raw = self.get_json(f"/v1/players/{account_id}/match-history", max_age_s=TTL_PLAYER)
        return [MatchHistoryEntry.model_validate(m) for m in raw]

    def player_rank(self, account_id: int) -> PlayerRank:
        raw = self.get_json(f"/v1/players/{account_id}/rank", max_age_s=TTL_PLAYER)
        return PlayerRank.model_validate(raw)

    def hero_stats(self, **filters: Any) -> list[dict]:
        """/v1/analytics/hero-stats. Returns totals per hero (not percentiles)."""
        return self.get_json("/v1/analytics/hero-stats", filters, max_age_s=TTL_ANALYTICS)

    def patches(self) -> list[dict]:
        return self.get_json("/v1/patches", max_age_s=TTL_ASSETS)
