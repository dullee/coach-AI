"""On-disk JSON cache for community API responses.

Every response is cached (CLAUDE.md: "Cache every response"). Each entry stores
when it was fetched, so callers decide freshness with a per-call max age.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def key(path: str, params: dict[str, Any] | None) -> str:
        canon = json.dumps({"path": path, "params": params or {}}, sort_keys=True, default=str)
        return hashlib.sha256(canon.encode()).hexdigest()

    def _file(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, max_age_s: float | None) -> Any | None:
        """Return the cached body, or None if missing or older than max_age_s.

        max_age_s=None means "any age is fine" (for data that never changes,
        such as a finished match's metadata).
        """
        f = self._file(key)
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if max_age_s is not None and time.time() - entry["fetched_at"] > max_age_s:
            return None
        return entry["body"]

    def set(self, key: str, body: Any, *, path: str) -> None:
        f = self._file(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        entry = {"fetched_at": time.time(), "path": path, "body": body}
        # Write to a temp file then rename, so a crash never leaves half a file.
        fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(entry, out)
        os.replace(tmp, f)
