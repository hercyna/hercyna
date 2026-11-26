"""Text-free scanner for released run files (charter section 5 rule 8).

Released run files hold action names, flags, counts, timings and hashes. No view, note or prompt text.
A violation carries the length and sha256 of the offending string, never the string.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

RELEASED_FILES = ("metrics.jsonl", "calls.jsonl", "memory_calls.jsonl", "timings.jsonl", "summary.json", "spend.json",
                  "health.json", "manifest.json", "stability.json", "power.json", "view-audit.json", "acbi.json")


@dataclass(frozen=True)
class Violation:
    file: str
    line: int  # 1-based for jsonl, 0 for json
    path: str
    length: int
    sha256: str


# A token: an id, label, action name, file name, timestamp, version or hash. No white space, 64 characters at most.
TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/@+\-]{0,63}")
# Long provider/model slugs (e.g. OpenCode/MLX HF paths) allowed strictly on `.provider_model` up to 96 chars.
PROVIDER_MODEL_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/@+\-]{0,95}")
# Exception class name ONLY (no message, no colon, no whitespace) allowed strictly in health.json `.adapters[].last_error.error`.
HEALTH_EXCEPTION = re.compile(r"[A-Z][A-Za-z0-9_.]*(?:Error|Exception)")
# The only places where a sentence is expected. Matched on the end of the path. One line, 200 characters at most.
PROSE_PATHS = (".bootstrap.unit", ".price.source")
PROSE_MAX = 200


def is_free_text(path: str, value: str) -> bool:
    """True when `value`, found at dotted key path `path`, could be view, note or prompt text.

    Strict by choice: a string passes only as a token, an exception class name at
    `.adapters[].last_error.error`, or short prose at a known path. A new field that holds a
    sentence is flagged until its path is added here on purpose.
    """
    if value == "" or TOKEN.fullmatch(value):
        return False
    if path == ".adapters[].last_error.error" and len(value) <= 64 and HEALTH_EXCEPTION.fullmatch(value):
        return False
    if path.endswith(".provider_model") and PROVIDER_MODEL_TOKEN.fullmatch(value):
        return False
    if path.endswith(PROSE_PATHS):
        return len(value) > PROSE_MAX or "\n" in value or "\r" in value
    return True


def iter_strings(obj, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield (path, string) for every string value and every dict key. Lists add `[]` to the path."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}"
            yield sub, str(k)
            yield from iter_strings(v, sub)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v, path + "[]")


def _violations(name: str, line: int, obj) -> list[Violation]:
    out = []
    for path, s in iter_strings(obj):
        if is_free_text(path, s):
            out.append(Violation(name, line, path, len(s), hashlib.sha256(s.encode("utf-8")).hexdigest()))
    return out


def scan_file(p: Path) -> list[Violation]:
    p = Path(p)
    if p.suffix == ".jsonl":
        out: list[Violation] = []
        with p.open(encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                if raw.strip():
                    out.extend(_violations(p.name, i, json.loads(raw)))
        return out
    return _violations(p.name, 0, json.loads(p.read_text(encoding="utf-8")))


def scan_run(run_dir: str | Path) -> list[Violation]:
    out: list[Violation] = []
    for name in RELEASED_FILES:
        p = Path(run_dir) / name
        if p.exists():
            out.extend(scan_file(p))
    return out
