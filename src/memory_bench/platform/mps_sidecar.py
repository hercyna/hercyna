"""Local PyTorch MPS /v1/embeddings and /v1/rerank HTTP sidecar (127.0.0.1:11435).

Strictly lazy-loaded with `local_files_only=True` by default so zero models are ever downloaded
to disk without explicit maintainer approval (A4). Keeps `memory-bench` core `dependencies = []` (A13).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Sequence

DEFAULT_MPS_HOST = "127.0.0.1"
DEFAULT_MPS_PORT = 11435

EmbedBackend = Callable[[list[str]], list[list[float]]]
RerankBackend = Callable[[str, list[str]], list[float]]


class MpsSidecarEngine:
    """Handles OpenAI-compatible `/v1/embeddings` and `/v1/rerank` payloads locally."""

    def __init__(
        self,
        *,
        embed_model: str = "BAAI/bge-m3",
        rerank_model: str = "BAAI/bge-reranker-v2-m3",
        embed_fn: EmbedBackend | None = None,
        rerank_fn: RerankBackend | None = None,
        local_files_only: bool = True,
    ) -> None:
        self.embed_model = embed_model
        self.rerank_model = rerank_model
        self._embed_fn = embed_fn
        self._rerank_fn = rerank_fn
        self.local_files_only = bool(local_files_only)

    def _ensure_embed(self) -> EmbedBackend:
        if self._embed_fn is not None:
            return self._embed_fn
        import torch  # type: ignore[import-not-found]
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = SentenceTransformer(self.embed_model, device=device, local_files_only=self.local_files_only)

        def _run(texts: list[str]) -> list[list[float]]:
            vecs = model.encode(texts, normalize_embeddings=True)
            return [[float(x) for x in row] for row in vecs]

        self._embed_fn = _run
        return _run

    def _ensure_rerank(self) -> RerankBackend:
        if self._rerank_fn is not None:
            return self._rerank_fn
        import torch  # type: ignore[import-not-found]
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = CrossEncoder(self.rerank_model, device=device, automodel_args={"local_files_only": self.local_files_only})

        def _run(query: str, docs: list[str]) -> list[float]:
            pairs = [(query, d) for d in docs]
            scores = model.predict(pairs)
            return [float(s) for s in scores]

        self._rerank_fn = _run
        return _run

    def handle_embeddings(self, req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        inp = req.get("input")
        texts = [inp] if isinstance(inp, str) else list(inp) if isinstance(inp, Sequence) else []
        if not texts or not all(isinstance(t, str) for t in texts):
            return 400, {"error": {"message": "input must be a non-empty string or list of strings"}}
        vecs = self._ensure_embed()(texts)
        data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)]
        est_tokens = sum(max(1, len(t) // 4) for t in texts)
        return 200, {
            "object": "list",
            "model": str(req.get("model") or self.embed_model),
            "data": data,
            "usage": {"prompt_tokens": est_tokens, "total_tokens": est_tokens},
        }

    def handle_rerank(self, req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        query = req.get("query")
        docs = req.get("documents")
        if not isinstance(query, str) or not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
            return 400, {"error": {"message": "query (str) and documents (list[str]) are required"}}
        if not docs:
            return 200, {"model": self.rerank_model, "results": []}
        scores = self._ensure_rerank()(query, docs)
        ranked = sorted(
            [{"index": i, "relevance_score": float(scores[i])} for i in range(len(docs))],
            key=lambda row: (-row["relevance_score"], row["index"]),
        )
        top_n = req.get("top_n")
        if isinstance(top_n, int) and top_n > 0:
            ranked = ranked[:top_n]
        return 200, {"model": str(req.get("model") or self.rerank_model), "results": ranked}


def make_mps_server(
    engine: MpsSidecarEngine,
    *,
    host: str = DEFAULT_MPS_HOST,
    port: int = DEFAULT_MPS_PORT,
) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server around `MpsSidecarEngine`."""

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _send(self, status: int, obj: dict[str, Any]) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(200, {"ok": True, "embed_model": engine.embed_model, "rerank_model": engine.rerank_model})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                self._send(400, {"error": {"message": "invalid JSON"}})
                return
            if self.path == "/v1/embeddings":
                status, resp = engine.handle_embeddings(body)
                self._send(status, resp)
            elif self.path == "/v1/rerank":
                status, resp = engine.handle_rerank(body)
                self._send(status, resp)
            else:
                self._send(404, {"error": {"message": "not found"}})

    return ThreadingHTTPServer((host, int(port)), _Handler)


__all__ = ["DEFAULT_MPS_HOST", "DEFAULT_MPS_PORT", "MpsSidecarEngine", "make_mps_server"]
