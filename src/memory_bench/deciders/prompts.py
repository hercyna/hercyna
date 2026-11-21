"""The decider prompt (docs/deciders.md section 1), built from a DecisionInput.

The text pieces are the frozen ones in `lifted.py`. `build_prompt` gives exactly the messages
`lifted.build_messages` gives for a view whose text is `inp.records_text` and a case whose query is
`inp.query_text`, with no inserted units (tests/test_prompts.py proves byte equality).
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..cases.schema import ACTIONS
from .base import DecisionInput
from .lifted import CONTEXT, POLICY, PROMPT_VERSIONS, REMINDER, REPLY, REPLY_FORMAT, SYSTEM

__all__ = ["build_prompt", "with_reminder", "prompt_hashes", "ACTION_SCHEMA", "PROMPT_VERSIONS"]

# The strict schema the LENS batch code sent as output_config.format (batch.ACTION_SCHEMA).
ACTION_SCHEMA: dict[str, Any] = copy.deepcopy(REPLY_FORMAT)
ACTION_SCHEMA["additionalProperties"] = False
ACTION_SCHEMA["properties"]["action"]["enum"] = list(ACTIONS)


def _check(version: str) -> None:
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"prompt version must be one of {PROMPT_VERSIONS}, got {version!r}")


def build_prompt(inp: DecisionInput, version: str = "v1") -> list[dict[str, str]]:
    """[system, user] messages for one decision."""
    _check(version)
    records = inp.records_text if inp.records_text.strip() else "(no records)"
    user = f"{CONTEXT}\n\nRecords:\n{records}\n\n{inp.query_text}\n\n{POLICY[version]}\n\n{REPLY[version]}"
    return [{"role": "system", "content": SYSTEM[version]}, {"role": "user", "content": user}]


def with_reminder(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """The retry messages: REMINDER appended to the last message, as lifted.LLMDecider does."""
    out = [dict(m) for m in messages]
    out[-1]["content"] += "\n\n" + REMINDER
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def prompt_hashes(version: str = "v1") -> dict[str, str]:
    """sha256 of each frozen text piece of one prompt version, for the protocol freeze."""
    _check(version)
    return {
        "system": _sha(SYSTEM[version]),
        "context": _sha(CONTEXT),
        "policy": _sha(POLICY[version]),
        "reply": _sha(REPLY[version]),
        "reminder": _sha(REMINDER),
        "reply_format": _sha(_canon(REPLY_FORMAT)),
        "action_schema": _sha(_canon(ACTION_SCHEMA)),
    }
