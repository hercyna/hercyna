"""Step 9 ledger with append-only call-log adjustments and reconciliation audit (Round 7 A1).

Wraps frozen Step 8 ``memory_bench.engine.ledger_v2`` without modifying ``ledger_v2.py``,
``ledgers.py``, or any archived run directory.

Why Step 9 exists (R6 P2-1):
Two archived Round 5 runs paid for live decider calls before stopping on HTTP 402, leaving those
rows in ``calls.jsonl`` while their view-stage ``spend.json`` recorded only ``memory_side_usd``:
  - ``results/v2/table/chat_agent_r5_step7/opencode-qwen3.8-flash/mem0``: 378 paid rows, $0.352621
  - ``results/v2/table/chat_agent_r5_step7/haiku-5-5/langmem``: 104 paid rows, $0.063530

``configs/v2/ledger_adjustments.jsonl`` is an append-only ledger journal with one JSON object per
adjusted run: ``run_path``, ``ledger``, ``usd``, ``call_log``, and ``paid_rows``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import ledger_v2
from .ledger_v2 import (
    LEDGERS,
    SKIP_DIR_NAMES,
    _sibling_run_id,
    ledger_for_decider,
    ledger_limits,
    stamp_spend,
    validate_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULTS_ROOT = REPO_ROOT / "results" / "v2"
DEFAULT_ADJUSTMENTS_PATH = REPO_ROOT / "configs" / "v2" / "ledger_adjustments.jsonl"
CALL_LOG_NAMES = ("calls.jsonl", "memory_calls.jsonl")
REQUIRED_ADJUSTMENT_KEYS = ("run_path", "ledger", "usd", "call_log", "paid_rows")


def _is_pack_dir(run_dir: Path, root: Path) -> bool:
    rel_parts = run_dir.relative_to(root).parts
    if any(part in SKIP_DIR_NAMES for part in rel_parts):
        return True
    if (run_dir / "submission.json").is_file():
        return True
    return False


def resolve_adjustment_run_dir(run_path: str, results_root: Path) -> Path:
    """Resolve an adjustment ``run_path`` against ``results_root``.

    Accepts either repo-relative ``results/v2/<subpath>`` or ``results_root``-relative ``<subpath>``.
    """
    norm = run_path.strip().strip("/")
    prefix = "results/v2/"
    if norm.startswith(prefix):
        sub = norm[len(prefix) :]
        return results_root / sub
    p = Path(norm)
    if p.is_absolute():
        return p
    return results_root / p


def load_adjustments(adjustments_path: str | Path) -> list[dict[str, Any]]:
    """Load and validate append-only ledger adjustment rows from a JSONL file."""
    p = Path(adjustments_path)
    if not p.is_file():
        raise FileNotFoundError(f"adjustments file not found: {p}")
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{p}:{lineno}: adjustment row must be a JSON object")
        missing = [k for k in REQUIRED_ADJUSTMENT_KEYS if k not in obj]
        if missing:
            raise ValueError(f"{p}:{lineno}: missing required key(s): {', '.join(missing)}")
        run_path = str(obj["run_path"]).strip()
        if not run_path:
            raise ValueError(f"{p}:{lineno}: run_path must be non-empty")
        ledger = validate_ledger(str(obj["ledger"]))
        usd = float(obj["usd"])
        if usd <= 0.0:
            raise ValueError(f"{p}:{lineno}: usd must be > 0 (got {usd})")
        call_log = str(obj["call_log"]).strip()
        if call_log not in CALL_LOG_NAMES:
            raise ValueError(f"{p}:{lineno}: call_log must be one of {CALL_LOG_NAMES}, got {call_log!r}")
        paid_rows = int(obj["paid_rows"])
        if isinstance(obj["paid_rows"], bool) or paid_rows <= 0:
            raise ValueError(f"{p}:{lineno}: paid_rows must be a positive integer")
        dedup_key = (run_path, call_log)
        if dedup_key in seen_keys:
            raise ValueError(f"{p}:{lineno}: duplicate adjustment for ({run_path!r}, {call_log!r})")
        seen_keys.add(dedup_key)
        rows.append({
            "run_path": run_path,
            "ledger": ledger,
            "usd": round(usd, 6),
            "call_log": call_log,
            "paid_rows": paid_rows,
        })
    return rows


def _resolve_adjustments_file(
    results_root: Path,
    adjustments_path: str | Path | bool | None,
) -> Path | None:
    if adjustments_path is False:
        return None
    if isinstance(adjustments_path, (str, Path)):
        return Path(adjustments_path)
    if results_root.resolve() == DEFAULT_RESULTS_ROOT.resolve() and DEFAULT_ADJUSTMENTS_PATH.is_file():
        return DEFAULT_ADJUSTMENTS_PATH
    local_adj = results_root / "ledger_adjustments.jsonl"
    if local_adj.is_file():
        return local_adj
    return None


def sum_ledger_spend(
    results_root: str | Path = "results/v2",
    ledger: str = "main",
    *,
    adjustments_path: str | Path | bool | None = None,
    exclude_paths: set[Path] | None = None,
    exclude_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Sum deduplicated Step 8 ``spend.json`` totals plus Step 9 append-only call-log adjustments."""
    cap_usd, stop_usd = ledger_limits(ledger)
    root = Path(results_root)
    excluded = {p.resolve() for p in (exclude_paths or set())}

    base = ledger_v2.sum_ledger_spend(
        root,
        ledger,
        exclude_paths=exclude_paths,
        exclude_run_ids=exclude_run_ids,
    )

    adj_file = _resolve_adjustments_file(root, adjustments_path)
    all_adjustments = load_adjustments(adj_file) if adj_file is not None else []
    applied: list[dict[str, Any]] = []
    for row in all_adjustments:
        if row["ledger"] != ledger:
            continue
        adj_dir = resolve_adjustment_run_dir(row["run_path"], root).resolve()
        adj_spend = (adj_dir / "spend.json").resolve()
        if adj_dir in excluded or adj_spend in excluded:
            continue
        applied.append(row)

    adjustments_usd = round(sum(float(r["usd"]) for r in applied), 6)
    recorded_total = round(float(base["recorded_usd"]) + adjustments_usd, 6)
    effective_total = round(float(base["effective_usd"]) + adjustments_usd, 6)
    remaining_to_stop = round(max(0.0, stop_usd - effective_total), 6)
    remaining_to_cap = round(max(0.0, cap_usd - effective_total), 6)

    return {
        **base,
        "ledger_step": 9,
        "recorded_before_adjustments_usd": base["recorded_usd"],
        "effective_before_adjustments_usd": base["effective_usd"],
        "adjustments_usd": adjustments_usd,
        "n_adjustments": len(applied),
        "adjustments": applied,
        "recorded_usd": recorded_total,
        "effective_usd": effective_total,
        "remaining_to_stop_usd": remaining_to_stop,
        "remaining_to_cap_usd": remaining_to_cap,
        "at_or_past_stop": effective_total >= stop_usd - 1e-9,
    }


