"""Deduplicated two-ledger Phase 1 spend tracking (Round 6 A1 / Step 8).

Wraps frozen Step 5 ``memory_bench.engine.ledgers`` without modifying it. Fixes R5 P1-1 where
``ledgers.sum_ledger_spend`` walked ``_packs/`` copies created by ``bench submit pack`` and
double-counted every finished run.

Deduplication rules:
1. Skip any ``spend.json`` under ``_packs/``, ``packs/``, ``dryrun-pack/``, ``submissions/``,
   or ``private/``, and skip any folder containing ``submission.json`` (a packed copy).
2. Deduplicate remaining files by sibling ``manifest.json``'s ``run_id`` (when present), keeping
   the single entry with the highest effective USD so no run can ever be counted twice.
3. Count R1–R4 files that carry no ``ledger`` key on ``main`` (unless their decider/path is
   ``sonnet``), preserving the Qwen ``metering_note`` effective spend correction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledgers import (
    LEDGERS,
    _effective_usd,
    _file_ledger,
    ledger_for_decider,
    ledger_limits,
    stamp_spend,
    validate_ledger,
)

SKIP_DIR_NAMES = frozenset({"dryrun-pack", "submissions", "private", "_packs", "packs"})


def _is_pack_copy(spend_path: Path, root: Path) -> bool:
    rel_parts = spend_path.relative_to(root).parts
    if any(part in SKIP_DIR_NAMES for part in rel_parts):
        return True
    if (spend_path.parent / "submission.json").is_file():
        return True
    return False


def _sibling_run_id(spend_path: Path) -> str | None:
    mp = spend_path.parent / "manifest.json"
    if not mp.is_file():
        return None
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        rid = data.get("run_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
    return None


def sum_ledger_spend(
    results_root: str | Path = "results/v2",
    ledger: str = "main",
    *,
    exclude_paths: set[Path] | None = None,
    exclude_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Sum unique ``spend.json`` files belonging to ``ledger`` under ``results_root``.

    Never double-counts ``_packs/`` or ``submission.json`` copies, and deduplicates by ``run_id``.
    """
    cap_usd, stop_usd = ledger_limits(ledger)
    root = Path(results_root)
    excluded = {p.resolve() for p in (exclude_paths or set())}
    excluded_rids = {r.strip() for r in (exclude_run_ids or set()) if r and r.strip()}

    pack_copies_skipped = 0
    deduped_skipped = 0
    by_run_id: dict[str, dict[str, Any]] = {}
    unkeyed_entries: list[dict[str, Any]] = []

    if root.exists():
        for p in sorted(root.rglob("spend.json")):
            if _is_pack_copy(p, root):
                pack_copies_skipped += 1
                continue
            if p.resolve() in excluded:
                continue
            rid = _sibling_run_id(p)
            if rid is not None and rid in excluded_rids:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if _file_ledger(p, data) != ledger:
                continue
            rec, eff = _effective_usd(data)
            entry: dict[str, Any] = {
                "path": p.as_posix(),
                "run_id": rid,
                "recorded_usd": round(rec, 6),
                "effective_usd": round(eff, 6),
            }
            if rid is not None:
                prev = by_run_id.get(rid)
                if prev is None:
                    by_run_id[rid] = entry
                else:
                    deduped_skipped += 1
                    if entry["effective_usd"] > prev["effective_usd"]:
                        by_run_id[rid] = entry
            else:
                unkeyed_entries.append(entry)

    entries = sorted(
        [*by_run_id.values(), *unkeyed_entries],
        key=lambda e: str(e["path"]),
    )
    recorded_total = round(sum(float(e["recorded_usd"]) for e in entries), 6)
    effective_total = round(sum(float(e["effective_usd"]) for e in entries), 6)
    remaining_to_stop = round(max(0.0, stop_usd - effective_total), 6)
    remaining_to_cap = round(max(0.0, cap_usd - effective_total), 6)
    return {
        "ledger": ledger,
        "ledger_step": 8,
        "cap_usd": cap_usd,
        "stop_usd": stop_usd,
        "recorded_usd": recorded_total,
        "effective_usd": effective_total,
        "remaining_to_stop_usd": remaining_to_stop,
        "remaining_to_cap_usd": remaining_to_cap,
        "at_or_past_stop": effective_total >= stop_usd - 1e-9,
        "n_files": len(entries),
        "pack_copies_skipped": pack_copies_skipped,
        "deduped_skipped": deduped_skipped,
        "files": entries,
    }


def run_headroom_usd(
    results_root: str | Path,
    ledger: str,
    *,
    run_dir: str | Path | None = None,
    max_run_usd: float | None = None,
) -> float:
    """Maximum USD a single run folder may hold (including spend already in ``run_dir``) without
    passing ``ledger``'s ``stop_usd``, using deduplicated Step 8 ledger sums."""
    exclude: set[Path] = set()
    exclude_rids: set[str] = set()
    if run_dir is not None:
        sp = Path(run_dir) / "spend.json"
        if sp.exists():
            exclude.add(sp)
        rid = _sibling_run_id(sp)
        if rid is not None:
            exclude_rids.add(rid)
    summary = sum_ledger_spend(results_root, ledger, exclude_paths=exclude, exclude_run_ids=exclude_rids)
    headroom = float(summary["remaining_to_stop_usd"])
    if max_run_usd is not None:
        headroom = min(headroom, float(max_run_usd))
    return round(max(0.0, headroom), 6)


__all__ = [
    "LEDGERS",
    "SKIP_DIR_NAMES",
    "validate_ledger",
    "ledger_limits",
    "ledger_for_decider",
    "stamp_spend",
    "sum_ledger_spend",
    "run_headroom_usd",
]
