"""JSON schema sent to the model as the required output format.

Kept to plain types (no length limits) so every structured-output backend
accepts it; the stricter rules (1-5 priorities, at least one issue id) are
checked by coach/validate.py against the CoachingReport Pydantic model.
"""

from __future__ import annotations

_STR = {"type": "string"}

COACHING_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "headline": _STR,
        "priorities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": _STR, "why": _STR, "how_to_fix": _STR, "drill": _STR,
                    "issue_ids": {"type": "array", "items": _STR},
                },
                "required": ["title", "why", "how_to_fix", "drill", "issue_ids"],
                "additionalProperties": False,
            },
        },
        "strengths": {"type": "array", "items": _STR},
        "next_match_focus": _STR,
    },
    "required": ["headline", "priorities", "strengths", "next_match_focus"],
    "additionalProperties": False,
}
