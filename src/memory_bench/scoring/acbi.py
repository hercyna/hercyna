"""Asymmetric Conflict Blindness Index (ACBI) and Siren Effect Ratio (SER) (memory-bench v2).

Emits a standalone text-free aggregate artifact (`acbi.json`) over conflict cases
(U2, CU2, HU2; |d*| >= 2) partitioned into R_none, R_partial (0 < hits < |d*|), and R_both.
When |R_both| == 0, ACBI is strictly None and `acbi_vs_none` is reported alongside bucket counts.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..adapters.base import Retrieved, enforce_budget
from ..cases.schema import Case
from .bootstrap import Boot
from .gates import is_committed
from .metrics import cell_name

ACBI_SCHEMA = "memory_bench.acbi/1"
CONFLICT_MECHANISMS = frozenset({"U2", "CU2", "HU2"})
_EPS = 1e-6


def classify_conflict_bucket(
    d_star: Sequence[str],
    retrieved_ids: Iterable[str],
) -> str:
    """Classify a single view over a conflict case (|d*| >= 2) into none / partial / both."""
    target = set(d_star)
    if len(target) < 2:
        return "not_conflict"
    hits = len(target & set(retrieved_ids))
    if hits == 0:
        return "none"
    if hits >= len(target):
        return "both"
    return "partial"


def exact_id_matcher(case: Case, view_record: Mapping[str, Any]) -> set[str]:
    """Extract matched d_star event IDs from a stored view record using exact ID presence."""
    ids = view_record.get("record_ids")
    if isinstance(ids, (list, tuple, set)):
        return set(str(x) for x in ids) & set(case.d_star)
    recs = view_record.get("records") or []
    found: set[str] = set()
    for r in recs:
        if isinstance(r, dict) and r.get("id"):
            found.add(str(r["id"]))
        elif isinstance(r, str):
            for did in case.d_star:
                if did in r:
                    found.add(did)
    text = view_record.get("text") or ""
    if isinstance(text, str) and text:
        for did in case.d_star:
            if did in text:
                found.add(did)
    return found & set(case.d_star)


def _acbi_stats(
    conflict_cases: list[str],
    case_buckets: Mapping[str, str],
    rows_by_case: Mapping[str, list[dict]],
    *,
    boot: Boot,
) -> dict[str, Any]:
    feats: dict[str, list[float]] = {
        "n_case": [],
        "is_none": [],
        "is_partial": [],
        "is_both": [],
        "wc_none": [],
        "den_none": [],
        "wc_partial": [],
        "den_partial": [],
        "wc_both": [],
        "den_both": [],
    }
    for cid in conflict_cases:
        bucket = case_buckets.get(cid, "none")
        rs = rows_by_case.get(cid, ())
        wc = sum(1.0 for r in rs if is_committed(r["action"]) and r["action"] != r["a_star"])
        den = float(len(rs))
        feats["n_case"].append(1.0)
        feats["is_none"].append(1.0 if bucket == "none" else 0.0)
        feats["is_partial"].append(1.0 if bucket == "partial" else 0.0)
        feats["is_both"].append(1.0 if bucket == "both" else 0.0)
        feats["wc_none"].append(wc if bucket == "none" else 0.0)
        feats["den_none"].append(den if bucket == "none" else 0.0)
        feats["wc_partial"].append(wc if bucket == "partial" else 0.0)
        feats["den_partial"].append(den if bucket == "partial" else 0.0)
        feats["wc_both"].append(wc if bucket == "both" else 0.0)
        feats["den_both"].append(den if bucket == "both" else 0.0)

    cache = boot.resampled_sums(feats)

    def _rate(num: str, den: str):
        return lambda s: s[num] / s[den] if s[den] > 0 else None

    def _arr(s: dict[str, float]) -> float | None:
        return s["is_partial"] / s["n_case"] if s["n_case"] > 0 else None

    def _acbi(s: dict[str, float]) -> float | None:
        if s["n_case"] <= 0 or s["den_partial"] <= 0 or s["den_both"] <= 0:
            return None
        arr = s["is_partial"] / s["n_case"]
        p_part = s["wc_partial"] / s["den_partial"]
        p_both = s["wc_both"] / s["den_both"]
        return arr * (p_part - p_both)

    def _acbi_vs_none(s: dict[str, float]) -> float | None:
        if s["n_case"] <= 0 or s["den_partial"] <= 0 or s["den_none"] <= 0:
            return None
        arr = s["is_partial"] / s["n_case"]
        p_part = s["wc_partial"] / s["den_partial"]
        p_none = s["wc_none"] / s["den_none"]
        return arr * (p_part - p_none)

    def _ser(s: dict[str, float]) -> float | None:
        if s["den_partial"] <= 0 or s["den_none"] <= 0:
            return None
        p_part = s["wc_partial"] / s["den_partial"]
        p_none = s["wc_none"] / s["den_none"]
        return p_part / max(_EPS, p_none)

    full = Boot.full_sums(feats)
    return {
        "counts": {
            "n_conflict_cases": int(full["n_case"]),
            "n_none": int(full["is_none"]),
            "n_partial": int(full["is_partial"]),
            "n_both": int(full["is_both"]),
        },
        "arr": boot.stat(feats, _arr, cache=cache),
        "p_wc_given_none": boot.stat(feats, _rate("wc_none", "den_none"), cache=cache),
        "p_wc_given_partial": boot.stat(feats, _rate("wc_partial", "den_partial"), cache=cache),
        "p_wc_given_both": boot.stat(feats, _rate("wc_both", "den_both"), cache=cache),
        "acbi": boot.stat(feats, _acbi, cache=cache),
        "acbi_vs_none": boot.stat(feats, _acbi_vs_none, cache=cache),
        "ser": boot.stat(feats, _ser, cache=cache),
    }


def compute_acbi(
    cases: Sequence[Case],
    rows: Iterable[dict],
    view_records: Iterable[Mapping[str, Any]],
    *,
    matcher: Callable[[Case, Mapping[str, Any]], set[str]] = exact_id_matcher,
    seed: int = 11,
    reps: int = 1000,
    exploratory: bool = False,
    calibration_kappa: float | None = None,
) -> dict[str, Any]:
    """Build text-free `acbi.json` joining cases, primary metric rows, and stored views.

    Emits strictly counts, conditional rates, and bootstrap CIs — zero view or case text.
    """
    case_map = {
        c.case_id: c
        for c in cases
        if c.mechanism in CONFLICT_MECHANISMS and len(c.d_star) >= 2
    }

    # Index stored views by (case_id, system, config_label) for ingest_pass == 1
    views_index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for v in view_records:
        if int(v.get("ingest_pass") or 1) != 1:
            continue
        cid = str(v.get("case_id") or "")
        if cid in case_map:
            views_index[(cid, str(v.get("system")), str(v.get("config_label") or "default"))] = v

    primary = [r for r in rows if int(r.get("ingest_pass", 1)) == 1]
    by_decider: dict[str, list[dict]] = defaultdict(list)
    for r in primary:
        by_decider[r["decider"]].append(r)

    deciders_out: dict[str, Any] = {}
    for did, drs in sorted(by_decider.items()):
        gated_conflict = sorted({
            r["case_id"]
            for r in drs
            if r["kind"] == "gate" and r["action"] == r["a_star"] and r["case_id"] in case_map
        })
        if not gated_conflict:
            deciders_out[did] = {"n_conflict_cases": 0, "cells": {}}
            continue
        boot = Boot(len(gated_conflict), reps=reps, seed=seed)
        gated_set = set(gated_conflict)
        cell_rows: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        cell_meta: dict[str, tuple[str, str, int | None]] = {}
        for r in drs:
            if r["kind"] != "view" or r["case_id"] not in gated_set:
                continue
            cname = cell_name(r["system"], r["config_label"], r["budget"])
            cell_rows[cname][r["case_id"]].append(r)
            cell_meta[cname] = (r["system"], str(r["config_label"] or "default"), r["budget"])

        cells_out: dict[str, Any] = {}
        for cname in sorted(cell_rows):
            sys_name, cfg_label, budget = cell_meta[cname]
            buckets: dict[str, str] = {}
            for cid in gated_conflict:
                c = case_map[cid]
                vrec = views_index.get((cid, sys_name, cfg_label))
                if (
                    vrec is not None
                    and isinstance(budget, int)
                    and budget > 0
                    and isinstance(vrec.get("text"), str)
                    and isinstance(vrec.get("units"), list)
                    and vrec.get("units")
                ):
                    cut = enforce_budget(
                        Retrieved(
                            text=vrec["text"],
                            record_ids=list(vrec.get("record_ids") or []),
                            units=list(vrec.get("units") or []),
                        ),
                        budget,
                    )
                    vrec = {**vrec, "text": cut.text, "record_ids": cut.record_ids, "units": cut.units}
                matched = matcher(c, vrec) if vrec is not None else set()
                buckets[cid] = classify_conflict_bucket(c.d_star, matched)
            cells_out[cname] = {
                "system": sys_name,
                "config_label": cfg_label,
                "budget": budget,
                **_acbi_stats(gated_conflict, buckets, cell_rows[cname], boot=boot),
            }
        deciders_out[did] = {
            "n_conflict_cases": len(gated_conflict),
            "cells": cells_out,
        }

    return {
        "schema": ACBI_SCHEMA,
        "exploratory": bool(exploratory),
        "calibration_kappa": calibration_kappa,
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95},
        "deciders": deciders_out,
    }


__all__ = [
    "ACBI_SCHEMA",
    "CONFLICT_MECHANISMS",
    "classify_conflict_bucket",
    "exact_id_matcher",
    "compute_acbi",
]
