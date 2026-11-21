"""Paired case-level bootstrap, ported from LENS (lens.flip.headline.metrics: Boot, ci, ratio).

The resample stream is identical to LENS: `random.Random(f"boot|{seed}")`, one
`Counter(rng.randrange(n) for _ in range(n))` per resample, stored as `sorted(c.items())`.
One list of resamples is shared by every metric, so differences of metrics are paired.

A metric is a function of sums of per-case features. Every feature the scorer builds is an
integer count stored as a float, so a resampled sum is exact whatever the summation order.
`resampled_sums` caches the sums of one feature set across all resamples, which lets many
ratios on the same cell share one pass.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Callable, Mapping, Sequence

Sums = dict[str, float]
Metric = Callable[[Sums], "float | None"]


class Boot:
    def __init__(self, n: int, reps: int = 1000, seed: int = 0):
        self.n = n
        self.reps = reps
        self.seed = seed
        rng = random.Random(f"boot|{seed}")
        self.samples: list[list[tuple[int, int]]] = []
        for _ in range(reps if n else 0):
            c = Counter(rng.randrange(n) for _ in range(n))
            self.samples.append(sorted(c.items()))
        # Dense weight vectors of the same resamples, for fast exact sums of integer features.
        self._weights: list[list[float]] = []
        for s in self.samples:
            w = [0.0] * n
            for i, k in s:
                w[i] = float(k)
            self._weights.append(w)

    def resampled_sums(self, feats: Mapping[str, Sequence[float]]) -> list[Sums]:
        """Feature sums for every resample, in resample order."""
        cols = {k: [float(x) for x in v] for k, v in feats.items()}
        for k, v in cols.items():
            if len(v) != self.n:
                raise ValueError(f"feature {k!r} has {len(v)} entries, expected {self.n}")
        return [{k: math.sumprod(w, v) for k, v in cols.items()} for w in self._weights]

    @staticmethod
    def full_sums(feats: Mapping[str, Sequence[float]]) -> Sums:
        return {k: float(sum(v)) for k, v in feats.items()}

    def stat(self, feats: Mapping[str, Sequence[float]], f: Metric,
             cache: list[Sums] | None = None) -> dict:
        """{value, ci95, sums} for metric f, as in LENS. Pass `cache` from resampled_sums to reuse it."""
        full = self.full_sums(feats)
        rs = cache if cache is not None else self.resampled_sums(feats)
        vals = [x for x in (f(s) for s in rs) if x is not None]
        return {"value": f(full), "ci95": ci(vals), "sums": {k: round(v, 6) for k, v in full.items()}}

    def paired_diff(self, feats_a: Mapping[str, Sequence[float]], f_a: Metric,
                    feats_b: Mapping[str, Sequence[float]], f_b: Metric,
                    cache_a: list[Sums] | None = None, cache_b: list[Sums] | None = None) -> dict:
        """{value, ci95} of f_a(feats_a) - f_b(feats_b), both evaluated on the same resamples.

        A resample where either side is undefined (None) is dropped, as LENS drops None values.
        """
        ra = cache_a if cache_a is not None else self.resampled_sums(feats_a)
        rb = cache_b if cache_b is not None else self.resampled_sums(feats_b)
        vals = []
        for sa, sb in zip(ra, rb):
            x, y = f_a(sa), f_b(sb)
            if x is not None and y is not None:
                vals.append(x - y)
        x, y = f_a(self.full_sums(feats_a)), f_b(self.full_sums(feats_b))
        return {"value": None if x is None or y is None else x - y, "ci95": ci(vals)}


def ci(vals: list[float], level: float = 0.95) -> list[float] | None:
    if len(vals) < 2:
        return None
    v = sorted(vals)

    def q(p: float) -> float:
        k = p * (len(v) - 1)
        lo = int(k)
        hi = min(lo + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)

    a = (1 - level) / 2
    return [round(q(a), 6), round(q(1 - a), 6)]


def ratio(num: str, den: str) -> Metric:
    return lambda s: s[num] / s[den] if s[den] else None
