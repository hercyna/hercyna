"""Temporal Half-Life, Recency-Bias Inversion Rate (RBIR) & Expiry Blindness Rate (EBR) (v2).

Stratifies wrong-action rates across distance buckets (`near` / `middle` / `far`) and reports
override inversion (C1 / H1 / M1) and expiry blindness (C2 / H2 / M2).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..cases.schema import Case
from .bootstrap import Boot
from .gates import is_committed
from .metrics import cell_name

TEMPORAL_SCHEMA = "memory_bench.temporal/1"
OVERRIDE_MECHANISMS = frozenset({"M1", "C1", "H1"})
EXPIRY_MECHANISMS = frozenset({"M2", "C2", "H2"})
DISTANCE_BUCKETS = ("near", "middle", "far")


def _distance_of(case: Case) -> str:
    meta = getattr(case, "metadata", None) or {}
    d = str(meta.get("distance") or "").lower()
    if d in DISTANCE_BUCKETS:
        return d
    lag = meta.get("override_lag_events")
    if isinstance(lag, (int, float)):
        if lag <= 10:
            return "near"
        if lag <= 30:
            return "middle"
        return "far"
    return "unknown"


def _rate_over_subset(
    cases: list[str],
    subset_ids: set[str],
    rows_by_case: Mapping[str, list[dict]],
    boot: Boot,
) -> dict[str, Any]:
    wc_col: list[float] = []
    den_col: list[float] = []
    for cid in cases:
        if cid not in subset_ids:
            wc_col.append(0.0)
            den_col.append(0.0)
            continue
        rs = rows_by_case.get(cid, ())
        wc = sum(1.0 for r in rs if is_committed(r["action"]) and r["action"] != r["a_star"])
        wc_col.append(wc)
        den_col.append(float(len(rs)))
    feats = {"wc": wc_col, "den": den_col}
    return {
        "n_cases": len(subset_ids),
        **boot.stat(feats, lambda s: s["wc"] / s["den"] if s["den"] > 0 else None),
    }


def compute_temporal(
    cases: Sequence[Case],
    rows: Iterable[dict],
    *,
    seed: int = 11,
    reps: int = 1000,
    exploratory: bool = False,
) -> dict[str, Any]:
    """Compute RBIR, EBR, and stratified WC(Delta t) across near/middle/far distance buckets."""
    case_map = {c.case_id: c for c in cases}
    primary = [r for r in rows if int(r.get("ingest_pass", 1)) == 1]
    by_decider: dict[str, list[dict]] = defaultdict(list)
    for r in primary:
        by_decider[r["decider"]].append(r)

    deciders_out: dict[str, Any] = {}
    for did, drs in sorted(by_decider.items()):
        gated = sorted({
            r["case_id"]
            for r in drs
            if r["kind"] == "gate" and r["action"] == r["a_star"] and r["case_id"] in case_map
        })
        if not gated:
            deciders_out[did] = {"n_gated": 0, "cells": {}}
            continue
        gated_set = set(gated)
        boot = Boot(len(gated), reps=reps, seed=seed)

        override_ids = {cid for cid in gated if case_map[cid].mechanism in OVERRIDE_MECHANISMS}
        expiry_ids = {cid for cid in gated if case_map[cid].mechanism in EXPIRY_MECHANISMS}
        by_dist: dict[str, set[str]] = {b: set() for b in DISTANCE_BUCKETS}
        for cid in gated:
            d = _distance_of(case_map[cid])
            if d in by_dist:
                by_dist[d].add(cid)

        cell_rows: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        cell_meta: dict[str, dict[str, Any]] = {}
        for r in drs:
            if r["kind"] != "view" or r["case_id"] not in gated_set:
                continue
            cname = cell_name(r["system"], r["config_label"], r["budget"])
            cell_rows[cname][r["case_id"]].append(r)
            cell_meta[cname] = {
                "system": r["system"],
                "config_label": r["config_label"],
                "budget": r["budget"],
            }

        cells_out: dict[str, Any] = {}
        for cname in sorted(cell_rows):
            cells_out[cname] = {
                **cell_meta[cname],
                "rbir": _rate_over_subset(gated, override_ids, cell_rows[cname], boot),
                "ebr": _rate_over_subset(gated, expiry_ids, cell_rows[cname], boot),
                "wc_by_distance": {
                    b: _rate_over_subset(gated, by_dist[b], cell_rows[cname], boot)
                    for b in DISTANCE_BUCKETS
                },
            }

        deciders_out[did] = {"n_gated": len(gated), "cells": cells_out}

    return {
        "schema": TEMPORAL_SCHEMA,
        "exploratory": bool(exploratory),
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95},
        "deciders": deciders_out,
    }


__all__ = [
    "TEMPORAL_SCHEMA",
    "OVERRIDE_MECHANISMS",
    "EXPIRY_MECHANISMS",
    "DISTANCE_BUCKETS",
    "compute_temporal",
]
