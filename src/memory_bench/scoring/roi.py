"""End-to-End Memory ROI, Tool-Call Tax, and Local GPU-Second Efficiency (memory-bench v2).

Quantifies substitution cost in End-to-End Harness Mode against both `e2e_floor` and `empty + tools`.
Per spec §4.2 (A9): when delta_cost <= 0 and delta_L_reduction > 0, records per-system status
`"roi_status": "dominant"` without dividing by epsilon and without imposing an order.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .bootstrap import Boot
from .gates import is_committed
from .metrics import cell_name

ROI_SCHEMA = "memory_bench.roi/1"
_EPS = 1e-12


def _mean_col(key: str):
    return lambda s: s[key] / s["n"] if s["n"] > 0 else None


def _chow_loss(lam: float):
    return lambda s: (lam * s["wc"] + s["ab"]) / s["n"] if s["n"] > 0 else None


def _cell_roi_features(
    cases: list[str],
    by_case: Mapping[str, list[dict]],
    mem_cost_per_case: float = 0.0,
) -> dict[str, list[float]]:
    feats: dict[str, list[float]] = {
        "wc": [],
        "ab": [],
        "tool_calls": [],
        "wall_seconds": [],
        "cost_usd": [],
        "gpu_seconds": [],
        "n": [],
    }
    for cid in cases:
        rs = by_case.get(cid, ())
        n = float(len(rs))
        wc = sum(1.0 for r in rs if is_committed(r["action"]) and r["action"] != r["a_star"])
        ab = sum(1.0 for r in rs if r["action"] == "REQUEST_INFO" and r["a_star"] != "REQUEST_INFO")
        tc = sum(float(r.get("tool_calls") or 0.0) for r in rs)
        ws = sum(float(r.get("wall_seconds") or 0.0) for r in rs)
        h_cost = sum(float(r.get("cost_usd") or 0.0) for r in rs)
        gpu = sum(float(r.get("gpu_seconds") or 0.0) for r in rs)
        feats["wc"].append(wc)
        feats["ab"].append(ab)
        feats["tool_calls"].append(tc)
        feats["wall_seconds"].append(ws)
        feats["cost_usd"].append(h_cost + mem_cost_per_case * n)
        feats["gpu_seconds"].append(gpu)
        feats["n"].append(n)
    return feats


def compute_roi(
    rows: Iterable[dict],
    *,
    memory_cost_per_case: Mapping[str, float] | None = None,
    lam: float = 5.0,
    seed: int = 11,
    reps: int = 1000,
    exploratory: bool = False,
) -> dict[str, Any]:
    """Compute paired-bootstrap Memory ROI and Tool-Call Tax per decider/harness.

    Compares each view cell against `floor` (e2e_floor) for Tool-Call Tax / WallSec delta,
    and against `empty` (cold-start `empty + tools`) for net cost delta and Chow risk reduction.
    """
    mem_costs = dict(memory_cost_per_case or {})
    primary = [r for r in rows if int(r.get("ingest_pass", 1)) == 1]
    by_decider: dict[str, list[dict]] = defaultdict(list)
    for r in primary:
        by_decider[r["decider"]].append(r)

    deciders_out: dict[str, Any] = {}
    for did, drs in sorted(by_decider.items()):
        gated = sorted({
            r["case_id"]
            for r in drs
            if r["kind"] == "gate" and r["action"] == r["a_star"]
        })
        if not gated:
            deciders_out[did] = {"n_gated": 0, "cells": {}}
            continue
        gated_set = set(gated)
        boot = Boot(len(gated), reps=reps, seed=seed)

        floor_cases: dict[str, list[dict]] = defaultdict(list)
        empty_cases: dict[str, list[dict]] = defaultdict(list)
        cell_cases: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        cell_meta: dict[str, dict[str, Any]] = {}

        for r in drs:
            if r["case_id"] not in gated_set:
                continue
            if r["kind"] == "floor":
                floor_cases[r["case_id"]].append(r)
            elif r["kind"] == "view":
                cname = cell_name(r["system"], r["config_label"], r["budget"])
                cell_cases[cname][r["case_id"]].append(r)
                cell_meta[cname] = {
                    "system": r["system"],
                    "config_label": r["config_label"],
                    "budget": r["budget"],
                }
                if r["system"] == "empty" and not empty_cases.get(r["case_id"]):
                    empty_cases[r["case_id"]].append(r)

        floor_feats = _cell_roi_features(gated, floor_cases, 0.0)
        floor_cache = boot.resampled_sums(floor_feats)

        has_empty = any(empty_cases.values())
        empty_feats = _cell_roi_features(gated, empty_cases, 0.0) if has_empty else None
        empty_cache = boot.resampled_sums(empty_feats) if empty_feats is not None else None

        loss_fn = _chow_loss(lam)
        cells_out: dict[str, Any] = {}

        for cname in sorted(cell_cases):
            meta = cell_meta[cname]
            mcost = float(mem_costs.get(cname, mem_costs.get(meta["system"], 0.0)))
            c_feats = _cell_roi_features(gated, cell_cases[cname], mcost)
            c_cache = boot.resampled_sums(c_feats)

            delta_tc = boot.paired_diff(
                c_feats, _mean_col("tool_calls"), floor_feats, _mean_col("tool_calls"),
                cache_a=c_cache, cache_b=floor_cache,
            )
            delta_wall = boot.paired_diff(
                c_feats, _mean_col("wall_seconds"), floor_feats, _mean_col("wall_seconds"),
                cache_a=c_cache, cache_b=floor_cache,
            )

            entry: dict[str, Any] = {
                **meta,
                "lambda": float(lam),
                "mean_tool_calls": boot.stat(c_feats, _mean_col("tool_calls"), cache=c_cache),
                "mean_wall_seconds": boot.stat(c_feats, _mean_col("wall_seconds"), cache=c_cache),
                "mean_cost_usd": boot.stat(c_feats, _mean_col("cost_usd"), cache=c_cache),
                "mean_gpu_seconds": boot.stat(c_feats, _mean_col("gpu_seconds"), cache=c_cache),
                "delta_tool_calls_vs_floor": delta_tc,
                "delta_wall_seconds_vs_floor": delta_wall,
                "roi_status": "no_empty_reference",
                "roi_mem_pp_per_cent": None,
                "roi_gpu_sec_pp_per_100s": None,
                "net_savings_usd_per_case": None,
                "risk_reduction_pts": None,
            }

            if empty_feats is not None and empty_cache is not None:
                # Risk reduction in percentage points: 100 * (L_empty - L_cell)
                dl_diff = boot.paired_diff(
                    empty_feats, loss_fn, c_feats, loss_fn,
                    cache_a=empty_cache, cache_b=c_cache,
                )
                dc_diff = boot.paired_diff(
                    c_feats, _mean_col("cost_usd"), empty_feats, _mean_col("cost_usd"),
                    cache_a=c_cache, cache_b=empty_cache,
                )
                entry["delta_cost_vs_empty"] = dc_diff
                risk_red_pts = None if dl_diff["value"] is None else round(100.0 * dl_diff["value"], 6)
                entry["risk_reduction_pts"] = risk_red_pts
                dc = dc_diff["value"]

                if dc is not None and risk_red_pts is not None:
                    if dc <= 0.0 and risk_red_pts > 0.0:
                        # Per-system status only — never an order (A9)
                        entry["roi_status"] = "dominant"
                        entry["net_savings_usd_per_case"] = round(-dc, 6)
                    elif dc > _EPS:
                        entry["roi_status"] = "measured"
                        entry["roi_mem_pp_per_cent"] = round(risk_red_pts / (dc / 0.01), 6)
                    else:
                        entry["roi_status"] = "non_improving"

                mean_gpu = entry["mean_gpu_seconds"]["value"]
                if mean_gpu is not None and mean_gpu > _EPS and risk_red_pts is not None:
                    entry["roi_gpu_sec_pp_per_100s"] = round(risk_red_pts / (mean_gpu / 100.0), 6)

            cells_out[cname] = entry

        deciders_out[did] = {"n_gated": len(gated), "cells": cells_out}

    return {
        "schema": ROI_SCHEMA,
        "exploratory": bool(exploratory),
        "lambda": float(lam),
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95},
        "deciders": deciders_out,
    }


__all__ = ["ROI_SCHEMA", "compute_roi"]
