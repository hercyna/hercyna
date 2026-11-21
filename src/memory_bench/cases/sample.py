"""Rebuild the headline case sample and the 300 development cases.

Extracted from the LENS replication bundle at commit 97a622f: sample_mix, u2_pair_for and
make_headline_sample come from src/lens/flip/headline/engine.py. Function bodies are unchanged.
dev_cases applies the protocol v3 merge rule from headline/merge.py (case selection only).
"""

from __future__ import annotations

import random

from .generator import DISTANCES, GENERATOR_VERSION, U2_PAIRS, Knobs, _quotas, default_mix, generate_case
from .schema import MECHANISMS, Case

LENGTHS = (100, 400)


def sample_mix(mechanisms: tuple[str, ...] | None = None) -> dict[str, float]:
    """The default power mix, or (for a subset run) the chosen mechanisms alone."""
    mix = default_mix(0.5)
    if not mechanisms:
        return mix
    bad = [m for m in mechanisms if m not in MECHANISMS]
    if bad or len(set(mechanisms)) != len(mechanisms):
        raise ValueError(f"--mechanisms needs distinct names from {', '.join(MECHANISMS)}; got {', '.join(mechanisms)}")
    return {m: mix[m] for m in MECHANISMS if m in mechanisms}


def u2_pair_for(i: int) -> str:
    """Protocol v3: the pair of the i-th U2 case of a run. Exactly half and half over any even count, and
    crossed with the length, which alternates with i % 2."""
    return tuple(U2_PAIRS)[(i // 2 + i % 2) % 2]


def make_headline_sample(n: int, seed: int, distractors: int = 2, mechanisms: tuple[str, ...] | None = None,
                         prefix: str | None = None, generator_version: int = GENERATOR_VERSION) -> list[Case]:
    """n cases in the generator's default power mix (rare share 0.5). Within each mechanism the
    lengths alternate 100 and 400. The distance knob is drawn per case from the seeded stream.

    `mechanisms` limits the run to those mechanisms (their default shares, renormalised). `prefix` replaces
    the "h<seed>" in the case ids. `generator_version` 2 regenerates the headline-300 sample; version 3
    differs from it in U2 cases only, and plants their pairs half and half by position."""
    quotas = _quotas(n, sample_mix(mechanisms))
    rng = random.Random(f"headline|{seed}")
    plan = [(m, i) for m in MECHANISMS for i in range(quotas[m])]
    rng.shuffle(plan)
    cases = []
    for idx, (m, i) in enumerate(plan):
        knobs = Knobs(length=LENGTHS[i % 2], distance=rng.choice(DISTANCES), distractors=distractors)
        pair = u2_pair_for(i) if m == "U2" and generator_version >= 3 else None
        cases.append(generate_case(rng.randrange(2**31), m, knobs, case_id=f"{prefix or f'h{seed}'}-{idx:04d}",
                                   generator_version=generator_version, u2_pair=pair))
    return cases


def dev_cases() -> list[Case]:
    """The 300 development cases in the released order (results/headline-300-v3/cases.jsonl).

    Base run: 300 cases, seed 11, generator version 2. Protocol v3 item 6: every non-U2 base case is kept
    in base order, then the 30 cases of the U2 run (seed 12, prefix u3, generator version 3) follow."""
    base = make_headline_sample(300, 11, generator_version=2)
    u2 = make_headline_sample(30, 12, mechanisms=("U2",), prefix="u3", generator_version=3)
    return [c for c in base if c.mechanism != "U2"] + u2
