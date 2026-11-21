"""The separation rule (CHARTER section 4.6, the Day 13 rule) on metric rows.

Per decider, on the SAME bootstrap resamples as `summarise` (Boot(n_cases, reps, seed) over the case order
of `metrics.case_frame`, so one seed gives one resample list):
- from the floor: HEADLINE(s,d) = wrong_commit(s,d) - floor(d), its paired 95 percent interval and a
  two-sided bootstrap p-value; a product separates when the interval's lower bound is above zero;
- from each other: for every unordered product pair (a, b), the paired difference wrong_commit(a) -
  wrong_commit(b) (equal to HEADLINE(a) - HEADLINE(b): the floor cancels), its interval and p-value, the
  Holm-adjusted p-value across the decider's pairs; a pair separates when holm_p < alpha.
Across deciders: a leaderboard only if at least one pair separates on every decider with the same direction.

Two-sided bootstrap p-value over the B resamples where both sides are defined:
    p = min(1, 2 * min((k_le + 1) / (B + 1), (k_ge + 1) / (B + 1)))
with k_le and k_ge the resampled differences <= 0 and >= 0. B equals reps unless a resample left a rate
undefined (no gated slot), as `Boot.paired_diff` drops such resamples too.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable, Sequence

from .bootstrap import Boot, Sums, ci, ratio
from .metrics import _check, _features, case_frame, cell_name

__all__ = ["separation", "holm", "bootstrap_p", "SEPARATION_SCHEMA"]

SEPARATION_SCHEMA = "memory_bench.separation/1"
LEVEL = 0.95
WC = ratio("wc", "g")


def bootstrap_p(diffs: Sequence[float]) -> float | None:
    """Two-sided bootstrap p-value of a difference being zero, from its resampled values."""
    b = len(diffs)
    if b == 0:
        return None
    k_le = sum(1 for x in diffs if x <= 0)
    k_ge = sum(1 for x in diffs if x >= 0)
    return min(1.0, 2 * min((k_le + 1) / (b + 1), (k_ge + 1) / (b + 1)))


def holm(pvals: Sequence[float | None]) -> list[float | None]:
    """Holm step-down adjusted p-values, in input order. None stays None and is not counted in m.
    Ties keep input order; the adjusted values are monotone in the raw ones and capped at 1."""
    order = sorted((i for i, p in enumerate(pvals) if p is not None), key=lambda i: pvals[i])
    m = len(order)
    out: list[float | None] = [None] * len(pvals)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * float(pvals[i])))
        out[i] = running
    return out


def _diff(boot: Boot, feats_a: dict, cache_a: list[Sums], feats_b: dict, cache_b: list[Sums]) -> dict[str, Any]:
    """value, ci95 and p of WC(a) - WC(b). value and ci95 are what Boot.paired_diff gives."""
    vals = []
    for sa, sb in zip(cache_a, cache_b):
        x, y = WC(sa), WC(sb)
        if x is not None and y is not None:
            vals.append(x - y)
    x, y = WC(boot.full_sums(feats_a)), WC(boot.full_sums(feats_b))
    return {"value": None if x is None or y is None else x - y, "ci95": ci(vals, LEVEL), "p": bootstrap_p(vals),
            "n_resamples": len(vals)}


def _selector(p: Any) -> tuple[str, str, int | None]:
    if isinstance(p, dict):
        return (p["system"], p["config_label"], p["budget"])
    system, label, budget = p
    return (system, label, budget)


def _decider(rows: list[dict], prods: list[tuple], names: list[str], seed: int, reps: int,
             alpha: float) -> dict[str, Any]:
    order, idx, _a_star, _mech, _gate, gated = case_frame(rows)
    n = len(order)
    boot = Boot(n, reps, seed)

    floor_rows = [r for r in rows if r["kind"] == "floor"]
    floor = None
    if floor_rows:
        ff = _features(n, idx, floor_rows, gated)
        floor = (ff, boot.resampled_sums(ff))

    cells: dict[str, tuple[dict, list[Sums]]] = {}
    for sel, name in zip(prods, names):
        crows = [r for r in rows if r["kind"] == "view" and (r["system"], r["config_label"], r["budget"]) == sel]
        if crows:
            f = _features(n, idx, crows, gated)
            cells[name] = (f, boot.resampled_sums(f))

    from_floor: dict[str, Any] = {}
    for name in names:
        if floor is None or name not in cells:
            from_floor[name] = None
            continue
        d = _diff(boot, *cells[name], *floor)
        d["separates"] = d["ci95"] is not None and d["ci95"][0] > 0
        from_floor[name] = d

    pairs: dict[str, Any] = {}
    for a, b in combinations(names, 2):
        if a not in cells or b not in cells:
            continue
        d = _diff(boot, *cells[a], *cells[b])
        v = d["value"]
        d["direction"] = None if v is None or v == 0 else ("a_lower" if v < 0 else "b_lower")
        pairs[f"{a} | {b}"] = d
    adj = holm([d["p"] for d in pairs.values()])
    for d, hp in zip(pairs.values(), adj):
        d["holm_p"] = hp
        d["separates"] = hp is not None and hp < alpha

    return {"n_cases": n, "n_gated": len(gated), "floor": floor is not None,
            "missing_products": [x for x in names if x not in cells], "from_floor": from_floor, "pairs": pairs}


def separation(rows: Iterable[dict], products: Sequence[Any], *, seed: int = 11, reps: int = 1000,
               alpha: float = 0.05) -> dict[str, Any]:
    """The separation verdict for `products` (cell selectors (system, config_label, budget); the caller leaves
    baselines out). Deterministic in the rows, the products and the seed; JSON-serialisable."""
    rows = list(rows)
    _check(rows)
    prods = [_selector(p) for p in products]
    names = [cell_name(*p) for p in prods]
    if len(set(names)) != len(names):
        raise ValueError(f"products repeat a cell: {names}")
    by_decider: dict[str, list[dict]] = {}
    for r in rows:
        by_decider.setdefault(r["decider"], []).append(r)
    deciders = {d: _decider(rs, prods, names, seed, reps, alpha) for d, rs in sorted(by_decider.items())}

    separating = []
    for a, b in combinations(names, 2):
        key = f"{a} | {b}"
        got = [dd["pairs"].get(key) for dd in deciders.values()]
        if got and all(g is not None and g["separates"] for g in got) and len({g["direction"] for g in got}) == 1:
            separating.append({"pair": key, "direction": got[0]["direction"]})
    if len(deciders) == 0:
        leaderboard, verdict = None, "no deciders"
    elif len(deciders) == 1:
        leaderboard, verdict = None, "needs two deciders"
    else:
        leaderboard = bool(separating)
        verdict = "leaderboard" if leaderboard else "null"

    return {
        "schema": SEPARATION_SCHEMA,
        "rule": {
            "source": "CHARTER section 4.6",
            "alpha": alpha, "correction": "holm", "seed": seed, "reps": reps, "level": LEVEL,
            "interval": "percentile interval over the paired case bootstrap of summarise (same seed, same resamples)",
            "p_value": "two-sided bootstrap p = min(1, 2 * min((k_le + 1) / (B + 1), (k_ge + 1) / (B + 1))), "
                       "B = resamples where both rates are defined",
            "from_floor": "a product separates from the floor when the lower bound of the 95 percent interval of "
                          "headline (wrong_commit - floor) is above zero",
            "pairs": "for each unordered product pair a | b: wrong_commit(a) - wrong_commit(b) (the floor cancels); "
                     "separates when the Holm-adjusted p across the decider's pairs is below alpha; direction "
                     "a_lower means a has the lower wrong-commit rate",
            "leaderboard": "true only if at least one pair separates on every decider present, in the same "
                           "direction; false publishes the null; null with a single decider",
        },
        "products": names,
        "deciders": deciders,
        "separating_pairs": separating,
        "leaderboard": leaderboard,
        "verdict": verdict,
    }
