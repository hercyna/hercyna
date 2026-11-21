"""Power note (docs/freeze.md section 6, S9.4). Normal approximation, no bootstrap.

Per decider, over gated cases (gate action == a_star):
- the per-case headline of a system is the mean over repeats of wrong-commit on its view rows minus the
  same mean on the floor rows (committed = not REQUEST_INFO and not PARSE_ERROR; wrong-commit = committed
  and != a_star);
- SE of a system's headline = population standard deviation (divisor n) of the per-case values / sqrt(n);
- SE of a pair's paired difference = the same on the per-case differences (cases both systems have); a pair
  is keyed `a:b` and holds a minus b;
- minimum detectable difference = (z(1 - a/2) + z(power)) * SE, at a = alpha and at the Holm worst case
  a = alpha / K.
Values are rates (0..1), rounded to six decimals.
"""

from __future__ import annotations

import math
from statistics import NormalDist, fmean, pstdev
from typing import Any, Iterable

from .gates import is_committed
from .metrics import case_frame
from .stability import repeat_groups

POWER_SCHEMA = "memory_bench.power/1"
FULL = "full"


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def mde(se: float | None, alpha: float, power: float) -> float | None:
    """(z(1 - alpha/2) + z(power)) * se."""
    if se is None:
        return None
    z = NormalDist()
    return (z.inv_cdf(1 - alpha / 2) + z.inv_cdf(power)) * se


def _wc_means(rows: list[dict], gated: set[str]) -> dict[str, float]:
    """Per gated case: mean over the rows (repeats) of wrong-commit."""
    acc: dict[str, list[int]] = {}
    for r in rows:
        if r["case_id"] in gated:
            a = r["action"]
            acc.setdefault(r["case_id"], []).append(int(is_committed(a) and a != r["a_star"]))
    return {c: sum(v) / len(v) for c, v in acc.items()}


def _se(values: list[float]) -> float | None:
    return pstdev(values) / math.sqrt(len(values)) if len(values) >= 2 else None


def _entry(values: list[float], alpha: float, holm_alpha: float, power: float) -> dict[str, Any]:
    se = _se(values)
    return {"n": len(values), "mean": _r(fmean(values)) if values else None, "se": _r(se),
            "mde": _r(mde(se, alpha, power)), "mde_holm": _r(mde(se, holm_alpha, power))}


def power_note(rows: Iterable[dict], *, pairs: int = 3, power: float = 0.8, alpha: float = 0.05) -> dict[str, Any]:
    """The power.json content for a list of metric rows. Pure and deterministic."""
    if pairs < 1:
        raise ValueError(f"pairs must be at least 1, got {pairs}")
    if not 0 < power < 1 or not 0 < alpha < 1:
        raise ValueError("power and alpha must lie strictly between 0 and 1")
    rows = list(rows)
    holm_alpha = alpha / pairs
    by_decider: dict[str, list[dict]] = {}
    for r in rows:
        by_decider.setdefault(r["decider"], []).append(r)
    deciders: dict[str, Any] = {}
    for did, rs in sorted(by_decider.items()):
        order, _idx, _a_star, _mech, _gate, gated = case_frame(rs)
        groups = repeat_groups(rs)
        floor = _wc_means(groups[FULL]["rows"], gated) if FULL in groups else None
        views = {k: _wc_means(g["rows"], gated) for k, g in groups.items() if g["system"] != FULL}
        systems: dict[str, Any] = {}
        for k, wc in views.items():
            if floor is None:
                systems[k] = None
                continue
            cases = [c for c in order if c in wc and c in floor]
            systems[k] = _entry([wc[c] - floor[c] for c in cases], alpha, holm_alpha, power)
        pair_out: dict[str, Any] = {}
        keys = list(views)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                cases = [c for c in order if c in views[a] and c in views[b]]
                pair_out[f"{a}:{b}"] = {"a": a, "b": b, "difference": "a_minus_b",
                                          **_entry([views[a][c] - views[b][c] for c in cases],
                                                   alpha, holm_alpha, power)}
        deciders[did] = {"n_gated": len(gated), "systems": systems, "pairs": pair_out}
    # Tokens only, so the file passes the text-free scanner; the definitions are in the module docstring.
    return {"schema": POWER_SCHEMA, "method": "normal_approximation", "sd": "population", "cases": "gated",
            "alpha": alpha, "power": power, "pairs_k": pairs, "alpha_holm": _r(holm_alpha),
            "deciders": deciders}


__all__ = ["power_note", "mde", "POWER_SCHEMA"]
