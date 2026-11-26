"""Native paired-bootstrap Chow risk L(lambda) and Risk-Coverage curves (memory-bench v2).

Evaluates L(lambda) = lambda * WC + AB and Delta L(lambda) = L_{s,b}(lambda) - L_floor(lambda)
over the registered protocol/v2.json lambda grid on the shipped paired Boot stream.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from .bootstrap import Boot
from .gates import is_committed
from .metrics import cell_name

CHOW_SCHEMA = "memory_bench.chow/1"
DEFAULT_CHOW_LAMBDAS: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0)
DISPLAY_CHOW_LAMBDAS: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0)
_EPS = 1e-12


def _case_wc_ab(rows: Sequence[dict]) -> tuple[float, float, float]:
    """Per-case mean (wc, ab, n_rows) for one cell across repeats."""
    n = len(rows)
    if not n:
        return 0.0, 0.0, 0.0
    wc = sum(1.0 for r in rows if is_committed(r["action"]) and r["action"] != r["a_star"])
    ab = sum(1.0 for r in rows if r["action"] == "REQUEST_INFO" and r["a_star"] != "REQUEST_INFO")
    return wc, ab, float(n)


def _chow_metric(lam: float):
    def f(s: dict[str, float]) -> float | None:
        if s["n"] <= 0:
            return None
        return (lam * s["wc"] + s["ab"]) / s["n"]

    return f


def _coverage_metric(s: dict[str, float]) -> float | None:
    if s["n"] <= 0:
        return None
    return 1.0 - (s["ab"] / s["n"])


def _selective_risk_metric(s: dict[str, float]) -> float | None:
    if s["n"] <= 0:
        return None
    cov = 1.0 - (s["ab"] / s["n"])
    if cov <= _EPS:
        return None
    return (s["wc"] / s["n"]) / cov


def _cell_features(cases: list[str], by_case: dict[str, list[dict]]) -> dict[str, list[float]]:
    wc_col: list[float] = []
    ab_col: list[float] = []
    n_col: list[float] = []
    for cid in cases:
        wc, ab, n = _case_wc_ab(by_case.get(cid, ()))
        wc_col.append(wc)
        ab_col.append(ab)
        n_col.append(n)
    return {"wc": wc_col, "ab": ab_col, "n": n_col}


def _decider_chow(
    rows: list[dict],
    lambdas: Sequence[float],
    *,
    seed: int,
    reps: int,
) -> dict[str, Any]:
    gated_cases = sorted({
        r["case_id"]
        for r in rows
        if r["kind"] == "gate" and r["action"] == r["a_star"]
    })
    if not gated_cases:
        return {"n_gated": 0, "lambdas": list(lambdas), "floor": None, "cells": {}}

    boot = Boot(len(gated_cases), reps=reps, seed=seed)
    floor_by_case: dict[str, list[dict]] = defaultdict(list)
    cells_by_key: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    cell_meta: dict[str, dict[str, Any]] = {}

    gated_set = set(gated_cases)
    for r in rows:
        if r["case_id"] not in gated_set:
            continue
        if r["kind"] == "floor":
            floor_by_case[r["case_id"]].append(r)
        elif r["kind"] == "view":
            cname = cell_name(r["system"], r["config_label"], r["budget"])
            cells_by_key[cname][r["case_id"]].append(r)
            cell_meta[cname] = {
                "system": r["system"],
                "config_label": r["config_label"],
                "budget": r["budget"],
            }

    floor_feats = _cell_features(gated_cases, floor_by_case)
    floor_cache = boot.resampled_sums(floor_feats)

    floor_out = {
        "risk_coverage": {
            "coverage": boot.stat(floor_feats, _coverage_metric, cache=floor_cache),
            "selective_risk": boot.stat(floor_feats, _selective_risk_metric, cache=floor_cache),
        },
        "by_lambda": {
            str(lam): boot.stat(floor_feats, _chow_metric(float(lam)), cache=floor_cache)
            for lam in lambdas
        },
    }

    cells_out: dict[str, Any] = {}
    for cname in sorted(cells_by_key):
        c_feats = _cell_features(gated_cases, cells_by_key[cname])
        c_cache = boot.resampled_sums(c_feats)
        by_lam: dict[str, Any] = {}
        for lam in lambdas:
            fn = _chow_metric(float(lam))
            by_lam[str(lam)] = {
                "risk": boot.stat(c_feats, fn, cache=c_cache),
                "delta_above_floor": boot.paired_diff(
                    c_feats, fn, floor_feats, fn, cache_a=c_cache, cache_b=floor_cache
                ),
            }
        cells_out[cname] = {
            **cell_meta[cname],
            "risk_coverage": {
                "coverage": boot.stat(c_feats, _coverage_metric, cache=c_cache),
                "selective_risk": boot.stat(c_feats, _selective_risk_metric, cache=c_cache),
            },
            "by_lambda": by_lam,
        }

    return {
        "n_gated": len(gated_cases),
        "lambdas": [float(x) for x in lambdas],
        "floor": floor_out,
        "cells": cells_out,
    }


def chow_summary(
    rows: Iterable[dict],
    *,
    lambdas: Sequence[float] = DEFAULT_CHOW_LAMBDAS,
    seed: int = 11,
    reps: int = 1000,
    exploratory: bool = False,
) -> dict[str, Any]:
    """Compute paired-bootstrap Chow L(lambda) and Risk-Coverage curves per decider.

    Strictly isolates `ingest_pass == 1` rows so dual-ingest passes never leak into headline risk.
    """
    primary = [r for r in rows if int(r.get("ingest_pass", 1)) == 1]
    by_decider: dict[str, list[dict]] = defaultdict(list)
    for r in primary:
        by_decider[r["decider"]].append(r)
    return {
        "schema": CHOW_SCHEMA,
        "exploratory": bool(exploratory),
        "lambdas": [float(x) for x in lambdas],
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95},
        "deciders": {
            did: _decider_chow(drs, lambdas, seed=seed, reps=reps)
            for did, drs in sorted(by_decider.items())
        },
    }


__all__ = ["CHOW_SCHEMA", "DEFAULT_CHOW_LAMBDAS", "DISPLAY_CHOW_LAMBDAS", "chow_summary"]
