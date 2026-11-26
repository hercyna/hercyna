"""Run-file I/O (docs/run-files.md sections 2 and 3).

metrics.jsonl and summary.json carry no timestamps and are written canonically,
so two runs of the same config give the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..cases.schema import Case, dumps_case
from ..scoring.metrics import ROW_KEY, ROW_KEY_V2, row_key_v2

METRICS = "metrics.jsonl"
SUMMARY = "summary.json"
SPEND = "spend.json"
HEALTH = "health.json"
TIMINGS = "timings.jsonl"
MANIFEST = "manifest.json"
STABILITY = "stability.json"  # bench stability (docs/freeze.md section 5); not written by a run
POWER = "power.json"  # bench power (docs/freeze.md section 6); not written by a run
ACBI = "acbi.json"  # post-run text-free conflict blindness audit (v2)
RESULT_FILES = (METRICS, SUMMARY, SPEND, HEALTH, TIMINGS)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def cases_sha256(cases: Iterable[Case]) -> str:
    """sha256 of the canonical cases.jsonl bytes (what cases.schema.write_jsonl writes)."""
    return sha256_text("".join(dumps_case(c) + "\n" for c in cases))


def row_line(row: dict) -> str:
    """One canonical metrics.jsonl line, without the newline."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def row_key(row: dict) -> tuple:
    return tuple(row[k] for k in ROW_KEY)


def dumps_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def write_json(p: Path, obj: Any) -> None:
    """Atomic write: temp file in the same directory, then replace."""
    write_text(p, dumps_json(obj))


def write_text(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def read_metrics(p: Path, repair: bool = False) -> list[dict]:
    """Rows of metrics.jsonl. With repair, a torn last line (no newline) is cut off the file first."""
    if not p.exists():
        return []
    data = p.read_bytes()
    if data and not data.endswith(b"\n"):
        if not repair:
            raise ValueError(f"{p}: last line is incomplete")
        data = data[: data.rfind(b"\n") + 1]
        with p.open("wb") as fh:
            fh.write(data)
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def append_rows(p: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(row_line(r) + "\n" for r in rows))
        fh.flush()
        os.fsync(fh.fileno())


def append_jsonl(p: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
                         for r in rows))
