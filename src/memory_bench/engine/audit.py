"""`bench audit views RUN`: stored views against failed memory-side calls (Day 14, review B3).

A product may swallow a refused or failed model call and still return a view, which is then thinner than it
should be. The view stage refuses to store a view when the proxy refused one of the item's calls at the cap, but
a call that failed in another way (an upstream error, a timeout) is not checked there. This audit checks every
stored view against memory_calls.jsonl.

For each stored view (the newest line per (case_id, system, config_label) in private/views.jsonl, as the view
store reads it) the ingest window runs from `at - ingest_seconds - retrieve_seconds - margin` to `at`. A
memory-side call of the same case, system and config label with a status other than 200 whose `at` (the proxy
stamps it when the call ends) falls inside the window marks the view.

private/views.jsonl holds view text. Only `case_id`, `system`, `config_label`, `at`, `ingest_seconds` and
`retrieve_seconds` are read from it; nothing else is kept, printed or written. view-audit.json holds counts,
statuses, labels and case ids only, so it passes the text-free scanner.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import files as F
from .views import views_path

VIEW_AUDIT = "view-audit.json"
SCHEMA = "memory_bench.view_audit/1"
MARGIN_SECONDS = 5.0
MEMORY_CALLS = "memory_calls.jsonl"


class AuditError(RuntimeError):
    """The run has no stored views or no memory-call log to audit."""


def _ts(s: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _lines(p: Path):
    for raw in p.read_bytes().split(b"\n"):
        if raw.strip():
            try:
                x = json.loads(raw)
            except ValueError:
                continue
            if isinstance(x, dict):
                yield x


def _stored_views(run_dir: Path) -> dict[tuple[str, str, str], dict]:
    """Newest line per key, reduced to the fields the audit needs (the text never leaves this function)."""
    out: dict[tuple[str, str, str], dict] = {}
    for line in _lines(views_path(run_dir)):
        cid = line.get("case_id")
        if not isinstance(cid, str):
            continue
        out[(cid, str(line.get("system")), str(line.get("config_label")))] = {
            "at": _ts(line.get("at")), "ingest_seconds": float(line.get("ingest_seconds") or 0.0),
            "retrieve_seconds": float(line.get("retrieve_seconds") or 0.0)}
    return out


def _status_key(s: Any) -> str:
    return "none" if s is None else str(s)


def audit_views(run_dir: str | Path, *, margin: float = MARGIN_SECONDS, write: bool = True) -> dict[str, Any]:
    """Counts of stored views with a failed memory-side call inside their ingest window. Writes view-audit.json."""
    run_dir = Path(run_dir)
    vp, cp = views_path(run_dir), run_dir / MEMORY_CALLS
    if not vp.exists():
        raise AuditError(f"{run_dir.name}: no private/views.jsonl (not a product run, or no view stored)")
    if not cp.exists():
        raise AuditError(f"{run_dir.name}: no {MEMORY_CALLS} (the run did not use the product proxy)")
    views = _stored_views(run_dir)

    failed: dict[tuple[str, str, str], list[datetime | None]] = {}
    by_status: dict[str, int] = {}
    n_calls = 0
    for c in _lines(cp):
        n_calls += 1
        if c.get("status") == 200:
            continue
        sk = _status_key(c.get("status"))
        by_status[sk] = by_status.get(sk, 0) + 1
        key = (str(c.get("case_id")), str(c.get("adapter")), str(c.get("config_label")))
        failed.setdefault(key, []).append(_ts(c.get("at")))

    systems: dict[str, dict[str, Any]] = {}

    def sys_slot(system: str, label: str) -> dict[str, Any]:
        return systems.setdefault(f"{system}/{label}", {
            "views_checked": 0, "views_with_failed_call": 0, "case_ids": [], "failed_calls_in_window": 0,
            "failed_calls": 0, "failed_calls_by_status": {}, "cases_failed_without_view": [],
            "views_unchecked": 0})

    for (cid, system, label), v in sorted(views.items()):
        s = sys_slot(system, label)
        if v["at"] is None:
            s["views_unchecked"] += 1  # no readable time: the window cannot be drawn
            continue
        s["views_checked"] += 1
        end = v["at"]
        start = end - timedelta(seconds=v["ingest_seconds"] + v["retrieve_seconds"] + margin)
        hits = sum(1 for t in failed.get((cid, system, label), []) if t is not None and start <= t <= end)
        if hits:
            s["views_with_failed_call"] += 1
            s["case_ids"].append(cid)
            s["failed_calls_in_window"] += hits

    for c in _lines(cp):  # per-system status counts (second pass keeps the first loop simple)
        if c.get("status") == 200:
            continue
        s = sys_slot(str(c.get("adapter")), str(c.get("config_label")))
        s["failed_calls"] += 1
        sk = _status_key(c.get("status"))
        s["failed_calls_by_status"][sk] = s["failed_calls_by_status"].get(sk, 0) + 1
    for (cid, system, label) in sorted(failed):
        if (cid, system, label) not in views:
            sys_slot(system, label)["cases_failed_without_view"].append(cid)

    for s in systems.values():
        s["failed_calls_by_status"] = dict(sorted(s["failed_calls_by_status"].items()))
        s["n_cases_failed_without_view"] = len(s["cases_failed_without_view"])
    out = {
        "schema": SCHEMA,
        "run_id": (F.read_json(run_dir / F.MANIFEST) if (run_dir / F.MANIFEST).exists() else {}).get("run_id"),
        "margin_seconds": margin,
        "memory_calls": n_calls,
        "views_checked": sum(s["views_checked"] for s in systems.values()),
        "views_with_failed_call": sum(s["views_with_failed_call"] for s in systems.values()),
        "failed_calls": sum(by_status.values()),
        "failed_calls_by_status": dict(sorted(by_status.items())),
        "cases_failed_without_view": sum(len(s["cases_failed_without_view"]) for s in systems.values()),
        "systems": dict(sorted(systems.items())),
    }
    if write:
        F.write_json(run_dir / VIEW_AUDIT, out)
    return out


__all__ = ["audit_views", "AuditError", "VIEW_AUDIT", "MARGIN_SECONDS"]
