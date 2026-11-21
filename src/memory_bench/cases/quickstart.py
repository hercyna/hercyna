"""The 20-case quickstart set: a fixed, deterministic slice of the development cases.

The first 10 `M0` cases and the first 2 cases of each of M1, M2, M3, U1 and U2, taken in development-file
order, and returned in development-file order. The cases are the development cases (CC BY 4.0 data)."""

from __future__ import annotations

from .sample import dev_cases
from .schema import Case

QUICKSTART_TAKE = {"M0": 10, "M1": 2, "M2": 2, "M3": 2, "U1": 2, "U2": 2}
QUICKSTART_SHA256 = "ecfdb132f0c3778dd0f775dda372f57df74dc2941bc259664d381f04d09689af"


def quickstart_cases(cases: list[Case] | None = None) -> list[Case]:
    pool = list(dev_cases() if cases is None else cases)
    seen = dict.fromkeys(QUICKSTART_TAKE, 0)
    keep: list[Case] = []
    for c in pool:
        if c.mechanism in seen and seen[c.mechanism] < QUICKSTART_TAKE[c.mechanism]:
            seen[c.mechanism] += 1
            keep.append(c)
    return keep
