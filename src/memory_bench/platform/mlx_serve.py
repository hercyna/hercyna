"""Local OpenAI-compatible MLX inference server wrapper (`hercyna-mlx-serve`, 127.0.0.1:11440).

Provides:
- Case-scoped KV prefix cache keyed by `(case_id, budget, system_prompt_sha256)`, flushed completely
  on `POST /v1/reset` or `reset()`.
- Explicit `enable_thinking` flag (default `False` for Mode A JSON).
- Hard `max_output_tokens = 2048` ceiling and N-gram repetition-loop detector/cutoff.
- Lazy `mlx_lm` import so core `memory-bench` keeps `dependencies = []` (A13).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

DEFAULT_MLX_HOST = "127.0.0.1"
DEFAULT_MLX_PORT = 11440
DEFAULT_MAX_OUTPUT_TOKENS = 2048


def detect_repetition_loop(text: str, *, ngram_words: int = 8, max_repeats: int = 4) -> bool:
    """Return True if any N-word window repeats consecutively >= max_repeats times."""
    words = text.split()
    if len(words) < ngram_words * max_repeats:
        return False
    seen: dict[tuple[str, ...], int] = {}
    for i in range(len(words) - ngram_words + 1):
        gram = tuple(words[i : i + ngram_words])
        seen[gram] = seen.get(gram, 0) + 1
        if seen[gram] >= max_repeats:
            return True
    return False


def clip_repetition(text: str, *, ngram_words: int = 8, max_repeats: int = 4) -> tuple[str, bool]:
    """Truncate runaway N-gram repetition loops cleanly, returning (text, clipped)."""
    words = text.split()
    if len(words) < ngram_words * max_repeats:
        return text, False
    seen: dict[tuple[str, ...], int] = {}
    for i in range(len(words) - ngram_words + 1):
        gram = tuple(words[i : i + ngram_words])
        cnt = seen.get(gram, 0) + 1
        seen[gram] = cnt
        if cnt >= max_repeats:
            return " ".join(words[:i]), True
    return text, False


@dataclass
class CaseScopedKVCache:
    """Prompt prefix cache scoped strictly to one active case_id, flushed on reset()."""

    active_case_id: str | None = None
    entries: dict[tuple[str, str, str], Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def reset(self, case_id: str | None = None) -> int:
        cleared = len(self.entries)
        self.entries.clear()
        self.active_case_id = case_id
        return cleared

    def lookup(self, case_id: str, budget: str, system_prompt: str) -> Any | None:
        if self.active_case_id is not None and case_id != self.active_case_id:
            self.reset(case_id)
        elif self.active_case_id is None:
            self.active_case_id = case_id
        key = (case_id, str(budget), hashlib.sha256(system_prompt.encode("utf-8")).hexdigest())
        if key in self.entries:
            self.hits += 1
            return self.entries[key]
        self.misses += 1
        return None

    def store(self, case_id: str, budget: str, system_prompt: str, payload: Any) -> None:
        if self.active_case_id != case_id:
            self.reset(case_id)
        key = (case_id, str(budget), hashlib.sha256(system_prompt.encode("utf-8")).hexdigest())
        self.entries[key] = payload


GenerateFn = Callable[[str, list[dict[str, str]], int, bool, Any], tuple[str, int, int, Any]]


def _make_mlx_backend(model_path: str) -> GenerateFn:
    """Lazy-load mlx_lm backend only when serving a live local model."""
    import mlx_lm  # type: ignore[import-not-found]

    model, tokenizer = mlx_lm.load(model_path)

    def _gen(
        _model_name: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        enable_thinking: bool,
        _cached_prefix: Any,
    ) -> tuple[str, int, int, Any]:
        kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
        try:
            prompt = tokenizer.apply_chat_template(messages, enable_thinking=enable_thinking, **kwargs)
        except TypeError:
            prompt = tokenizer.apply_chat_template(messages, **kwargs)
        out = mlx_lm.generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
        tin = len(tokenizer.encode(prompt))
        tout = len(tokenizer.encode(out))
        return str(out), int(tin), int(tout), {"prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}

    return _gen


class MlxServeEngine:
    """Core request engine backing `hercyna-mlx-serve` (testable without opening a socket)."""

    def __init__(
        self,
        model_id: str,
        *,
        generate_fn: GenerateFn | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.model_id = model_id
        self._generate_fn = generate_fn
        self.max_output_tokens = int(max_output_tokens)
        self.kv_cache = CaseScopedKVCache()
        self.lock = threading.Lock()
        self.total_gpu_seconds = 0.0

    def _backend(self) -> GenerateFn:
        if self._generate_fn is None:
            self._generate_fn = _make_mlx_backend(self.model_id)
        return self._generate_fn

    def reset(self, case_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            cleared = self.kv_cache.reset(case_id)
        return {"ok": True, "cleared_entries": cleared, "active_case_id": case_id}

    def handle_chat_completions(self, req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        messages = req.get("messages")
        if not isinstance(messages, list) or not messages:
            return 400, {"error": {"message": "messages must be a non-empty list"}}

        case_id = str(req.get("case_id") or "default")
        budget = str(req.get("budget") or "full")
        enable_thinking = bool(req.get("enable_thinking", False))
        req_max = int(req.get("max_tokens") or self.max_output_tokens)
        max_tokens = max(1, min(req_max, self.max_output_tokens))

        sys_prompt = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")

        with self.lock:
            t0 = time.monotonic()
            cached_prefix = self.kv_cache.lookup(case_id, budget, sys_prompt)
            text, tin, tout, new_prefix = self._backend()(
                self.model_id, messages, max_tokens, enable_thinking, cached_prefix
            )
            elapsed = max(0.0, time.monotonic() - t0)
            self.total_gpu_seconds += elapsed
            if cached_prefix is None and new_prefix is not None:
                self.kv_cache.store(case_id, budget, sys_prompt, new_prefix)

        clipped_text, was_loop = clip_repetition(text)
        finish_reason = "repetition_cutoff" if was_loop else "stop"
        return 200, {
            "id": f"mlx-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "model": self.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": clipped_text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": int(tin),
                "completion_tokens": int(tout),
                "total_tokens": int(tin + tout),
            },
            "hercyna_local": {
                "gpu_seconds": round(elapsed, 6),
                "kv_cache_hit": cached_prefix is not None,
                "enable_thinking": enable_thinking,
                "repetition_cutoff": was_loop,
            },
        }


def make_mlx_server(
    engine: MlxServeEngine,
    *,
    host: str = DEFAULT_MLX_HOST,
    port: int = DEFAULT_MLX_PORT,
) -> ThreadingHTTPServer:
    """Create a loopback-only HTTP server around `MlxServeEngine`."""

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
                self._send(200, {"ok": True, "model": engine.model_id, "kv_hits": engine.kv_cache.hits})
            elif self.path == "/v1/models":
                self._send(200, {"object": "list", "data": [{"id": engine.model_id, "object": "model"}]})
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
            if self.path == "/v1/reset":
                self._send(200, engine.reset(body.get("case_id")))
            elif self.path == "/v1/chat/completions":
                status, resp = engine.handle_chat_completions(body)
                self._send(status, resp)
            else:
                self._send(404, {"error": {"message": "not found"}})

    return ThreadingHTTPServer((host, int(port)), _Handler)


__all__ = [
    "DEFAULT_MLX_HOST",
    "DEFAULT_MLX_PORT",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "CaseScopedKVCache",
    "MlxServeEngine",
    "detect_repetition_loop",
    "clip_repetition",
    "make_mlx_server",
]
