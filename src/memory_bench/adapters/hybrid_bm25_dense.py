"""Hybrid BM25 + Pure-Python Dense Reciprocal Rank Fusion baseline (memory-bench v2, A13).

RRF(i) = 1 / (k + r_bm25(i)) + 1 / (k + r_dense(i)), with default k = 60 and tie-break -i
(later record wins). Packs chronologically within budget via `_VerbatimAdapter._walk()`.
"""

from __future__ import annotations

from typing import Any

from .base import Retrieved
from .dense_raw import DenseRawAdapter, Embedder
from .legacy.rag_bm25 import bm25_scores


def rrf_scores(bm25: list[float], dense: list[float], *, k: int = 60) -> list[float]:
    """Compute Reciprocal Rank Fusion scores from parallel BM25 and dense similarity lists."""
    n = len(bm25)
    if n == 0:
        return []
    bm25_order = sorted(range(n), key=lambda i: (-bm25[i], -i))
    dense_order = sorted(range(n), key=lambda i: (-dense[i], -i))
    r_bm25 = [0] * n
    r_dense = [0] * n
    for rank, idx in enumerate(bm25_order, 1):
        r_bm25[idx] = rank
    for rank, idx in enumerate(dense_order, 1):
        r_dense[idx] = rank
    return [1.0 / (k + r_bm25[i]) + 1.0 / (k + r_dense[i]) for i in range(n)]


class HybridBm25DenseAdapter(DenseRawAdapter):
    """Reciprocal Rank Fusion over verbatim BM25 and exact pure-Python dense cosine."""

    name = "hybrid_bm25_dense"
    needs_proxy = True

    def __init__(
        self,
        *,
        proxy: Any = None,
        embedder: Embedder | None = None,
        config_label: str = "default",
        embedding_model: str = "shared-embedder",
        rrf_k: int = 60,
        batch_size: int = 64,
        **extra: Any,
    ) -> None:
        super().__init__(
            proxy=proxy,
            embedder=embedder,
            config_label=config_label,
            embedding_model=embedding_model,
            batch_size=batch_size,
            rrf_k=int(rrf_k),
            **extra,
        )
        self.rrf_k = max(1, int(rrf_k))

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        if not self._records:
            return self._walk([], budget_tokens)
        bm25 = bm25_scores([r.text for r in self._records], query)
        dense = self.dense_scores(query)
        fused = rrf_scores(bm25, dense, k=self.rrf_k)
        order = sorted(range(len(self._records)), key=lambda i: (-fused[i], -i))
        res = self._walk(order, budget_tokens)
        if self._scope is not None:
            res.usage = self._scope.usage()
        return res


__all__ = ["HybridBm25DenseAdapter", "rrf_scores"]
