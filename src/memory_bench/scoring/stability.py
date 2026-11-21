"""Repeat stability (docs/freeze.md section 5, S9.2).

Per decider and system, over the decider's gated cases (gate action == a_star): how many cases got the same
action on every repeat, and how many later repeats differ from repeat 1. `full` is read from its floor rows
(repeats 1..R on the full history); every other system from its view rows. Text-free: counts and shares only.

`identical_cases`: gated cases with the same action on every repeat. `later_differ`: rows of repeats after the
first whose action differs from the first repeat's, out of `later_repeats` such rows.
"""

from __future__ import annotations

from typing import Any, Iterable

from .metrics import DEFAULT_CONFIG_LABEL, case_frame

STABILITY_SCHEMA = "memory_bench.stability/1"
FULL = "full"


def system_id(system: str, config_label: str) -> str:
    """The protocol's system name: `bm25` for a default config, `mem0/k25` for a labelled one."""
    return system if config_label == DEFAULT_CONFIG_LABEL else f"{system}/{config_label}"


def repeat_groups(rows: list[dict]) -> dict[str, dict]:
    """Repeat rows of one decider by system: {key: {"system", "budget", "rows"}}. `full` comes from the floor
    rows; view systems from view rows. A system with more than one budget gets one key per budget
    (`bm25@500`); with one budget the key is the system id alone."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["kind"] == "floor":
            groups.setdefault((FULL, None), []).append(r)
        elif r["kind"] == "view":
            groups.setdefault((system_id(r["system"], r["config_label"]), r["budget"]), []).append(r)
    budgets: dict[str, set] = {}
    for sid, b in groups:
        budgets.setdefault(sid, set()).add(b)
    out: dict[str, dict] = {}
    for (sid, b), rs in groups.items():
        key = sid if len(budgets[sid]) == 1 else f"{sid}@{'none' if b is None else b}"
        out[key] = {"system": sid, "budget": b, "rows": rs}
    return out


def _share(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def _stability(rows: list[dict], order: list[str], gated: set[str]) -> dict[str, Any]:
    by_case: dict[str, dict[int, str]] = {}
    for r in rows:
        if r["case_id"] in gated:
            by_case.setdefault(r["case_id"], {})[r["repeat"]] = r["action"]
    repeats = sorted({r["repeat"] for r in rows})
    cases = [c for c in order if c in by_case]
    identical = sum(len(set(by_case[c].values())) == 1 for c in cases)
    first = repeats[0] if repeats else None
    later = differ = 0
    for c in cases:
        acts = by_case[c]
        if first not in acts:
            continue
        for rep, a in acts.items():
            if rep != first:
                later += 1
                differ += a != acts[first]
    return {"gated_cases": len(cases), "identical_cases": identical, "identical_share": _share(identical, len(cases)),
            "repeats": repeats, "later_repeats": later, "later_differ": differ,
            "later_differ_share": _share(differ, later)}


def repeat_stability(rows: Iterable[dict]) -> dict[str, Any]:
    """The stability.json content for a list of metric rows. Pure and deterministic."""
    rows = list(rows)
    by_decider: dict[str, list[dict]] = {}
    for r in rows:
        by_decider.setdefault(r["decider"], []).append(r)
    deciders: dict[str, Any] = {}
    for did, rs in sorted(by_decider.items()):
        order, _idx, _a_star, _mech, _gate, gated = case_frame(rs)
        systems = {}
        for key, g in repeat_groups(rs).items():
            systems[key] = {"system": g["system"], "budget": g["budget"], **_stability(g["rows"], order, gated)}
        deciders[did] = {"n_cases": len(order), "n_gated": len(gated), "systems": systems}
    # Tokens only, so the file passes the text-free scanner; the definitions are in the module docstring.
    return {"schema": STABILITY_SCHEMA, "cases": "gated", "full_rows": "floor", "deciders": deciders}


__all__ = ["repeat_stability", "repeat_groups", "system_id", "STABILITY_SCHEMA"]
