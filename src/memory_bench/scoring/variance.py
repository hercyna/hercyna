"""Descriptive balanced two-way nested MoM ANOVA for Dual-Ingestion Variance (memory-bench v2).

Per spec §4.4 (A9):
- The nested ANOVA is strictly descriptive and carries no headline.
- Over I cases, J ingest passes nested within each case (J >= 2), and K repeats per pass (K >= 2):
    sigma2_repeat = MS_within
    sigma2_ingest = max(0, (MS_pass_in_case - MS_within) / K)
    sigma2_case   = max(0, (MS_case - MS_pass_in_case) / (J * K))
    IFS = sigma2_ingest / (sigma2_ingest + sigma2_repeat) if denom > 0 else None
- Paired with View Exact-Match Rate and Key-Fact Jaccard Overlap across ingest passes.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .gates import is_committed
from .metrics import cell_name

VARIANCE_SCHEMA = "memory_bench.variance/1"
_EPS = 1e-12


def balanced_nested_anova(data: Mapping[str, Mapping[int, Sequence[float]]]) -> dict[str, Any]:
    """Compute balanced two-way nested random-effects MoM ANOVA over binary/numeric Y_{ijk}.

    `data` maps `case_id -> {ingest_pass: [y_1, ..., y_K]}`.
    Only cases with identical J >= 2 passes and K >= 2 repeats are included in the balanced block.
    """
    eligible = {
        cid: {p: list(ys) for p, ys in passes.items() if ys}
        for cid, passes in data.items()
    }
    eligible = {cid: p for cid, p in eligible.items() if len(p) >= 2}
    if len(eligible) < 2:
        return {
            "balanced": False,
            "n_cases": len(eligible),
            "n_passes": 0,
            "n_repeats": 0,
            "sigma2_repeat": None,
            "sigma2_ingest": None,
            "sigma2_case": None,
            "ifs": None,
        }

    # Determine common balanced J and K
    j_min = min(len(p) for p in eligible.values())
    k_min = min(len(ys) for p in eligible.values() for ys in p.values())
    if j_min < 2 or k_min < 2:
        return {
            "balanced": False,
            "n_cases": len(eligible),
            "n_passes": j_min,
            "n_repeats": k_min,
            "sigma2_repeat": None,
            "sigma2_ingest": None,
            "sigma2_case": None,
            "ifs": None,
        }

    cases_sorted = sorted(eligible)
    i_cnt = len(cases_sorted)
    j_cnt = j_min
    k_cnt = k_min

    grid: list[list[list[float]]] = []
    all_vals: list[float] = []
    for cid in cases_sorted:
        pass_keys = sorted(eligible[cid])[:j_cnt]
        c_rows = [list(eligible[cid][pk][:k_cnt]) for pk in pass_keys]
        grid.append(c_rows)
        for ys in c_rows:
            all_vals.extend(ys)

    grand_mean = sum(all_vals) / len(all_vals)
    ss_case = 0.0
    ss_pass = 0.0
    ss_within = 0.0

    for c_rows in grid:
        c_vals = [y for ys in c_rows for y in ys]
        c_mean = sum(c_vals) / len(c_vals)
        ss_case += (j_cnt * k_cnt) * ((c_mean - grand_mean) ** 2)
        for ys in c_rows:
            p_mean = sum(ys) / len(ys)
            ss_pass += k_cnt * ((p_mean - c_mean) ** 2)
            for y in ys:
                ss_within += (y - p_mean) ** 2

    df_case = i_cnt - 1
    df_pass = i_cnt * (j_cnt - 1)
    df_within = i_cnt * j_cnt * (k_cnt - 1)

    ms_case = ss_case / df_case
    ms_pass = ss_pass / df_pass
    ms_within = ss_within / df_within

    sigma2_repeat = max(0.0, ms_within)
    sigma2_ingest = max(0.0, (ms_pass - ms_within) / k_cnt)
    sigma2_case = max(0.0, (ms_case - ms_pass) / (j_cnt * k_cnt))

    denom = sigma2_ingest + sigma2_repeat
    ifs = (sigma2_ingest / denom) if denom > _EPS else None

    return {
        "balanced": True,
        "n_cases": i_cnt,
        "n_passes": j_cnt,
        "n_repeats": k_cnt,
        "ms_case": round(ms_case, 8),
        "ms_pass_in_case": round(ms_pass, 8),
        "ms_within": round(ms_within, 8),
        "sigma2_repeat": round(sigma2_repeat, 8),
        "sigma2_ingest": round(sigma2_ingest, 8),
        "sigma2_case": round(sigma2_case, 8),
        "ifs": None if ifs is None else round(ifs, 6),
    }


def view_drift_metrics(view_records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute View Exact-Match Rate and Key-Fact Jaccard Overlap between pass 1 and pass 2."""
    by_sys_case: dict[tuple[str, str], dict[str, dict[int, Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for v in view_records:
        cid = str(v.get("case_id") or "")
        sys_name = str(v.get("system") or "")
        cfg = str(v.get("config_label") or "default")
        ipass = int(v.get("ingest_pass") or 1)
        if cid and sys_name:
            by_sys_case[(sys_name, cfg)][cid][ipass] = v

    out: dict[str, Any] = {}
    for (sys_name, cfg), cases in sorted(by_sys_case.items()):
        paired = [(cid, p[1], p[2]) for cid, p in sorted(cases.items()) if 1 in p and 2 in p]
        if not paired:
            continue
        exact_matches = 0
        jaccards: list[float] = []
        for _cid, v1, v2 in paired:
            h1, h2 = v1.get("view_sha256"), v2.get("view_sha256")
            if h1 is not None and h2 is not None:
                same = h1 == h2
            else:
                same = v1.get("records") == v2.get("records") and v1.get("text") == v2.get("text")
            if same:
                exact_matches += 1
            s1 = set(str(x) for x in (v1.get("record_ids") or []))
            s2 = set(str(x) for x in (v2.get("record_ids") or []))
            if not s1 and not s2:
                jaccards.append(1.0 if same else 0.0)
            else:
                jaccards.append(len(s1 & s2) / len(s1 | s2))
        key = f"{sys_name}/{cfg}"
        out[key] = {
            "n_paired_cases": len(paired),
            "view_exact_match_rate": round(exact_matches / len(paired), 6),
            "key_fact_jaccard_mean": round(sum(jaccards) / len(jaccards), 6),
        }
    return out


def compute_variance(
    rows: Iterable[dict],
    view_records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compute descriptive dual-ingest nested ANOVA over `ingest_pass in {1, 2}` view rows."""
    rows = list(rows)
    by_decider: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_decider[r["decider"]].append(r)

    deciders_out: dict[str, Any] = {}
    for did, drs in sorted(by_decider.items()):
        gated = {
            r["case_id"]
            for r in drs
            if r["kind"] == "gate" and int(r.get("ingest_pass", 1)) == 1 and r["action"] == r["a_star"]
        }
        cells: dict[str, dict[str, dict[int, list[tuple[int, float]]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        for r in drs:
            if r["kind"] != "view" or r["case_id"] not in gated:
                continue
            cname = cell_name(r["system"], r["config_label"], r["budget"])
            ipass = int(r.get("ingest_pass", 1))
            rep = int(r.get("repeat", 0))
            wc = 1.0 if (is_committed(r["action"]) and r["action"] != r["a_star"]) else 0.0
            cells[cname][r["case_id"]][ipass].append((rep, wc))

        cells_out: dict[str, Any] = {}
        for cname in sorted(cells):
            ordered = {
                cid: {
                    ipass: [y for _rep, y in sorted(pairs)]
                    for ipass, pairs in pass_map.items()
                }
                for cid, pass_map in cells[cname].items()
            }
            cells_out[cname] = balanced_nested_anova(ordered)
        deciders_out[did] = {"cells": cells_out}

    return {
        "schema": VARIANCE_SCHEMA,
        "descriptive_only": True,
        "deciders": deciders_out,
        "view_drift": view_drift_metrics(view_records),
    }


__all__ = ["VARIANCE_SCHEMA", "balanced_nested_anova", "view_drift_metrics", "compute_variance"]