def run_headroom_usd(
    results_root: str | Path,
    ledger: str,
    *,
    run_dir: str | Path | None = None,
    max_run_usd: float | None = None,
    adjustments_path: str | Path | bool | None = None,
) -> float:
    """Maximum USD a single run folder may hold without passing ``ledger``'s ``stop_usd`` (Step 9)."""
    exclude: set[Path] = set()
    exclude_rids: set[str] = set()
    if run_dir is not None:
        rd = Path(run_dir)
        exclude.add(rd)
        sp = rd / "spend.json"
        if sp.exists():
            exclude.add(sp)
        rid = _sibling_run_id(sp)
        if rid is not None:
            exclude_rids.add(rid)
    summary = sum_ledger_spend(
        results_root,
        ledger,
        adjustments_path=adjustments_path,
        exclude_paths=exclude,
        exclude_run_ids=exclude_rids,
    )
    headroom = float(summary["remaining_to_stop_usd"])
    if max_run_usd is not None:
        headroom = min(headroom, float(max_run_usd))
    return round(max(0.0, headroom), 6)


def sum_paid_uncached_log(log_path: Path) -> tuple[int, float]:
    """Return ``(paid_uncached_count, paid_uncached_usd)`` for a ``calls.jsonl`` or ``memory_calls.jsonl`` file."""
    if not log_path.is_file():
        return 0, 0.0
    count = 0
    total = 0.0
    for lineno, raw in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{log_path}:{lineno}: call-log row must be a JSON object")
        usd = float(row.get("usd") or 0.0)
        if not row.get("cached") and usd > 0.0:
            count += 1
            total += usd
    return count, round(total, 6)


