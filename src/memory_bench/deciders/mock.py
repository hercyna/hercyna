"""Mock deciders for engine tests. Deterministic, no network.

Label access is explicit and kept here: a mock is built with the case list and
applies the rule oracle to the events whose ids the view quotes. Text that is
not a quoted record is invisible to it, as in lifted.oracle_decider.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from ..cases.generator import oracle
from ..cases.schema import ACTIONS, REQUEST_INFO, Case
from .base import DecisionInput

COMMITTED = tuple(a for a in ACTIONS if a != REQUEST_INFO)


class MockOracleDecider:
    id = "mock-oracle"
    model = "mock"

    def __init__(self, cases: Sequence[Case]) -> None:
        self._cases = {c.case_id: c for c in cases}
        self.params: dict = {}

    def _oracle(self, inp: DecisionInput) -> str:
        case = self._cases[inp.case_id]
        ids = set(inp.record_ids)
        track = getattr(case, "track", "account_review")
        if track == "account_review":
            return oracle([e for e in case.events if e.id in ids], case.query_period)
        from ..tracks import get_track

        return get_track(track).oracle(case, visible_ids=ids)

    def _committed_actions(self, inp: DecisionInput) -> tuple[str, ...]:
        case = self._cases[inp.case_id]
        track = getattr(case, "track", "account_review")
        if track == "account_review":
            return COMMITTED
        from ..tracks import get_track

        meta = get_track(track).meta
        return tuple(a for a in meta.valid_actions if a != meta.abstention_action)

    def decide(self, inp: DecisionInput) -> str:
        return self._oracle(inp)


class MockNoisyDecider(MockOracleDecider):
    """The oracle, except for a deterministic share of (case_id, repeat, records text) where it
    returns a different committed action."""

    id = "mock-noisy"

    def __init__(self, cases: Sequence[Case], flip_rate: float, seed: int = 0) -> None:
        if not 0.0 <= flip_rate <= 1.0:
            raise ValueError(f"flip_rate must be in [0, 1], got {flip_rate}")
        super().__init__(cases)
        self.flip_rate = float(flip_rate)
        self.seed = int(seed)
        self.params = {"flip_rate": self.flip_rate, "seed": self.seed}

    def decide(self, inp: DecisionInput) -> str:
        right = self._oracle(inp)
        text_hash = hashlib.sha256(inp.records_text.encode("utf-8")).hexdigest()
        h = hashlib.sha256(f"{self.seed}|{inp.case_id}|{inp.repeat}|{text_hash}".encode("ascii")).digest()
        u = int.from_bytes(h[:8], "big") / 2**64
        if u >= self.flip_rate:
            return right
        others = [a for a in self._committed_actions(inp) if a != right]
        return others[h[8] % len(others)]
