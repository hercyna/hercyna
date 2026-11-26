"""Hermetic stdin-based subprocess runner for OpenCode Zen CLI calls (R3 A2 & B3).

Injected via `load_decider(..., runner=opencode_zen_stdin_runner)` so frozen
`src/memory_bench/deciders/transports.py` and `protocol/v2.json` Step 1 stay untouched
while delivering two critical runtime behaviors verified on `opencode v2.3.0`:
1. Prompt passed on `stdin` instead of argv (B3), avoiding shell/argv size ceilings.
2. Selective permission denial (`bash`, `webfetch`, `websearch`, `edit`, `write`, `task`
   denied) inside an empty per-call temp `work/` cwd, preventing OpenCode Zen's
   free-tier anti-abuse check from issuing false-positive HTTP 429 (`FreeUsageLimitError`)
   when `"tools": {"*": false}` strips the entire tool schema, while blocking all shell,
   file-mutation, and network tools and never touching `~/.config/opencode` or `:49374`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Mapping

SELECTIVE_DENY_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
        "bash": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "edit": "deny",
        "write": "deny",
        "task": "deny",
    },
}


def opencode_zen_stdin_runner(
    argv: list[str],
    stdin: str | None,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run `opencode run --standalone --format json --model <m>` with prompt on stdin."""
    cmd = list(argv)
    prompt_text = stdin
    # OpenCodeCliTransport.argv() appends prompt_text as the final positional arg after `--model <m>`.
    if prompt_text is None and "--model" in cmd:
        m_idx = cmd.index("--model")
        if len(cmd) > m_idx + 2:
            prompt_text = cmd.pop()

    child_env = dict(env) if env is not None else None
    work_dir: Path | None = None
    if child_env is not None and "XDG_CONFIG_HOME" in child_env:
        cfg_home = Path(child_env["XDG_CONFIG_HOME"])
        oc_dir = cfg_home / "opencode"
        oc_dir.mkdir(parents=True, exist_ok=True)
        (oc_dir / "opencode.jsonc").write_text(
            json.dumps(SELECTIVE_DENY_CONFIG, indent=2) + "\n",
            encoding="utf-8",
        )
        child_env.pop("OPENCODE_CONFIG_DIR", None)
        work_dir = cfg_home.parent / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            cmd,
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
            cwd=str(work_dir) if work_dir is not None else None,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"command {cmd[:5]} timed out after {timeout}s") from None
    return proc.returncode, proc.stdout, proc.stderr


__all__ = ["opencode_zen_stdin_runner", "SELECTIVE_DENY_CONFIG"]