def audit_call_logs_vs_spend(
    results_root: str | Path = "results/v2",
    *,
    adjustments_path: str | Path | bool | None = None,
    tolerance_usd: float = 0.005,
) -> dict[str, Any]:
    """Audit every non-pack run folder under ``results_root``: sum paid, uncached rows across
    ``calls.jsonl`` and ``memory_calls.jsonl``, and report any folder whose paid call-log sum
    exceeds ``spend.json['total_usd'] + adjustments`` by more than ``tolerance_usd``."""
    root = Path(results_root)
    adj_file = _resolve_adjustments_file(root, adjustments_path)
    adjustments = load_adjustments(adj_file) if adj_file is not None else []

    adj_by_dir: dict[Path, float] = {}
    adjustment_mismatches: list[dict[str, Any]] = []
    for row in adjustments:
        rdir = resolve_adjustment_run_dir(row["run_path"], root).resolve()
        adj_by_dir[rdir] = round(adj_by_dir.get(rdir, 0.0) + float(row["usd"]), 6)
        log_file = rdir / row["call_log"]
        if log_file.is_file():
            paid_n, paid_usd = sum_paid_uncached_log(log_file)
            if paid_n != row["paid_rows"] or abs(paid_usd - float(row["usd"])) > 0.0005:
                adjustment_mismatches.append({
                    "run_path": row["run_path"],
                    "call_log": row["call_log"],
                    "expected_paid_rows": row["paid_rows"],
                    "actual_paid_rows": paid_n,
                    "expected_usd": row["usd"],
                    "actual_usd": paid_usd,
                })

    run_dirs: set[Path] = set()
    if root.exists():
        for fname in ("spend.json", *CALL_LOG_NAMES):
            for p in root.rglob(fname):
                if not _is_pack_dir(p.parent, root):
                    run_dirs.add(p.parent)

    checked: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for d in sorted(run_dirs):
        sp_path = d / "spend.json"
        spend_usd = 0.0
        if sp_path.is_file():
            sp_data = json.loads(sp_path.read_text(encoding="utf-8"))
            if isinstance(sp_data, dict):
                spend_usd = round(float(sp_data.get("total_usd") or 0.0), 6)
        adj_usd = adj_by_dir.get(d.resolve(), 0.0)
        accounted_usd = round(spend_usd + adj_usd, 6)

        calls_n, calls_usd = sum_paid_uncached_log(d / "calls.jsonl")
        mem_n, mem_usd = sum_paid_uncached_log(d / "memory_calls.jsonl")
        paid_log_usd = round(calls_usd + mem_usd, 6)
        excess_usd = round(paid_log_usd - accounted_usd, 6)

        rec = {
            "run_dir": d.as_posix(),
            "spend_usd": spend_usd,
            "adjustment_usd": adj_usd,
            "accounted_usd": accounted_usd,
            "paid_calls_rows": calls_n,
            "paid_calls_usd": calls_usd,
            "paid_memory_calls_rows": mem_n,
            "paid_memory_calls_usd": mem_usd,
            "paid_call_logs_usd": paid_log_usd,
            "excess_usd": excess_usd,
        }
        checked.append(rec)
        if excess_usd > tolerance_usd + 1e-9:
            violations.append(rec)

    return {
        "results_root": root.as_posix(),
        "tolerance_usd": tolerance_usd,
        "dirs_checked": len(checked),
        "violations": violations,
        "adjustment_mismatches": adjustment_mismatches,
        "ok": not violations and not adjustment_mismatches,
        "runs": checked,
    }


__all__ = [
    "LEDGERS",
    "SKIP_DIR_NAMES",
    "DEFAULT_ADJUSTMENTS_PATH",
    "REQUIRED_ADJUSTMENT_KEYS",
    "validate_ledger",
    "ledger_limits",
    "ledger_for_decider",
    "stamp_spend",
    "load_adjustments",
    "resolve_adjustment_run_dir",
    "sum_ledger_spend",
    "run_headroom_usd",
    "sum_paid_uncached_log",
    "audit_call_logs_vs_spend",
]
