"""Exploratory Apple FoundationModels transport (`afm_command`, 1k-view quarantine)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ...deciders.transports import ContextOverflowError, RawReply, TransportError, estimate_tokens

AFM_MAX_VIEW_TOKENS = 1500  # Strict quarantine ceiling well below ~4k OS context cap
RunnerFn = Callable[[Sequence[str], str, float], tuple[int, str, str]]


def _default_runner(argv: Sequence[str], stdin_text: str, timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(list(argv), input=stdin_text, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


@dataclass
class AfmProbeResult:
    status: str  # "ok" | "guardrail_refusal" | "context_exceeded" | "unavailable" | "parse_error"
    raw_reply: RawReply
    detail: str | None = None


class AfmCommandTransport:
    """Quarantined exploratory transport invoking the compiled `hercyna-afm` Swift binary.

    Refuses `gate` and `floor` calls outright so compact on-device models can never enter
    full-history gated tables.
    """

    name = "afm_command"
    exploratory_only = True

    def __init__(
        self,
        binary_path: str | Path,
        *,
        max_view_tokens: int = AFM_MAX_VIEW_TOKENS,
        timeout_s: float = 60.0,
        runner: RunnerFn | None = None,
    ) -> None:
        self.binary_path = Path(binary_path)
        self.max_view_tokens = int(max_view_tokens)
        self.timeout_s = float(timeout_s)
        self.runner = runner or _default_runner

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        kind: str = "view",
        valid_actions: Sequence[str] = (),
    ) -> AfmProbeResult:
        if kind in ("gate", "floor"):
            raise ContextOverflowError(
                f"afm_command is strictly quarantined to exploratory 1k views; refused {kind!r} call"
            )
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        user_parts = [m["content"] for m in messages if m.get("role") != "system"]
        prompt = "\n\n".join(user_parts)
        est_in = estimate_tokens(messages)
        if est_in > self.max_view_tokens:
            raise ContextOverflowError(
                f"afm_command view prompt (~{est_in} tok) exceeds exploratory ceiling ({self.max_view_tokens} tok)"
            )

        payload = json.dumps({
            "system": "\n".join(system_parts) if system_parts else None,
            "prompt": prompt,
            "valid_actions": list(valid_actions),
        })
        code, stdout, stderr = self.runner([str(self.binary_path)], payload, self.timeout_s)
        if code != 0:
            raise TransportError(f"hercyna-afm exited {code}: {stderr[:200]}")
        try:
            obj = json.loads(stdout.strip())
        except ValueError:
            return AfmProbeResult(
                status="parse_error",
                raw_reply=RawReply(text=stdout.strip(), input_tokens=est_in, output_tokens=0, estimated=True),
                detail="non_json_output",
            )
        status = str(obj.get("status") or "error")
        if status == "ok" and obj.get("action"):
            reply_json = json.dumps({"action": obj["action"], "reason": obj.get("reason") or ""})
            return AfmProbeResult(
                status="ok",
                raw_reply=RawReply(
                    text=reply_json,
                    input_tokens=est_in,
                    output_tokens=max(1, len(reply_json) // 4),
                    estimated=True,
                ),
            )
        return AfmProbeResult(
            status=status,
            raw_reply=RawReply(
                text="",
                input_tokens=est_in,
                output_tokens=0,
                estimated=True,
                context_truncated=(status == "context_exceeded"),
            ),
            detail=obj.get("detail"),
        )


__all__ = ["AFM_MAX_VIEW_TOKENS", "AfmProbeResult", "AfmCommandTransport"]
