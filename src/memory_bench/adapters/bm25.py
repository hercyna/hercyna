"""BM25 over record text. Ties go to the later record; best first until the next line does not fit."""

from __future__ import annotations

from .base import _VerbatimAdapter
from .base import Retrieved
from .legacy.rag_bm25 import bm25_scores


class Bm25Adapter(_VerbatimAdapter):
    name = "bm25"

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        scores = bm25_scores([r.text for r in self._records], query)
        order = sorted(range(len(self._records)), key=lambda i: (-scores[i], -i))
        return self._walk(order, budget_tokens)
