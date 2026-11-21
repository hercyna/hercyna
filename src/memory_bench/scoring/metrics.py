"""summary.json from metric rows (docs/run-files.md sections 2 and 4, CHARTER section 4).

Per decider:
- case order is the order of first appearance of case_id among the decider's rows;
- G = cases whose gate row has action == a_star; the rest are excluded and counted;
- every cell (system, config_label, budget) of view rows, and the floor (the decider's floor rows),
  goes through the same code path: per-case integer features, each rate a ratio of feature sums;
- one Boot over all cases of the decider is shared by every cell, the floor and every repeat,
  so headline = wrong_commit - floor.wrong_commit is a paired difference.

The denominator of a rate is the number of gated (case, repeat) slots present for the cell.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from ..cases.schema import REQUEST_INFO
from ..deciders.lifted import PARSE_ERROR
from .bootstrap import Boot, ratio
from .gates import gate_report, is_committed, validity_gate

SUMMARY_SCHEMA = "memory_bench.summary/1"
KINDS = ("gate", "floor", "view")
ROW_KEY = ("case_id", "decider", "kind", "system", "config_label", "budget", "repeat")
DEFAULT_CONFIG_LABEL = "default"

# Feature names follow LENS where LENS had them (wc, ab, hpe, h, g, one).
RATES = {
    "wrong_commit": ("wc", "g"),
    "abstention": ("ab", "g"),
    "parse_error": ("hpe", "g"),
    "harmful": ("h", "g"),
    "right_action": ("ok", "g"),
    "false_closure_rate": ("fc", "com"),
}


class ScoringError(ValueError):
    """The metric rows are incomplete or inconsistent."""


# ---------------------------------------------------------------------------
# Legacy conversion
# ---------------------------------------------------------------------------

def legacy_rows_to_metric_rows(rows: Iterable[dict], decider: str = "haiku-5-5-lens-v3",
                               track: str = "account_review") -> list[dict]:
    """LENS decision rows (one per case and cell) to metric rows.

    One gate row per case (system full, repeat 0, action a_H, budget null), emitted at the case's
    first appearance, then one view row per legacy row (repeat 1, action a_V). No floor rows.
    """
    out: list[dict] = []
    a_h: dict[str, str] = {}
    for r in rows:
        cid = r["case_id"]
        if cid in a_h:
            if a_h[cid] != r["a_H"]:
                raise ScoringError(f"case {cid}: a_H differs across cells ({a_h[cid]!r} vs {r['a_H']!r})")
        else:
            a_h[cid] = r["a_H"]
            out.append({"case_id": cid, "track": track, "mechanism": r["mechanism"], "a_star": r["a_star"],
                        "decider": decider, "kind": "gate", "system": "full",
                        "config_label": DEFAULT_CONFIG_LABEL, "budget": None, "repeat": 0, "action": r["a_H"],
                        "view_tokens": 0, "view_units": 0, "view_sha256": ""})
        out.append({"case_id": cid, "track": track, "mechanism": r["mechanism"], "a_star": r["a_star"],
                    "decider": decider, "kind": "view", "system": r["system"],
                    "config_label": DEFAULT_CONFIG_LABEL, "budget": int(r["budget"]), "repeat": 1,
                    "action": r["a_V"], "view_tokens": int(r["view_tokens"]), "view_units": int(r["view_units"]),
                    "view_sha256": ""})
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _row_key(r: dict) -> tuple:
    return tuple(r[k] for k in ROW_KEY)


def cell_name(system: str, config_label: str, budget: int | None) -> str:
    """Cell key in summary.json: `<system>/<config_label>@<budget>` ("none" for a null budget)."""
    return f"{system}/{config_label}@{'none' if budget is None else budget}"


def _features(n: int, idx: dict[str, int], rows: list[dict], gated: set[str]) -> dict[str, list[float]]:
    names = ("wc", "ab", "hpe", "h", "ok", "g", "one", "com", "fc")
    feats = {k: [0.0] * n for k in names}
    for r in rows:
        i = idx[r["case_id"]]
        a, star = r["action"], r["a_star"]
        feats["one"][i] += 1
        if r["case_id"] not in gated:
            continue
        com = is_committed(a)
        feats["g"][i] += 1
        feats["ok"][i] += a == star
        feats["h"][i] += a != star
        feats["wc"][i] += com and a != star
        feats["ab"][i] += a == REQUEST_INFO and star != REQUEST_INFO
        feats["hpe"][i] += a == PARSE_ERROR
        feats["com"][i] += com
        feats["fc"][i] += com and a != star
    return feats


def _rates(boot: Boot, feats: dict[str, list[float]], cache: list) -> dict[str, Any]:
    return {name: boot.stat(feats, ratio(num, den), cache) for name, (num, den) in RATES.items()}


def _view(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    return {"mean_tokens": sum(r["view_tokens"] for r in rows) / n if n else None,
            "mean_units": sum(r["view_units"] for r in rows) / n if n else None}


def _accuracy(cases: list[str], a_gate: dict[str, str], a_star: dict[str, str]) -> dict[str, Any]:
    ok = sum(a_gate[c] == a_star[c] for c in cases)
    return {"n": len(cases), "correct": ok, "accuracy": ok / len(cases) if cases else None}


def case_frame(rows: list[dict]) -> tuple[list[str], dict[str, int], dict[str, str], dict[str, str],
                                         dict[str, str], set[str]]:
    """For one decider's rows: case order (first appearance), index, a_star, mechanism, gate action, gated set.
    The bootstrap is Boot(len(order), reps, seed) over this order (shared with scoring.separation)."""
    order: list[str] = []
    meta: dict[str, tuple[str, str]] = {}
    for r in rows:
        cid = r["case_id"]
        m = (r["a_star"], r["mechanism"])
        if cid not in meta:
            meta[cid] = m
            order.append(cid)
        elif meta[cid] != m:
            raise ScoringError(f"case {cid}: a_star or mechanism differs across rows ({meta[cid]} vs {m})")
    idx = {c: i for i, c in enumerate(order)}
    a_star = {c: meta[c][0] for c in order}
    mech = {c: meta[c][1] for c in order}

    gate = {r["case_id"]: r["action"] for r in rows if r["kind"] == "gate"}
    missing = [c for c in order if c not in gate]
    if missing:
        raise ScoringError(f"{len(missing)} case(s) have no gate row, first {missing[0]!r}")
    gated = {c for c in order if gate[c] == a_star[c]}
    return order, idx, a_star, mech, gate, gated


def _decider_summary(rows: list[dict], seed: int, reps: int) -> dict[str, Any]:
    order, idx, a_star, mech, gate, gated = case_frame(rows)

    overall = _accuracy(order, gate, a_star)
    per_mech = {m: _accuracy([c for c in order if mech[c] == m], gate, a_star)
                for m in sorted(set(mech.values()))}
    accs = {m: a["accuracy"] for m, a in per_mech.items()}
    excluded = [c for c in order if c not in gated]

    boot = Boot(len(order), reps, seed)

    floor_rows = [r for r in rows if r["kind"] == "floor"]
    floor = None
    floor_feats = floor_cache = None
    if floor_rows:
        floor_feats = _features(len(order), idx, floor_rows, gated)
        floor_cache = boot.resampled_sums(floor_feats)
        floor = {**_rates(boot, floor_feats, floor_cache), "view": _view(floor_rows),
                 "repeats": sorted({r["repeat"] for r in floor_rows})}

    by_cell: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["kind"] == "view":
            by_cell.setdefault((r["system"], r["config_label"], r["budget"]), []).append(r)

    cells: dict[str, Any] = {}
    for (system, label, budget), crows in by_cell.items():
        feats = _features(len(order), idx, crows, gated)
        cache = boot.resampled_sums(feats)
        cell: dict[str, Any] = {"system": system, "config_label": label, "budget": budget,
                                **_rates(boot, feats, cache), "view": _view(crows),
                                "repeats": sorted({r["repeat"] for r in crows})}
        if floor_feats is not None:
            cell["headline"] = boot.paired_diff(feats, ratio("wc", "g"), floor_feats, ratio("wc", "g"),
                                                cache, floor_cache)
            cell["abstention_above_floor"] = boot.paired_diff(feats, ratio("ab", "g"), floor_feats,
                                                              ratio("ab", "g"), cache, floor_cache)
        else:
            cell["headline"] = cell["abstention_above_floor"] = None
        cells[cell_name(system, label, budget)] = cell

    return {
        "n_cases": len(order),
        "n_gated": len(gated),
        "excluded_from_gated": {"count": len(excluded),
                                "by_mechanism": dict(sorted(Counter(mech[c] for c in excluded).items())),
                                "gate_parse_errors": sum(gate[c] == PARSE_ERROR for c in excluded)},
        "validity": {"overall": overall, "per_mechanism": per_mech,
                     "passes": validity_gate(overall["accuracy"], accs),
                     "gate": gate_report(overall["accuracy"], accs)},
        "floor": floor,
        "cells": cells,
    }


def _check(rows: list[dict]) -> None:
    seen: set[tuple] = set()
    for r in rows:
        if r["kind"] not in KINDS:
            raise ScoringError(f"unknown row kind {r['kind']!r} for case {r['case_id']!r}")
        k = _row_key(r)
        if k in seen:
            raise ScoringError(f"duplicate row key {dict(zip(ROW_KEY, k))}")
        seen.add(k)


def summarise(rows: Iterable[dict], *, seed: int = 11, reps: int = 1000) -> dict[str, Any]:
    """The summary.json content for a list of metric rows. Deterministic in the rows and the seed."""
    rows = list(rows)
    _check(rows)
    by_decider: dict[str, list[dict]] = {}
    for r in rows:
        by_decider.setdefault(r["decider"], []).append(r)
    return {
        "schema": SUMMARY_SCHEMA,
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95,
                      "unit": "case (paired: every cell, the floor and every repeat of a resampled case come along)"},
        "deciders": {d: _decider_summary(rs, seed, reps) for d, rs in sorted(by_decider.items())},
    }
