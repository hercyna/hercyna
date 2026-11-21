"""The empty baseline: a memory that returns nothing (Day 14, review finding B1).

It answers the question how a system scores that never gives the decider a record. Exploratory in protocol v1,
which does not list it among the frozen systems."""

from __future__ import annotations

from .base import _VerbatimAdapter
from .base import Retrieved


class EmptyAdapter(_VerbatimAdapter):
    name = "empty"

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        return self._walk([], budget_tokens)
