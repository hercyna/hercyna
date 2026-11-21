"""The latest records whose lines fit the budget, in history order."""

from __future__ import annotations

from .base import _VerbatimAdapter
from .base import Retrieved


class RecentAdapter(_VerbatimAdapter):
    name = "recent"

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        return self._walk(list(range(len(self._records) - 1, -1, -1)), budget_tokens)
