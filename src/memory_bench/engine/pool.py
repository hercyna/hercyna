"""Pool the table's runs into one row set and apply the frozen separation rule (CHARTER 4.6, Day 13).

The table is several runs per decider, one per product environment (the product extras conflict, so a run holds
the products of one environment). Each run repeats the same gate and floor calls through the shared call cache.
Pooling is allowed only when the runs agree on everything the rule reads:

- every run is complete and table-grade, with the same `cases.sha256` and the same `protocol_sha256`;
- every row key appears once after pooling, or appears in several runs with identical rows (the cached gate,
  floor and shared baseline rows). A key whose rows differ means the runs did not share their calls: refused.

The separation is then `scoring.separation` on the pooled rows, products being every view system that is not a
baseline. Nothing here computes a rate.

Shared views (Day 14, review major 1): a product's views are ingested once and reused across deciders on purpose,
so every (case, system, config_label, budget, repeat) of a product should carry one `view_sha256` for every
decider. The pool facts record whether it does (`shared_views`, `shared_view_cells`, `differing_view_cells`).
Views that differ are legal (separate ingests), so that is a fact, not a refusal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..scoring import separation
from ..scoring.metrics import ROW_DEFAULTS_V2, _row_key
from . import files as F

BASELINES = (
    "full",
    "bm25",
    "recent",
    "empty",
    "dense_raw",
    "hybrid_bm25_dense",
    "always_act",
    "always_request_info",
)


class PoolError(RuntimeError):
    """The runs cannot be pooled into one table."""


def pool(run_dirs: Sequence[str | Path], *, require_table: bool = True) -> tuple[list[dict], dict[str, Any]]:
    """(pooled rows, facts about the pool). Raises PoolError naming the first disagreement."""
    if not run_dirs:
        raise PoolError("no runs given")
    seen: dict[tuple, dict] = {}
    facts: dict[str, Any] = {"runs": [], "cases_sha256": None, "protocol_sha256": None, "grade": None}
    for d in map(Path, run_dirs):
        man = F.read_json(d / F.MANIFEST)
        if man.get("state") != "complete":
            raise PoolError(f"{d.name}: state {man.get('state')!r}, not complete")
        grade = man.get("grade") or "exploratory"
        if require_table and grade != "table":
            raise PoolError(f"{d.name}: grade {grade!r}, not table")
        cases = (man.get("cases") or {}).get("sha256")
        proto = (man.get("protocol") or {}).get("protocol_sha256")
        for key, val in (("cases_sha256", cases), ("protocol_sha256", proto), ("grade", grade)):
            if facts[key] is None:
                facts[key] = val
            elif facts[key] != val:
                raise PoolError(f"{d.name}: {key} {val!r} differs from {facts[key]!r}")
        n_new = 0
        run_partition: tuple[str, str, str] | None = None
        for r in F.read_metrics(d / F.METRICS):
            part = (
                str(r.get("track", ROW_DEFAULTS_V2["track"])),
                str(r.get("mode", ROW_DEFAULTS_V2["mode"])),
                str(r.get("eval_mode", ROW_DEFAULTS_V2["eval_mode"])),
            )
            if run_partition is None:
                run_partition = part
            elif part != run_partition:
                raise PoolError(f"{d.name}: mixed partitions {run_partition!r} and {part!r} inside one run")
            k = _row_key(r)
            if k in seen:
                if seen[k] != r:
                    raise PoolError(f"{d.name}: row {k} differs from the same row in another run; "
                                    f"the runs did not share their calls")
            else:
                seen[k] = r
                n_new += 1
        if run_partition is not None:
            if "partition" not in facts:
                facts["partition"] = run_partition
            elif facts["partition"] != run_partition:
                raise PoolError(
                    f"{d.name}: partition {run_partition!r} differs from {facts['partition']!r}; "
                    f"runs across different tracks, modes, or eval_modes cannot be pooled"
                )
        facts["runs"].append({"run": d.name, "new_rows": n_new})
    rows = list(seen.values())
    facts["deciders"] = sorted({r["decider"] for r in rows})
    facts["products"] = sorted({(r["system"], r["config_label"], r["budget"]) for r in rows
                                if r["kind"] == "view" and r["system"] not in BASELINES})
    facts.update(shared_views(rows))
    return rows, facts


def shared_views(rows: Sequence[dict]) -> dict[str, Any]:
    """Whether each product view cell (case, system, config_label, budget, repeat) has one view_sha256 across
    deciders. Cells seen by one decider only are not counted; with none to compare `shared_views` is None."""
    by: dict[tuple, dict[str, str]] = {}
    for r in rows:
        if r["kind"] != "view" or r["system"] in BASELINES:
            continue
        k = (r["case_id"], r["system"], r["config_label"], r["budget"], r["repeat"])
        by.setdefault(k, {})[r["decider"]] = r.get("view_sha256")
    checked = [v for v in by.values() if len(v) > 1]
    differ = sum(1 for v in checked if len(set(v.values())) > 1)
    return {"shared_views": (differ == 0) if checked else None, "shared_view_cells": len(checked), "differing_view_cells": differ}


def verdict(run_dirs: Sequence[str | Path], *, seed: int = 11, reps: int = 1000, alpha: float = 0.05,
            require_table: bool = True) -> dict[str, Any]:
    rows, facts = pool(run_dirs, require_table=require_table)
    res = separation(rows, facts["products"], seed=seed, reps=reps, alpha=alpha)
    facts["products"] = [list(p) for p in facts["products"]]
    return {"pool": facts, "separation": res}


__all__ = ["pool", "verdict", "shared_views", "PoolError", "BASELINES"]
