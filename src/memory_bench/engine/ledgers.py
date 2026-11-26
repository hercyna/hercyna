"""Two-ledger Phase 1 spend tracking and cap enforcement (Round 5 A1).

Two completely separate ledgers — funds are NEVER moved between them:
- ``main``:   cap_usd = 60.0, stop_usd = 55.0 (all non-Sonnet deciders, shared extractor & embedder, R1-R4 spend)
- ``sonnet``: cap_usd = 20.0, stop_usd = 18.0 (Claude Sonnet 5.5 gate and table runs only)

Every ``spend.json`` written in Round 5+ carries ``ledger``, ``cap_usd``, and ``stop_usd`` (all
strictly text-free tokens/numbers). Each ledger sums ONLY its own ``spend.json`` files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

LEDGERS: dict[str, dict[str, float]] = {
    "main": {"cap_usd": 60.0, "stop_usd": 55.0},
    "sonnet": {"cap_usd": 20.0, "stop_usd": 18.0},
}

SKIP_DIR_NAMES = frozenset({"dryrun-pack", "submissions", "private"})


def validate_ledger(ledger: str) -> str:
    if ledger not in LEDGERS:
        raise ValueError(f"unknown ledger {ledger!r}; expected one of {sorted(LEDGERS)}")
    return ledger


def ledger_limits(ledger: str) -> tuple[float, float]:
    """Return (cap_usd, stop_usd) for the named ledger."""
    validate_ledger(ledger)
    info = LEDGERS[ledger]
    return float(info["cap_usd"]), float(info["stop_usd"])


def ledger_for_decider(decider_id: str) -> str:
    """Route Sonnet runs to the 'sonnet' ledger and everything else to 'main'."""
    low = str(decider_id or "").strip().lower()
    if low == "sonnet-5-5" or low.startswith("sonnet-") or "claude-sonnet" in low:
        return "sonnet"
    return "main"


def stamp_spend(
    spend: Mapping[str, Any],
    ledger: str,
    *,
    run_cap_usd: float | None = None,
) -> dict[str, Any]:
    """Stamp ``ledger``, ``cap_usd``, and ``stop_usd`` into a spend.json object.

    Preserves the Gateway's enforced per-run stop headroom in ``run_cap_usd`` when distinct
    from the ledger's ``cap_usd``. All keys and values are strictly text-free.
    """
    cap_usd, stop_usd = ledger_limits(ledger)
    out = dict(spend)
    prev_cap = out.get("cap_usd")
    out["ledger"] = ledger
    out["cap_usd"] = cap_usd
    out["stop_usd"] = stop_usd
    if run_cap_usd is not None:
        out["run_cap_usd"] = round(float(run_cap_usd), 6)
    elif isinstance(prev_cap, (int, float)) and abs(float(prev_cap) - cap_usd) > 1e-9:
        out["run_cap_usd"] = round(float(prev_cap), 6)
    return out


def _file_ledger(path: Path, data: Mapping[str, Any]) -> str:
    explicit = data.get("ledger")
    if isinstance(explicit, str) and explicit in LEDGERS:
        return explicit
    by_dec = data.get("by_decider")
    if isinstance(by_dec, Mapping) and by_dec:
        if all(ledger_for_decider(did) == "sonnet" for did in by_dec):
            return "sonnet"
        return "main"
    for part in path.parts:
        if ledger_for_decider(part) == "sonnet":
            return "sonnet"
    return "main"


def _effective_usd(data: Mapping[str, Any]) -> tuple[float, float]:
    """Return (recorded_usd, effective_usd) accounting for any Qwen metering_note correction."""
    recorded = float(data.get("total_usd") or 0.0)
    note = data.get("metering_note")
    if isinstance(note, Mapping) and isinstance(note.get("corrected_total_usd"), (int, float)):
        effective = max(recorded, float(note["corrected_total_usd"]))
        return recorded, effective
    return recorded, recorded


def sum_ledger_spend(
    results_root: str | Path = "results/v2",
    ledger: str = "main",
    *,
    exclude_paths: set[Path] | None = None,
) -> dict[str, Any]:
    """Sum all ``spend.json`` files belonging to ``ledger`` under ``results_root``.

    Skips packed copies under ``dryrun-pack/`` or ``submissions/`` so no run is double-counted.
    Uses ``effective_usd`` (incorporating Qwen's corrected metering note) to compute conservative
    remaining headroom to ``stop_usd``.
    """
    cap_usd, stop_usd = ledger_limits(ledger)
    root = Path(results_root)
    excluded = {p.resolve() for p in (exclude_paths or set())}
    recorded_total = 0.0
    effective_total = 0.0
    entries: list[dict[str, Any]] = []

    if root.exists():
        for p in sorted(root.rglob("spend.json")):
            if any(part in SKIP_DIR_NAMES for part in p.relative_to(root).parts):
                continue
            if p.resolve() in excluded:
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
            recorded_total += rec
            effective_total += eff
            entries.append({
                "path": p.as_posix(),
                "recorded_usd": round(rec, 6),
                "effective_usd": round(eff, 6),
            })

    recorded_total = round(recorded_total, 6)
    effective_total = round(effective_total, 6)
    remaining_to_stop = round(max(0.0, stop_usd - effective_total), 6)
    remaining_to_cap = round(max(0.0, cap_usd - effective_total), 6)
    return {
        "ledger": ledger,
        "cap_usd": cap_usd,
        "stop_usd": stop_usd,
        "recorded_usd": recorded_total,
        "effective_usd": effective_total,
        "remaining_to_stop_usd": remaining_to_stop,
        "remaining_to_cap_usd": remaining_to_cap,
        "at_or_past_stop": effective_total >= stop_usd - 1e-9,
        "n_files": len(entries),
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
    passing ``ledger``'s ``stop_usd``.

    When resuming an existing ``run_dir``, its own ``spend.json`` is excluded from external ledger
    spend because ``Gateway``/``ProductProxy`` already load ``run_dir``'s own spend into their
    in-run meter.
    """
    exclude: set[Path] = set()
    if run_dir is not None:
        sp = Path(run_dir) / "spend.json"
        if sp.exists():
            exclude.add(sp)
    summary = sum_ledger_spend(results_root, ledger, exclude_paths=exclude)
    headroom = float(summary["remaining_to_stop_usd"])
    if max_run_usd is not None:
        headroom = min(headroom, float(max_run_usd))
    return round(max(0.0, headroom), 6)


__all__ = [
    "LEDGERS",
    "validate_ledger",
    "ledger_limits",
    "ledger_for_decider",
    "stamp_spend",
    "sum_ledger_spend",
    "run_headroom_usd",
]
