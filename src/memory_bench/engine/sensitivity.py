"""`bench sensitivity`: the separation rule over several bootstrap seeds (Day 14, review major 4). EXPLORATORY.

The frozen verdict is the one at the protocol's seed (11) and resample count (1000). This reruns the same rule on
the same pooled rows for other seeds, at a given resample count, to show how much the Holm p-values and the
verdict move with the resampling alone. It changes nothing frozen and its output is labelled exploratory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..scoring import separation
from .pool import pool

SCHEMA = "memory_bench.sensitivity/1"


def sensitivity(run_dirs: Sequence[str | Path], seeds: Sequence[int], *, reps: int = 10000, alpha: float = 0.05,
                require_table: bool = True) -> dict[str, Any]:
    rows, facts = pool(run_dirs, require_table=require_table)
    products = facts["products"]
    by_seed: dict[str, Any] = {}
    for seed in seeds:
        res = separation(rows, products, seed=seed, reps=reps, alpha=alpha)
        by_seed[str(seed)] = {
            "verdict": res["verdict"], "leaderboard": res["leaderboard"],
            "separating_pairs": res["separating_pairs"],
            "pairs": {did: {k: {"value": p["value"], "ci95": p["ci95"], "p": p["p"], "holm_p": p["holm_p"],
                                "separates": p["separates"], "direction": p["direction"],
                                "n_resamples": p["n_resamples"]}
                            for k, p in dd["pairs"].items()}
                      for did, dd in res["deciders"].items()},
        }
    facts = {**facts, "products": [list(p) for p in products]}
    outcomes = {(v["verdict"], tuple(sorted((x["pair"], x["direction"]) for x in v["separating_pairs"])))
                for v in by_seed.values()}
    return {"schema": SCHEMA, "grade": "exploratory",
            "note": "exploratory seed sensitivity; the frozen verdict is seed 11 with 1000 resamples",
            "reps": reps, "alpha": alpha, "seeds": [int(s) for s in seeds], "pool": facts, "by_seed": by_seed,
            "verdict_stable": len(outcomes) == 1}


def table(res: dict[str, Any]) -> list[str]:
    """Compact text table: per pair and decider, the Holm p for each seed (* separates), then the verdicts."""
    seeds = [str(s) for s in res["seeds"]]
    out = [f"EXPLORATORY seed sensitivity, {res['reps']} resamples, alpha {res['alpha']} "
           f"(frozen verdict: seed 11, 1000 resamples)"]
    first = res["by_seed"][seeds[0]]["pairs"] if seeds else {}
    width = max([len(k) for dd in first.values() for k in dd] + [4])
    out.append(f"{'pair':{width}s}  {'decider':12s} " + " ".join(f"{'s' + s:>9s}" for s in seeds))
    for did in sorted(first):
        for k in sorted(first[did]):
            cells = []
            for s in seeds:
                p = res["by_seed"][s]["pairs"][did][k]
                hp = p["holm_p"]
                cells.append(f"{'-' if hp is None else f'{hp:.4f}'}{'*' if p['separates'] else ' '}".rjust(9))
            out.append(f"{k:{width}s}  {did:12s} " + " ".join(cells))
    out.append(f"{'verdict':{width}s}  {'':12s} " + " ".join(f"{res['by_seed'][s]['verdict'][:9]:>9s}" for s in seeds))
    for s in seeds:
        sp = res["by_seed"][s]["separating_pairs"]
        out.append(f"seed {s}: {res['by_seed'][s]['verdict']}; separating: "
                   + ("; ".join(f"{x['pair']} ({x['direction']})" for x in sp) or "none"))
    out.append(f"verdict stable across seeds: {'yes' if res['verdict_stable'] else 'no'}")
    return out


__all__ = ["sensitivity", "table", "SCHEMA"]
