"""ChatAgentTrack implementing TrackSpec for Track 2 (`chat_agent`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...cases.schema import Case, Event, read_jsonl
from ...deciders.base import DecisionInput
from ..base import PatchVerdict, TrackMeta, WorkspaceSpec, render_v2_record
from .generator import chat_oracle, generate_chat_cases
from .prompts import PROMPT_VERSIONS, action_schema, build_messages, parse_reply, with_reminder
from .schema import (
    CASE_SCHEMA,
    CHAT_ACTIONS,
    CHAT_MECHANISMS,
    EXECUTE_OR_ANSWER,
    REQUEST_INFO,
    TRACK_NAME,
)


class ChatAgentTrack:
    """Track 2 (`chat_agent`) multi-session conversational memory benchmark."""

    meta = TrackMeta(
        name=TRACK_NAME,
        case_schema=CASE_SCHEMA,
        mechanisms=CHAT_MECHANISMS,
        valid_actions=CHAT_ACTIONS,
        abstention_action=REQUEST_INFO,
        default_routine_action=EXECUTE_OR_ANSWER,
        prompt_versions=PROMPT_VERSIONS,
        supports_mode_b=False,
    )

    def load_cases(self, path: Path) -> list[Case]:
        return list(read_jsonl(path))

    def render_record(self, case: Case, event: Event) -> str:
        return render_v2_record(event)

    def build_messages(self, inp: DecisionInput, version: str = "v1") -> list[dict[str, str]]:
        return build_messages(inp, version=version)

    def with_reminder(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        return with_reminder(messages)

    def action_schema(self, version: str = "v1", eval_mode: str = "mode_a") -> dict[str, Any]:
        return action_schema(version=version, eval_mode=eval_mode)

    def parse_reply(self, raw: str, eval_mode: str = "mode_a") -> dict[str, Any] | None:
        return parse_reply(raw, eval_mode=eval_mode)

    def oracle(self, case: Case, visible_ids: set[str] | None = None) -> str:
        if visible_ids is None:
            events = case.events
        else:
            events = [ev for ev in case.events if ev.id in visible_ids]
        meta = getattr(case, "metadata", None) or {}
        return chat_oracle(
            events,
            target_slot=str(meta.get("slot") or ""),
            query_date=str(meta.get("query_date") or ""),
            missing_param=bool(meta.get("missing_param", False)),
            requires_standing_rule=bool(meta.get("requires_standing_rule", False)),
        )

    def generate_cases(self, split: str, seed: int, n: int, **knobs: Any) -> list[Case]:
        return generate_chat_cases(split=split, seed=seed, n=n, prefix=knobs.get("prefix"))

    def prepare_workspace(self, case: Case, dest_dir: Path, *, blind: bool) -> WorkspaceSpec:
        return WorkspaceSpec(case_id=case.case_id, workspace_dir=dest_dir, blind=blind)

    def verify_workspace(self, case: Case, dest_dir: Path, result: Any) -> PatchVerdict:
        return PatchVerdict(
            passed=False,
            verdict_reason="MODE_B_UNSUPPORTED",
            details={"track": self.meta.name},
        )


__all__ = ["ChatAgentTrack"]
