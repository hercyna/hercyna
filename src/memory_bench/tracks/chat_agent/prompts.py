"""Frozen prompts and strict JSON parser for Track 2 (`chat_agent`)."""

from __future__ import annotations

import json
import re
from typing import Any

from ...deciders.base import DecisionInput
from .schema import CHAT_ACTION_SCHEMA, CHAT_ACTIONS

PROMPT_VERSIONS = ("v1", "v2")

_SYSTEM_V1 = """You are a multi-session workspace assistant deciding the single next action for a user's request based ONLY on the provided conversation history records.

Choose exactly one action from:
- EXECUTE_OR_ANSWER: The request matches a stable preference or routine workflow, all required details are present, and no prohibition, expired approval, unresolved conflict, or preference override applies.
- USE_UPDATED_PREF: An earlier preference for the requested workflow was explicitly superseded by a later preference override in the records, and no prohibition, expired approval, or conflict blocks execution.
- CHECK_OR_RENEW: The requested workflow depends on a time-bound permission or approval whose expiration date is on or before the request date (with no later valid renewal in the records).
- REFUSE_OR_GUARD: An active standing prohibition or privacy/budget boundary in the records forbids performing the requested action.
- REQUEST_INFO: Either (a) the user's request omits a required parameter and no default appears in the records, (b) the records contain two or more active conflicting standing rules for the requested workflow with no resolution, or (c) the user asks to apply a standing rule for the workflow but no standing rule appears in the records.

Apply rules in strict priority order:
1. REFUSE_OR_GUARD (active prohibition/boundary on the requested workflow).
2. REQUEST_INFO (two or more incompatible active standing rules on the workflow, or no standing rule visible when the request asks to apply a standing rule).
3. CHECK_OR_RENEW (permission/approval for the workflow expired on or before the request date without a valid renewal).
4. REQUEST_INFO (request asks for a specific action but is missing a required parameter with no default in records).
5. USE_UPDATED_PREF (a later preference override replaced an earlier preference on the workflow).
6. EXECUTE_OR_ANSWER (otherwise).

Ignore records belonging to unrelated workflows. Compare dates strictly against the request's Date (YYYY-MM-DD).

Reply with a single JSON object only: {"action": "<ONE_OF_THE_FIVE_ACTIONS>", "reason": "<one brief sentence>"}."""

_SYSTEM_V2 = (
    _SYSTEM_V1
    + "\n\nDo not include markdown fences, commentary, or extra keys outside the JSON object."
)

_REMINDER = (
    'Reply ONLY with one JSON object of the form {"action": "...", "reason": "..."} '
    f"where action is strictly one of {', '.join(CHAT_ACTIONS)}. No extra text."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def build_messages(inp: DecisionInput, version: str = "v1") -> list[dict[str, str]]:
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"chat_agent prompt version must be one of {PROMPT_VERSIONS}, got {version!r}")
    system = _SYSTEM_V1 if version == "v1" else _SYSTEM_V2
    records_block = inp.records_text.strip() or "(no records retrieved)"
    user = (
        f"Conversation history records:\n{records_block}\n\n"
        f"Current user request:\n{inp.query_text}\n\n"
        'Return JSON: {"action": "...", "reason": "..."}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def with_reminder(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [*messages, {"role": "user", "content": _REMINDER}]


def action_schema(version: str = "v1", eval_mode: str = "mode_a") -> dict[str, Any]:
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"chat_agent prompt version must be one of {PROMPT_VERSIONS}, got {version!r}")
    if eval_mode != "mode_a":
        raise ValueError(f"chat_agent supports eval_mode='mode_a' only, got {eval_mode!r}")
    return dict(CHAT_ACTION_SCHEMA)


def _extract_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    for candidate in (text,):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return None


def parse_reply(raw: str, eval_mode: str = "mode_a") -> dict[str, Any] | None:
    if eval_mode != "mode_a":
        raise ValueError(f"chat_agent supports eval_mode='mode_a' only, got {eval_mode!r}")
    obj = _extract_object(raw)
    if obj is None:
        return None
    action = obj.get("action")
    if not isinstance(action, str):
        return None
    action = action.strip()
    if action not in CHAT_ACTIONS:
        return None
    reason = obj.get("reason", "")
    return {"action": action, "reason": str(reason).strip()}


__all__ = [
    "PROMPT_VERSIONS",
    "build_messages",
    "with_reminder",
    "action_schema",
    "parse_reply",
]
