"""Track 1 (`account_review`) TrackSpec delegating directly to v1 modules."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..cases.generator import oracle as v1_oracle
from ..cases.render import render_events
from ..cases.sample import dev_cases, make_headline_sample
from ..cases.schema import ACTIONS, EXPLAIN, MECHANISMS, REQUEST_INFO, Case, Event, read_jsonl
from ..deciders.base import DecisionInput
from ..deciders.lifted import PROMPT_VERSIONS, parse_reply as v1_parse_reply
from ..deciders.prompts import ACTION_SCHEMA, build_prompt
from .base import PatchVerdict, TrackMeta, WorkspaceSpec


class AccountReviewTrack:
    meta = TrackMeta(
        name="account_review",
        case_schema="memory_bench.case/1",
        mechanisms=MECHANISMS,
        valid_actions=ACTIONS,
        abstention_action=REQUEST_INFO,
        default_routine_action=EXPLAIN,
        prompt_versions=PROMPT_VERSIONS,
        supports_mode_b=False,
    )

    def load_cases(self, path: Path) -> list[Case]:
        return list(read_jsonl(path))

    def render_record(self, case: Case, event: Event) -> str:
        rendered = render_events(case, [event])
        return str(rendered[0])

    def build_messages(self, inp: DecisionInput, version: str = "v1") -> list[dict[str, str]]:
        return build_prompt(inp, version)

    def action_schema(self, version: str = "v1", eval_mode: str = "mode_a") -> dict[str, Any]:
        return copy.deepcopy(ACTION_SCHEMA)

    def parse_reply(self, raw: str, eval_mode: str = "mode_a") -> dict[str, Any] | None:
        return v1_parse_reply(raw)

    def oracle(self, case: Case, visible_ids: set[str] | None = None) -> str:
        events = case.events if visible_ids is None else [e for e in case.events if e.id in visible_ids]
        return v1_oracle(events, case.query_period)

    def generate_cases(self, split: str, seed: int, n: int, **knobs: Any) -> list[Case]:
        if split == "dev":
            return list(dev_cases())[:n]
        return list(make_headline_sample(seed=seed, n=n))

    def prepare_workspace(self, case: Case, dest_dir: Path, *, blind: bool) -> WorkspaceSpec:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return WorkspaceSpec(case_id=case.case_id, workspace_dir=dest_dir, blind=True)

    def verify_workspace(self, case: Case, dest_dir: Path, result: Any) -> PatchVerdict:
        action = getattr(result, "action", None) if not isinstance(result, dict) else result.get("action")
        ok = action == case.a_star
        return PatchVerdict(passed=ok, verdict_reason="PASS" if ok else "ACTION_MISMATCH")
