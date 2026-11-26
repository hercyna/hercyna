"""Pluggable TrackSpec protocol and v2 record formatting (memory-bench v2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..cases.schema import Case, Event
from ..deciders.base import DecisionInput


@dataclass(frozen=True)
class TrackMeta:
    name: str
    case_schema: str
    mechanisms: tuple[str, ...]
    valid_actions: tuple[str, ...]
    abstention_action: str = "REQUEST_INFO"
    default_routine_action: str = "EXPLAIN"
    prompt_versions: tuple[str, ...] = ("v1", "v2")
    supports_mode_b: bool = False


@dataclass(frozen=True)
class WorkspaceSpec:
    case_id: str
    workspace_dir: Path
    blind: bool
    base_commit: str | None = None
    test_cmd: str | None = None
    protected_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchVerdict:
    passed: bool
    verdict_reason: str
    details: dict[str, Any] = field(default_factory=dict)


def render_v2_record(event: Event) -> str:
    """Normative v2 plain space-delimited ISO format (no leading bracket).

    Format: "<id> <date> <type> <subtype> <text>" (collapsing empty subtype).
    """
    body = getattr(event, "text", None)
    if body is None:
        parts = [f"{k}={v}" for k, v in sorted(event.fields.items())]
        body = " ".join(parts)
    else:
        body = str(body).strip()
        # Strip any leading "[id date] " prefix if caller embedded one in text
        if body.startswith("[") and "]" in body:
            prefix, rest = body[1:].split("]", 1)
            toks = prefix.strip().split()
            if len(toks) == 2 and toks[0] == str(event.id) and toks[1] == str(event.date):
                body = rest.strip()
    head = f"{event.id} {event.date} {event.type}"
    if event.subtype:
        head = f"{head} {event.subtype}"
    return f"{head} {body}".strip()


@runtime_checkable
class TrackSpec(Protocol):
    meta: TrackMeta

    def load_cases(self, path: Path) -> list[Case]: ...
    def render_record(self, case: Case, event: Event) -> str: ...
    def build_messages(self, inp: DecisionInput, version: str = "v1") -> list[dict[str, str]]: ...
    def action_schema(self, version: str = "v1", eval_mode: str = "mode_a") -> dict[str, Any]: ...
    def parse_reply(self, raw: str, eval_mode: str = "mode_a") -> dict[str, Any] | None: ...
    def oracle(self, case: Case, visible_ids: set[str] | None = None) -> str: ...
    def generate_cases(self, split: str, seed: int, n: int, **knobs: Any) -> list[Case]: ...
    def prepare_workspace(self, case: Case, dest_dir: Path, *, blind: bool) -> WorkspaceSpec: ...
    def verify_workspace(self, case: Case, dest_dir: Path, result: Any) -> PatchVerdict: ...
