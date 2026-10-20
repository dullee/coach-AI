"""Pick a coaching backend by name: claude (default), gemini (free testing),
pollinations (third-party gateway, testing only), local."""

from __future__ import annotations

import os

from backend.coach.base import CoachModel

BACKENDS = ("claude", "gemini", "pollinations", "local")


class BackendUnavailable(Exception):
    pass


def default_backend() -> str:
    return os.environ.get("COACH_BACKEND", "claude")


def make_coach(backend: str | None = None, model: str | None = None) -> CoachModel:
    backend = backend or default_backend()
    if backend == "claude":
        from backend.coach.claude import NO_CREDENTIALS, ClaudeCoach, has_credentials

        if not has_credentials():
            raise BackendUnavailable(NO_CREDENTIALS + "  (or try free testing with --backend gemini)")
        return ClaudeCoach(model=model)
    if backend == "gemini":
        from backend.coach.local import GeminiCoach

        try:
            return GeminiCoach(model=model)
        except ValueError as e:
            raise BackendUnavailable(str(e)) from e
    if backend == "pollinations":
        from backend.coach.local import PollinationsCoach

        try:
            return PollinationsCoach(model=model)
        except ValueError as e:
            raise BackendUnavailable(str(e)) from e
    if backend == "local":
        from backend.coach.local import LocalCoach

        return LocalCoach(model=model)
    raise BackendUnavailable(f"unknown backend {backend!r}; choose one of {BACKENDS}")
