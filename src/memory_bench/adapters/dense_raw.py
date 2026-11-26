"""Pure-Python exact-cosine dense retrieval baseline (memory-bench v2, A13: dependencies = []).

Uses `math.sumprod` + `math.sqrt` from Python 3.12 stdlib so core `memory-bench` keeps zero
third-party runtime dependencies. Embeddings come either from an injected `embedder(texts)`
callable (offline/mock/local tests) or via `ProductProxy`'s OpenAI-compatible `/embeddings`
route so `dense_raw` shares identical embeddings, caching, and metering with memory products.
"""

from __future__ import annotations

import json
import math
import urllib.request
from typing import Any, Callable, Sequence

from .base import Record, Retrieved, Usage, _VerbatimAdapter

Embedder = Callable[[list[str]], list[list[float]]]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Exact pure-Python cosine similarity using Python 3.12 math.sumprod."""
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(math.sumprod(a, a))
    nb = math.sqrt(math.sumprod(b, b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(math.sumprod(a, b) / (na * nb))


def _proxy_embed(base_url: str, api_key: str, model: str, texts: list[str], timeout_s: float = 120.0) -> list[list[float]]:
    if not texts:
        return []
    url = base_url.rstrip("/") + "/embeddings"
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    data = sorted(obj.get("data") or [], key=lambda item: int(item.get("index", 0)))
    return [[float(x) for x in item["embedding"]] for item in data]


class DenseRawAdapter(_VerbatimAdapter):
    """Exact-cosine dense retrieval over verbatim records; deterministic tie-break -i (newer wins)."""

    name = "dense_raw"
    needs_proxy = True

    def __init__(
        self,
        *,
        proxy: Any = None,
        embedder: Embedder | None = None,
        config_label: str = "default",
        embedding_model: str = "shared-embedder",
        batch_size: int = 64,
        **extra: Any,
    ) -> None:
        super().__init__()
        self.proxy = proxy
        self._embedder = embedder
        self.config_label = config_label
        self.embedding_model = embedding_model
        self.batch_size = max(1, int(batch_size))
        self.config = {"embedding_model": self.embedding_model, "batch_size": self.batch_size, **extra}
        self._scope: Any = None
        self._vectors: list[list[float]] = []

    def reset(self, case_id: str) -> None:
        super().reset(case_id)
        if self._scope is not None:
            self._scope.close()
            self._scope = None
        if self.proxy is not None and self._embedder is None:
            self._scope = self.proxy.scope(self.name, self.config_label, case_id)
        self._vectors = []

    def _embed(self, texts: list[str], phase: str) -> list[list[float]]:
        if not texts:
            return []
        if self._embedder is not None:
            return [list(map(float, v)) for v in self._embedder(texts)]
        if self._scope is None:
            raise RuntimeError("DenseRawAdapter requires either an injected embedder or a ProductProxy")
        self._scope.phase(phase)
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            out.extend(_proxy_embed(self._scope.base_url, self._scope.api_key, self.embedding_model, batch))
        return out

    def ingest(self, records: Sequence[Record]) -> Usage:
        super().ingest(records)
        if self._records:
            self._vectors = self._embed([r.text for r in self._records], phase="ingest")
        return self._scope.usage() if self._scope is not None else Usage()

    def dense_scores(self, query: str) -> list[float]:
        if not self._records:
            return []
        q_vecs = self._embed([query], phase="retrieve")
        if not q_vecs:
            return [0.0] * len(self._records)
        qv = q_vecs[0]
        return [cosine_similarity(qv, rv) for rv in self._vectors]

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        scores = self.dense_scores(query)
        order = sorted(range(len(self._records)), key=lambda i: (-scores[i], -i))
        res = self._walk(order, budget_tokens)
        if self._scope is not None:
            res.usage = self._scope.usage()
        return res

    def close(self) -> None:
        if self._scope is not None:
            self._scope.close()
            self._scope = None


__all__ = ["DenseRawAdapter", "cosine_similarity", "Embedder"]
