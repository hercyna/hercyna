"""Schema constants and case validation for Track 2 (`chat_agent`)."""

from __future__ import annotations

from typing import Any

TRACK_NAME = "chat_agent"
CASE_SCHEMA = "memory_bench.case/2"
GENERATOR_VERSION = 1

EXECUTE_OR_ANSWER = "EXECUTE_OR_ANSWER"
USE_UPDATED_PREF = "USE_UPDATED_PREF"
CHECK_OR_RENEW = "CHECK_OR_RENEW"
REFUSE_OR_GUARD = "REFUSE_OR_GUARD"
REQUEST_INFO = "REQUEST_INFO"

CHAT_ACTIONS: tuple[str, ...] = (
    EXECUTE_OR_ANSWER,
    USE_UPDATED_PREF,
    CHECK_OR_RENEW,
    REFUSE_OR_GUARD,
    REQUEST_INFO,
)

CHAT_MECHANISMS: tuple[str, ...] = ("H0", "H1", "H2", "H3", "HU1", "HU2")

# Natural 300-case mix per spec_v2_coding_and_chat.md section 4.1:
# 50% H0 (150), 10% each of H1, H2, H3, HU1, HU2 (30 each).
NATURAL_MIX_RATIOS: dict[str, tuple[int, int]] = {
    "H0": (1, 2),
    "H1": (1, 10),
    "H2": (1, 10),
    "H3": (1, 10),
    "HU1": (1, 10),
    "HU2": (1, 10),
}

MECHANISM_TO_ACTION: dict[str, str] = {
    "H0": EXECUTE_OR_ANSWER,
    "H1": USE_UPDATED_PREF,
    "H2": CHECK_OR_RENEW,
    "H3": REFUSE_OR_GUARD,
    "HU1": REQUEST_INFO,
    "HU2": REQUEST_INFO,
}

MECHANISM_TO_CLS: dict[str, str] = {
    "H0": "none",
    "H1": "rare",
    "H2": "rare",
    "H3": "rare",
    "HU1": "unobserved",
    "HU2": "unresolved",
}

SLOTS: tuple[str, ...] = (
    "report_format",
    "travel_policy",
    "invoice_export",
    "meeting_window",
    "dataset_export",
    "purchase_approval",
)

DISTANCE_BUCKETS: tuple[str, ...] = ("near", "middle", "far")
LENGTH_BUCKETS: tuple[str, ...] = ("short", "medium", "long")

CHAT_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(CHAT_ACTIONS)},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
    "additionalProperties": False,
}

__all__ = [
    "TRACK_NAME",
    "CASE_SCHEMA",
    "GENERATOR_VERSION",
    "EXECUTE_OR_ANSWER",
    "USE_UPDATED_PREF",
    "CHECK_OR_RENEW",
    "REFUSE_OR_GUARD",
    "REQUEST_INFO",
    "CHAT_ACTIONS",
    "CHAT_MECHANISMS",
    "NATURAL_MIX_RATIOS",
    "MECHANISM_TO_ACTION",
    "MECHANISM_TO_CLS",
    "SLOTS",
    "DISTANCE_BUCKETS",
    "LENGTH_BUCKETS",
    "CHAT_ACTION_SCHEMA",
]
