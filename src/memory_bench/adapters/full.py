"""Reference adapter: every record. The budget is ignored."""

from __future__ import annotations

from .base import _VerbatimAdapter
from .base import Retrieved


class FullAdapter(_VerbatimAdapter):
    name = "full"
    reference = True

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        return self._pack([r.id for r in self._records])
