"""Fakes for product-proxy tests: a fake OpenAI-compatible upstream on 127.0.0.1, a urllib client and a
test-only product adapter. No network beyond the loopback interface, no real key command."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence

from memory_bench.adapters.base import Record, Retrieved
from memory_bench.adapters.recent import RecentAdapter

CHAT_MODEL = "upstream-chat-1"
EMBED_MODEL = "upstream-embed-1"


def _content_chars(messages: Any) -> int:
    n = 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            n += len(c)
    return n


def chat_reply(req: dict, completion_tokens: int = 40, usage: dict | None = None) -> dict:
    """A canned chat completion. prompt_tokens = content chars // 3 (at most the proxy's chars / 2.2 estimate)."""
    pt = _content_chars(req.get("messages")) // 3
    u = usage if usage is not None else {"prompt_tokens": pt, "completion_tokens": completion_tokens,
                                         "total_tokens": pt + completion_tokens}
    out = {"id": "chatcmpl-fake", "object": "chat.completion", "model": req.get("model"),
           "choices": [{"index": 0, "message": {"role": "assistant", "content": "a short summary"},
                        "finish_reason": "stop"}]}
    if u:
        out["usage"] = u
    return out


def embed_reply(req: dict, usage: dict | None = None) -> dict:
    inp = req.get("input")
    items = inp if isinstance(inp, list) else [inp]
    n = sum(len(x) for x in items if isinstance(x, str)) // 4
    u = usage if usage is not None else {"prompt_tokens": n, "total_tokens": n}
    out = {"object": "list", "model": req.get("model"),
           "data": [{"object": "embedding", "index": i, "embedding": [0.25, -0.5, float(i)]}
                    for i in range(len(items))]}
    if u:
        out["usage"] = u
    return out


class _Upstream(ThreadingHTTPServer):
    request_queue_size = 128


class FakeUpstream:
    """A ThreadingHTTPServer answering POST /v1/chat/completions and /v1/embeddings. Records every request
    (path, headers, parsed body). `script[route]` holds (status, body_dict_or_bytes) answers used first, in
    order; after that the canned replies. `delay` slows every answer (for concurrency tests)."""

    def __init__(self, delay: float = 0.0, completion_tokens: int = 40):
        self.requests: list[dict] = []
        self.script: dict[str, list[tuple[int, Any]]] = {"chat": [], "embeddings": []}
        self.lock = threading.Lock()
        self.delay = delay
        self.completion_tokens = completion_tokens
        self.usage_override: dict[str, dict | None] = {}
        self.now = self.peak = 0
        up = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                import time

                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                route = {"/v1/chat/completions": "chat", "/v1/embeddings": "embeddings"}.get(self.path)
                with up.lock:
                    up.requests.append({"path": self.path, "headers": dict(self.headers.items()), "body": body,
                                        "route": route})
                    scripted = up.script[route].pop(0) if route and up.script[route] else None
                    up.now += 1
                    up.peak = max(up.peak, up.now)
                try:
                    if up.delay:
                        time.sleep(up.delay)
                    if route is None:
                        status, out = 404, {"error": {"message": "no such path", "type": "not_found"}}
                    elif scripted is not None:
                        status, out = scripted
                    elif route == "chat":
                        status, out = 200, chat_reply(body, up.completion_tokens, up.usage_override.get("chat"))
                    else:
                        status, out = 200, embed_reply(body, up.usage_override.get("embeddings"))
                finally:
                    with up.lock:
                        up.now -= 1
                raw = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = _Upstream(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def count(self, route: str | None = None) -> int:
        with self.lock:
            return sum(1 for r in self.requests if route is None or r["route"] == route)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "FakeUpstream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def memory_side(up: FakeUpstream, *, chat_price=(1.25, 2.50), embed_price=(0, 0), key=None, **extra) -> dict:
    def price(p):
        return None if p is None else {"input_per_m": p[0], "output_per_m": p[1], "as_of": "2026-11-19",
                                       "source": "test"}
    chat = {"base_url": up.base_url, "model": CHAT_MODEL, "price": price(chat_price), "assumed_max_output_tokens": 4000}
    if key is not None:
        chat["key"] = key
    return {"id": "test-mem", "chat": chat,
            "embeddings": {"base_url": up.base_url, "model": EMBED_MODEL, "dims": 3, "price": price(embed_price)},
            "timeout_s": 10, "retries": 2, **extra}


def call(url: str, body: Any = None, *, method: str | None = None, api_key: str = "bench-local",
         raw: bytes | None = None) -> tuple[int, Any]:
    """One request with urllib, as a product SDK would send it. Returns (status, parsed JSON or bytes)."""
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method or ("GET" if data is None else "POST"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, out = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, out = exc.code, exc.read()
    try:
        return status, json.loads(out)
    except ValueError:
        return status, out


class ProbeProductAdapter:
    """A test-only product adapter: embeds the records at ingest and asks the chat model at retrieve, both
    through its proxy scope with urllib; returns the latest records within the budget (as `recent` does) with
    the usage the proxy measured."""

    name = "probe"
    product_version = "test-0"
    config_label = "default"
    reference = False
    needs_proxy = True

    def __init__(self, proxy, **config) -> None:
        self.proxy = proxy
        self.config = dict(config)
        self.scope = None
        self.inner = RecentAdapter()
        self.seen_elsewhere: list[float] = []

    def reset(self, namespace: str) -> None:
        if self.scope is not None:
            self.scope.close()
        self.scope = self.proxy.scope(self.name, self.config_label, namespace)
        self.inner.reset(namespace)

    def _post(self, route: str, body: dict) -> dict:
        status, out = call(f"{self.scope.base_url}/{route}", body, api_key=self.scope.api_key)
        if status != 200:
            raise RuntimeError(f"product proxy answered {status}")
        return out

    def ingest(self, records: Sequence[Record]) -> None:
        self.scope.phase("ingest")
        self._post("embeddings", {"model": "text-embedding-3-small", "input": [r.text for r in records]})
        self.inner.ingest(records)

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        self.scope.phase("retrieve")
        self.seen_elsewhere.append(self.proxy.spent_elsewhere())
        got = self.inner.retrieve(query, budget_tokens)
        self._post("chat/completions", {"model": "gpt-4o-mini", "max_tokens": 50, "stream": False,
                                        "messages": [{"role": "user", "content": query + "\n" + got.text}]})
        got.usage = self.scope.usage()
        return got

    def close(self) -> None:
        if self.scope is not None:
            self.scope.close()
            self.scope = None
