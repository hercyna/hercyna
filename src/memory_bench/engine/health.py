"""`bench health rebuild RUN`: recompute health.json from every attempt (Day 14, review B3).

The engine writes health.json from its in-memory counters, so a resumed run's file covers only its last attempt:
a product whose views were all reused shows no ingest time and no product warnings. timings.jsonl is appended by
every attempt, so it holds the whole history. This rebuild reads it, plus metrics.jsonl for the cases done, and
writes health.json in the format of docs/run-files.md section 3.

What comes from where:
- `cases_done`: metrics.jsonl against the manifest's budgets and repeats (as the engine counts it);
- `ingest_seconds_mean`, `retrieve_seconds_mean`, `last_ok_at`: every ingest and retrieve row in timings.jsonl;
- `product_warnings.total`: the sum of the rows' `warnings`; `by_logger` from the rows' `warnings_by_logger`
  (written since Day 14). Rows from before carry only a total: their split comes from the stored views'
  by-logger counts in private/views.jsonl (counts only, no text is read out), and what no stored view explains
  (warnings of attempts whose view was not kept) is counted under the logger key `_unknown`;
- `errors`, `last_error`, `unattributed_warnings`: no row records them, so they are kept from the existing
  health.json (the last attempt's count). A product row adds `attempts`: items started (reset rows) and views
  stored, so items that ended without a view are visible;
- a top-level `rebuilt` key says the file was rebuilt and from what.

The manifest's `files` hash for health.json is updated the same way `rebuild_summary` updates the summary's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..submit.textfree import HEALTH_EXCEPTION
from . import files as F
from .views import views_path

UNKNOWN_LOGGER = "_unknown"


def sanitize_health_errors(health: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Replace any free-text exception message in `last_error.error` with exception class name + `error_sha256`."""
    changed = False
    adapters = health.get("adapters")
    if not isinstance(adapters, list):
        return health, False
    for entry in adapters:
        if not isinstance(entry, dict):
            continue
        le = entry.get("last_error")
        if not isinstance(le, dict):
            continue
        raw = le.get("error")
        if isinstance(raw, str) and not HEALTH_EXCEPTION.fullmatch(raw):
            cand = raw.split(":", 1)[0].strip()
            exc_cls = cand if HEALTH_EXCEPTION.fullmatch(cand) else "Exception"
            le["error"] = exc_cls
            le["error_sha256"] = F.sha256_text(raw)
            changed = True
    return health, changed


def _read_jsonl(p: Path) -> list[dict]:
    """Whole lines only; a torn or unreadable line is skipped."""
    if not p.exists():
        return []
    out = []
    for raw in p.read_bytes().split(b"\n"):
        if raw.strip():
            try:
                x = json.loads(raw)
            except ValueError:
                continue
            if isinstance(x, dict):
                out.append(x)
    return out


def _view_warnings(run_dir: Path) -> dict[tuple[str, str, str], tuple[dict[str, int], str]]:
    """(case_id, system, config_label) -> (by-logger counts, at) of the newest stored view. Reads no text field."""
    out: dict[tuple[str, str, str], tuple[dict[str, int], str]] = {}
    for line in _read_jsonl(views_path(run_dir)):
        w = line.get("warnings")
        by = (w or {}).get("by_logger") if isinstance(w, dict) else None
        if isinstance(line.get("case_id"), str):
            out[(line["case_id"], str(line.get("system")), str(line.get("config_label")))] = (
                {str(k): int(v) for k, v in by.items()} if isinstance(by, dict) else {}, str(line.get("at") or ""))
    return out


def _cases_done(manifest: dict, metrics: list[dict], entries: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    budgets = list(manifest.get("budgets") or [])
    repeats = int(manifest.get("repeats") or 0)
    keys = {F.row_key(r) for r in metrics}
    by_case: dict[str, set] = {}
    deciders = sorted({r["decider"] for r in metrics})
    for r in metrics:
        by_case.setdefault(r["case_id"], set())
    done: dict[tuple[str, str], int] = {k: 0 for k in entries}
    for cid in by_case:
        for did in deciders:
            for name, label in entries:
                if name == "full":
                    want = [(cid, did, "gate", name, label, None, 0)] + [
                        (cid, did, "floor", name, label, None, r) for r in range(1, repeats + 1)]
                else:
                    want = [(cid, did, "view", name, label, b, r) for b in budgets for r in range(1, repeats + 1)]
                if want and all(k in keys for k in want):
                    done[(name, label)] += 1
    return done


def rebuild_health(run_dir: str | Path) -> dict[str, Any]:
    """Recompute health.json from timings.jsonl (all attempts) and metrics.jsonl; returns the new health dict."""
    run_dir = Path(run_dir)
    m_path = run_dir / F.MANIFEST
    manifest = F.read_json(m_path)
    old = F.read_json(run_dir / F.HEALTH) if (run_dir / F.HEALTH).exists() else {}
    old_by = {(a.get("name"), a.get("config_label")): a for a in old.get("adapters") or []}
    timings = _read_jsonl(run_dir / F.TIMINGS)
    metrics = F.read_metrics(run_dir / F.METRICS)

    entries: list[tuple[str, str]] = []
    versions: dict[tuple[str, str], str] = {}
    for a in manifest.get("adapters") or []:
        k = (a["name"], a["config_label"])
        if k not in versions:
            entries.append(k)
            versions[k] = a.get("product_version")
    if ("full", "default") not in versions:  # older manifests list only the compared adapters
        entries.insert(0, ("full", "default"))
        versions[("full", "default")] = "builtin"
    for r in timings:  # a timing row of an adapter the manifest does not list is still reported
        k = (r.get("system"), r.get("config_label"))
        if k not in versions:
            entries.append(k)
            versions[k] = (old_by.get(k) or {}).get("product_version")

    done = _cases_done(manifest, metrics, entries)
    stored = _view_warnings(run_dir)

    adapters = []
    for k in entries:
        rows = [r for r in timings if (r.get("system"), r.get("config_label")) == k]
        ingest = [float(r["seconds"]) for r in rows if r.get("call") == "ingest"]
        retrieve = [float(r["seconds"]) for r in rows if r.get("call") == "retrieve"]
        product = any("warnings" in r for r in rows)
        by: dict[str, int] = {}
        total = 0
        legacy_total, legacy_cases = 0, set()
        for r in rows:
            n = int(r.get("warnings") or 0)
            total += n
            split = r.get("warnings_by_logger")
            if isinstance(split, dict):
                for name, c in split.items():
                    by[str(name)] = by.get(str(name), 0) + int(c)
            elif n:
                legacy_total += n
                legacy_cases.add(r.get("case_id"))
        if legacy_total:
            # A stored view's split belongs to the attempt that made it: the case's last ingest row at or before
            # the view's time. It is used only when that row is an old row (no split of its own).
            got: dict[str, int] = {}
            for cid in sorted(c for c in legacy_cases if isinstance(c, str)):
                view = stored.get((cid, k[0], k[1]))
                if view is None:
                    continue
                own = [r for r in rows if r.get("case_id") == cid and r.get("call") == "ingest"
                       and str(r.get("at") or "") <= view[1]]
                last = max(own, key=lambda r: str(r.get("at") or ""), default=None)
                if last is None or isinstance(last.get("warnings_by_logger"), dict):
                    continue
                for name, c in view[0].items():
                    got[name] = got.get(name, 0) + c
            explained = sum(got.values())
            if explained > legacy_total:  # never claim more than the rows counted
                got, explained = {}, 0
            for name, c in got.items():
                by[name] = by.get(name, 0) + c
            if legacy_total - explained:
                by[UNKNOWN_LOGGER] = by.get(UNKNOWN_LOGGER, 0) + legacy_total - explained
        ok_at = [str(r["at"]) for r in rows if r.get("call") == "retrieve" and r.get("at")]
        prev = old_by.get(k) or {}
        errors = int(prev.get("errors") or 0)
        e: dict[str, Any] = {
            "name": k[0], "config_label": k[1], "product_version": versions.get(k), "ok": errors == 0,
            "cases_done": done.get(k, 0), "errors": errors, "last_error": prev.get("last_error"),
            "ingest_seconds_mean": round(sum(ingest) / len(ingest), 6) if ingest else None,
            "retrieve_seconds_mean": round(sum(retrieve) / len(retrieve), 6) if retrieve else None,
            "last_ok_at": max(ok_at) if ok_at else prev.get("last_ok_at"),
            "product_warnings": {"total": total, "by_logger": dict(sorted(by.items()))},
        }
        if product:
            e["attempts"] = {"items": sum(1 for r in rows if r.get("call") == "reset"),
                             "stored_views": sum(1 for (_, s, c) in stored if (s, c) == k)}
        adapters.append(e)

    rep: dict[str, Any] = {"run_id": manifest.get("run_id") or old.get("run_id"), "updated": F.now(),
                           "adapters": adapters,
                           "rebuilt": {"from": ["timings.jsonl", "metrics.jsonl"], "timing_rows": len(timings)}}
    if old.get("unattributed_warnings"):
        rep["unattributed_warnings"] = old["unattributed_warnings"]
    sanitize_health_errors(rep)
    F.write_json(run_dir / F.HEALTH, rep)
    if manifest.get("files"):
        manifest["files"][F.HEALTH] = F.sha256_file(run_dir / F.HEALTH)
        F.write_json(m_path, manifest)
    return rep


__all__ = ["rebuild_health", "sanitize_health_errors", "UNKNOWN_LOGGER"]
