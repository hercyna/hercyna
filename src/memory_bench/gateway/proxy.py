"""The product proxy: the memory-side gateway (docs/product-proxy.md).

Memory products make their model calls from inside their own SDKs. The one setting every SDK has is a
`base_url`, so the bench runs one local OpenAI-compatible endpoint (a `ThreadingHTTPServer` on 127.0.0.1)
and every product points at it through a per-case `Scope`.

    proxy = ProductProxy(load_memory_side("configs/memory-side/x.json"), run_dir=d, cache_path=c, cap_usd=5)
    proxy.start()
    scope = proxy.scope("mem0", "default", case_id)     # in the adapter's reset()
    client = OpenAI(base_url=scope.base_url, api_key=scope.api_key)
    scope.phase("retrieve"); ...; usage = scope.usage()
    proxy.stop()

A forwarded call: the model is pinned to the configured upstream model, `"stream": true` is refused, the
rest of the body passes through. Order for a call that is not cached: cap check and reservation (under the
lock) -> upstream call with retries (outside the lock) -> memory_calls.jsonl line (flushed) -> cache store
(2xx only) -> settle. A request already in flight is not sent twice: the second caller waits and reads it
from the cache. One cap covers the decider and the memory side: `spent_elsewhere` is read before the lock
is taken, so the proxy lock and the Gateway lock never nest.

Low-level calls are injectable so tests never touch a real command: `opener(url, data, headers, timeout)`
and `runner(argv, stdin, timeout)` as in deciders/transports.py. A key value never reaches a log line, a
file, an exception or a response; the dummy key the product sends is ignored.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..adapters.base import Usage
from ..submit.textfree import TOKEN
from .cache import DEFAULT_PATH
from .meter import CHARS_PER_TOKEN, _now, _SharedCache

if TYPE_CHECKING:  # deciders.lifted imports gateway.cache, so deciders.transports is imported lazily
    from ..deciders.transports import Opener, Runner

log = logging.getLogger("memory_bench.proxy")

__all__ = ["ProductProxy", "Scope", "MEMORY_CALLS", "DUMMY_KEY", "PHASES", "STREAM_MESSAGE",
           "UnpricedMemorySide", "load_memory_side", "memory_side_spec", "meter", "worst_case",
           "request_key"]

MEMORY_CALLS = "memory_calls.jsonl"
DUMMY_KEY = "bench-local"
PHASES = ("ingest", "retrieve")
ROUTES = {"chat/completions": "chat", "embeddings": "embeddings"}
UPSTREAM_PATH = {"chat": "/chat/completions", "embeddings": "/embeddings"}
DEFAULT_ASSUMED_MAX_OUTPUT = 4000
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_RETRIES = 3
CACHE_TAG = "memory-bench/proxy/1"
STREAM_MESSAGE = "streaming is not supported by the bench proxy; set stream to false"
SNIP = 300

TOP_KEYS = frozenset({"id", "chat", "embeddings", "timeout_s", "retries", "description"})
UPSTREAM_KEYS = frozenset({"base_url", "model", "key", "price", "assumed_max_output_tokens", "dims",
                           "description"})


class UnpricedMemorySide(ValueError):
    """An upstream has a null price and the proxy was not built with allow_unpriced."""


class _KeyFailure(Exception):
    """The upstream key could not be read. The message never holds key material."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _check_key(key: Any, where: str) -> None:
    ok = isinstance(key, dict) and len(key) == 1 and (
        (isinstance(key.get("env"), str) and key["env"])
        or (isinstance(key.get("command"), list) and key["command"]
            and all(isinstance(a, str) for a in key["command"])))
    if not ok:  # the value is not repeated: it may be a pasted key
        raise ValueError(f'{where}.key must be {{"env": "NAME"}} or {{"command": [argv]}} or absent')


def _check_upstream(up: Any, where: str) -> None:
    if not isinstance(up, dict):
        raise ValueError(f"memory-side config needs an object {where!r}")
    unknown = sorted(k for k in up if k not in UPSTREAM_KEYS and not k.startswith("_"))
    if unknown:
        raise ValueError(f"memory-side {where} has unknown keys: {unknown}")
    for k in ("base_url", "model"):
        if not isinstance(up.get(k), str) or not up[k]:
            raise ValueError(f"memory-side {where} needs a string {k!r}")
    if "price" not in up:
        raise ValueError(f"memory-side {where} needs 'price' (an object, or null when unknown)")
    p = up["price"]
    if p is not None and not (isinstance(p, dict) and all(isinstance(p.get(k), (int, float))
                                                          for k in ("input_per_m", "output_per_m"))):
        raise ValueError(f"memory-side {where}.price must be null or have numeric input_per_m and output_per_m")
    if up.get("key") is not None:
        _check_key(up["key"], where)
    for k in ("assumed_max_output_tokens", "dims"):
        if k in up and not (isinstance(up[k], int) and up[k] > 0):
            raise ValueError(f"memory-side {where}.{k} must be a positive integer")


def load_memory_side(path_or_dict: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Read and check a memory-side config (configs/memory-side/*.json). Raises ValueError naming the bad key,
    never echoing a key value. Returns a deep copy; no defaults are added, so config_sha256 is of what was given."""
    if isinstance(path_or_dict, dict):
        cfg = copy.deepcopy(path_or_dict)
    else:
        cfg = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("memory-side config must be a JSON object")
    unknown = sorted(k for k in cfg if k not in TOP_KEYS and not k.startswith("_"))
    if unknown:
        raise ValueError(f"memory-side config has unknown keys: {unknown}")
    if not isinstance(cfg.get("id"), str) or not cfg["id"]:
        raise ValueError("memory-side config needs a string 'id'")
    for where in ("chat", "embeddings"):
        _check_upstream(cfg.get(where), where)
    if "timeout_s" in cfg and not (isinstance(cfg["timeout_s"], (int, float)) and cfg["timeout_s"] > 0):
        raise ValueError("memory-side timeout_s must be a positive number")
    if "retries" in cfg and not (isinstance(cfg["retries"], int) and cfg["retries"] >= 0):
        raise ValueError("memory-side retries must be a non-negative integer")
    return cfg


def check_priced(cfg: dict[str, Any], allow_unpriced: bool) -> None:
    for where in ("chat", "embeddings"):
        if cfg[where].get("price") is None and not allow_unpriced:
            raise UnpricedMemorySide(f"memory side {cfg['id']!r}: {where} has no price; "
                                     f"pass --allow-unpriced to run it")


def memory_side_spec(cfg: dict[str, Any]) -> dict[str, Any]:
    """The manifest block. Never key material: the key is config data (an env name or an argv)."""
    text = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"id": cfg["id"], "chat_model": cfg["chat"]["model"], "embeddings_model": cfg["embeddings"]["model"],
            "config_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


# ---------------------------------------------------------------------------
# Pure helpers: meter, worst case, cache key
# ---------------------------------------------------------------------------

def _estimate(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


def _text_size(obj: Any) -> tuple[int, int]:
    """(characters in every string, count of integers) under obj. Integers are token ids in an embeddings input."""
    if isinstance(obj, str):
        return len(obj), 0
    if isinstance(obj, bool) or obj is None:
        return 0, 0
    if isinstance(obj, int):
        return 0, 1
    chars = ints = 0
    items = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else ()
    for v in items:
        c, i = _text_size(v)
        chars += c
        ints += i
    return chars, ints


def _content_chars(content: Any) -> int:
    """Characters of a message content: a string, or a list of parts whose text is under "text"."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(x["text"]) for x in content if isinstance(x, dict) and isinstance(x.get("text"), str))
    return 0


def _input_estimate(route: str, req: dict[str, Any]) -> int:
    if route == "chat":
        msgs = req.get("messages") if isinstance(req.get("messages"), list) else []
        return _estimate(sum(_content_chars(m.get("content")) for m in msgs if isinstance(m, dict)))
    chars, ints = _text_size(req.get("input"))
    return _estimate(chars) + ints


def _price(up: dict[str, Any]) -> dict[str, float] | None:
    p = up.get("price")
    if p is None:
        return None
    return {"input_per_m": float(p["input_per_m"]), "output_per_m": float(p["output_per_m"])}


def _usd(price: dict | None, tin: int, tout: int) -> float:
    if price is None:
        return 0.0
    return round(tin * price["input_per_m"] / 1e6 + tout * price["output_per_m"] / 1e6, 6)


def _int(x: Any) -> int | None:
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def meter(route: str, req: dict[str, Any], reply: bytes) -> tuple[int, int, bool]:
    """(input_tokens, output_tokens, estimated) of one 2xx reply (design section 3 rule 4).

    Chat: input = usage.prompt_tokens; output = max(completion_tokens, total_tokens - prompt_tokens).
    Embeddings: input = usage.prompt_tokens or usage.total_tokens; output 0.
    A missing part is estimated as ceil(chars / 2.2) and the call is marked estimated."""
    try:
        obj = json.loads(reply)
    except ValueError:
        obj = None
    obj = obj if isinstance(obj, dict) else {}
    u = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    pt, ct, tt = _int(u.get("prompt_tokens")), _int(u.get("completion_tokens")), _int(u.get("total_tokens"))
    if route == "embeddings":
        tin = pt if pt is not None else tt
        if tin is None:
            return _input_estimate(route, req), 0, True
        return tin, 0, False
    estimated = False
    tin = pt
    if tin is None:
        tin, estimated = _input_estimate(route, req), True
    if ct is not None and pt is not None and tt is not None:
        tout = max(ct, tt - pt)  # reasoning tokens reported outside completion_tokens are output
    elif ct is not None:
        tout = ct
    elif pt is not None and tt is not None:
        tout = tt - pt
    else:
        chars = 0
        for ch in obj.get("choices") or []:
            if isinstance(ch, dict):
                msg = ch.get("message") if isinstance(ch.get("message"), dict) else {}
                chars += _content_chars(msg.get("content")) + _content_chars(ch.get("text"))
        if not obj:
            chars = len(reply)
        tout, estimated = _estimate(chars), True
    return int(tin), int(max(tout, 0)), estimated


def worst_case(up: dict[str, Any], route: str, req: dict[str, Any]) -> float:
    """Upper-bound dollars of one live call: input estimate plus max_tokens or max_completion_tokens (else the
    upstream's assumed_max_output_tokens, default 4000) at the output price. Embeddings have no output."""
    price = _price(up)
    if price is None:
        return 0.0
    tin = _input_estimate(route, req)
    tout = 0
    if route == "chat":
        tout = _int(req.get("max_tokens")) or _int(req.get("max_completion_tokens")) or int(
            up.get("assumed_max_output_tokens") or DEFAULT_ASSUMED_MAX_OUTPUT)
    return tin * price["input_per_m"] / 1e6 + tout * price["output_per_m"] / 1e6


def request_key(up: dict[str, Any], route: str, req: dict[str, Any]) -> str:
    """sha256 of the upstream id (base_url), the route and the canonical JSON body after the model rewrite."""
    blob = json.dumps({"kind": CACHE_TAG, "upstream": str(up["base_url"]).rstrip("/"), "route": route, "body": req},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def _logged_model(name: Any) -> str | None:
    """The requested model name as logged: as sent when it is a plain token, else a short hash (no free text)."""
    if name is None:
        return None
    s = name if isinstance(name, str) else json.dumps(name, sort_keys=True)
    if TOKEN.fullmatch(s):
        return s
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _err(etype: str, message: str) -> bytes:
    return json.dumps({"error": {"message": message, "type": etype, "param": None, "code": etype}},
                      ensure_ascii=True).encode("utf-8")


def _snip(b: bytes) -> str:
    return b.decode("utf-8", "replace")[:SNIP]


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "-", s)[:96]


def _bucket() -> dict[str, Any]:
    return {"calls": 0, "cached_calls": 0, "embed_calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

class Scope:
    """One adapter's view of the proxy for one case. Build the product's SDK client with base_url and api_key."""

    def __init__(self, proxy: "ProductProxy", sid: str, adapter: str, config_label: str, case_id: str):
        self._proxy = proxy
        self.id = sid
        self.adapter, self.config_label, self.case_id = adapter, config_label, case_id
        self.base_url = f"{proxy.base_url}/s/{sid}/v1"
        self.api_key = DUMMY_KEY
        self._phase = "ingest"
        self._usage = Usage()
        self.closed = False

    def phase(self, name: str) -> None:
        """Set the phase later calls are logged under: "ingest" (the default) or "retrieve"."""
        if name not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}, got {name!r}")
        with self._proxy.lock:
            self._phase = name

    @property
    def current_phase(self) -> str:
        return self._phase

    def usage(self) -> Usage:
        """Usage measured by the proxy for this scope: every 2xx call, live or cached, so it is deterministic."""
        with self._proxy.lock:
            u = self._usage
            return Usage(model_calls=u.model_calls, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                         embed_calls=u.embed_calls)

    def close(self) -> None:
        """Unregister. Later calls on this scope get 404; usage() stays readable."""
        self._proxy._unregister(self)

    def _add(self, route: str, tin: int, tout: int) -> None:  # under the proxy lock
        if route == "chat":
            self._usage.model_calls += 1
        else:
            self._usage.embed_calls += 1
        self._usage.input_tokens += tin
        self._usage.output_tokens += tout

    def __repr__(self) -> str:
        return f"Scope({self.id!r}, phase={self._phase!r}, closed={self.closed})"


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128  # product SDKs open many connections at once; the default backlog of 5 resets them
    proxy: "ProductProxy"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "memory-bench-proxy"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - no request log on stderr
        pass

    def _read_body(self) -> bytes:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b";", 1)[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _dispatch(self, method: str) -> None:
        try:
            body = self._read_body()
            status, out = self.server.proxy._handle(method, self.path, body)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the product gets a JSON error; no text of the request is kept
            status, out = 500, _err("bench_proxy_error", f"bench proxy internal error: {type(exc).__name__}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")


# ---------------------------------------------------------------------------
# The proxy
# ---------------------------------------------------------------------------

def _no_spend() -> float:
    return 0.0


class ProductProxy:
    """ProductProxy(memory_side, run_dir=..., cache_path=..., cap_usd=0.0). start(), scope(...), stop()."""

    def __init__(self, memory_side: dict[str, Any] | str | Path, *, run_dir: str | Path | None,
                 cache_path: str | Path | None, cap_usd: float = 0.0,
                 spent_elsewhere: Callable[[], float] = _no_spend, opener: Opener | None = None,
                 runner: Runner | None = None, clock: Callable[[], float] = time.monotonic,
                 allow_unpriced: bool = False, port: int = 0, sleep: Callable[[float], None] | None = None,
                 backoff_s: float = 2.0, environ: dict[str, str] | None = None):
        from ..deciders.transports import subprocess_runner, urllib_opener

        self.cfg = load_memory_side(memory_side)
        check_priced(self.cfg, allow_unpriced)
        self.upstreams = {"chat": self.cfg["chat"], "embeddings": self.cfg["embeddings"]}
        self.timeout_s = float(self.cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
        self.retries = int(self.cfg.get("retries", DEFAULT_RETRIES))
        self.cap_usd = float(cap_usd)
        self.spent_elsewhere = spent_elsewhere
        self.opener = opener or urllib_opener
        self.runner = runner or subprocess_runner
        self.environ = environ if environ is not None else os.environ
        self.clock = clock
        self.sleep = sleep or time.sleep
        self.backoff_s = backoff_s
        self.cache_path = str(cache_path or DEFAULT_PATH)
        self.cache = _SharedCache(self.cache_path)
        self.lock = threading.RLock()
        self.lines: list[dict] = []
        self._spent = 0.0
        self._reserved = 0.0
        self._inflight: dict[str, threading.Event] = {}
        self._scopes: dict[str, Scope] = {}
        self.cap_reached = False
        self.cap_detail: str | None = None
        # (adapter, config_label, case_id) of every scope that had a call refused at the cap: the engine's view
        # stage does not store the view of such an item, even when the product swallowed the refusal.
        self.refused: set[tuple[str, str, str]] = set()
        self.port = int(port)
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._cache_open = True
        self.run_dir: Path | None = None
        if run_dir is not None:
            self._open_log(Path(run_dir))

    # -- lifecycle ---------------------------------------------------------

    def _open_log(self, run_dir: Path) -> None:
        """Load the meter from run_dir/memory_calls.jsonl (a torn last line is cut): the cap covers the folder."""
        run_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir = run_dir
        p = run_dir / MEMORY_CALLS
        if p.exists():
            data = p.read_bytes()
            if data and not data.endswith(b"\n"):
                data = data[: data.rfind(b"\n") + 1]
                p.write_bytes(data)
            for x in data.decode("utf-8").splitlines():
                if x.strip():
                    line = json.loads(x)
                    self.lines.append(line)
                    self._spent += line["usd"]

    def start(self) -> None:
        """Bind 127.0.0.1 on the configured port (0: a free one) and serve in a daemon thread."""
        if self._server is not None:
            return
        server = _Server(("127.0.0.1", self.port), _Handler)
        server.proxy = self
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="product-proxy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop serving, drop every scope and close the cache. Meter, spend() and scope usage stay readable."""
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=10)
        with self.lock:
            for s in self._scopes.values():
                s.closed = True
            self._scopes.clear()
            if self._cache_open:
                self.cache.close()
                self._cache_open = False

    def __enter__(self) -> "ProductProxy":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- scopes ------------------------------------------------------------

    def scope(self, adapter: str, config_label: str, case_id: str) -> Scope:
        """Register and return a scope for one adapter, config label and case."""
        if self._server is None:
            raise RuntimeError("start the proxy before asking for a scope")
        sid = f"{_safe(f'{adapter}.{config_label}.{case_id}')}-{secrets.token_hex(3)}"
        s = Scope(self, sid, str(adapter), str(config_label), str(case_id))
        with self.lock:
            self._scopes[sid] = s
        return s

    def _unregister(self, s: Scope) -> None:
        with self.lock:
            if self._scopes.get(s.id) is s:
                del self._scopes[s.id]
            s.closed = True

    # -- meter -------------------------------------------------------------

    def spent_usd(self) -> float:
        """Live dollars over the run folder, memory side only."""
        with self.lock:
            return round(self._spent, 6)

    def reserved_usd(self) -> float:
        """Worst-case dollars of live memory-side calls in flight right now."""
        with self.lock:
            return self._reserved

    def spend(self) -> dict[str, dict[str, Any]]:
        """By adapter: calls (chat and embeddings, live and cached), cached_calls, embed_calls, and the live
        calls' input_tokens, output_tokens and usd."""
        with self.lock:
            lines = list(self.lines)
        out: dict[str, dict[str, Any]] = {}
        for x in lines:
            b = out.setdefault(x["adapter"], _bucket())
            b["calls"] += 1
            b["embed_calls"] += x["route"] == "embeddings"
            if x["cached"]:
                b["cached_calls"] += 1
                continue
            b["input_tokens"] += x["input_tokens"]
            b["output_tokens"] += x["output_tokens"]
            b["usd"] += x["usd"]
        for b in out.values():
            b["usd"] = round(b["usd"], 6)
        return out

    # -- routes ------------------------------------------------------------

    def _handle(self, method: str, path: str, body: bytes) -> tuple[int, bytes]:
        path = path.split("?", 1)[0]
        if path == "/healthz" and method == "GET":
            return 200, b'{"ok": true}'
        m = re.fullmatch(r"/s/([^/]+)/v1/(.+?)/?", path)
        if not m:
            return 404, _err("not_found", f"no route {path}")
        with self.lock:
            scope = self._scopes.get(m.group(1))
        if scope is None:
            return 404, _err("not_found", f"unknown or closed scope in {path}")
        rest = m.group(2)
        if method == "GET" and rest == "models":
            data = [{"id": self.upstreams[r]["model"], "object": "model", "owned_by": "memory-bench"}
                    for r in ("chat", "embeddings")]
            return 200, json.dumps({"object": "list", "data": data}).encode("utf-8")
        if method == "POST" and rest in ROUTES:
            return self._forward(scope, ROUTES[rest], body)
        return 404, _err("not_found", f"no route {method} {path}")

    def _forward(self, scope: Scope, route: str, raw: bytes) -> tuple[int, bytes]:
        try:
            req = json.loads(raw)
        except ValueError:
            return 400, _err("invalid_request_error", "request body is not JSON")
        if not isinstance(req, dict):
            return 400, _err("invalid_request_error", "request body is not a JSON object")
        if req.get("stream") is True:
            return 400, _err("invalid_request_error", STREAM_MESSAGE)
        up = self.upstreams[route]
        requested = req.get("model")
        req["model"] = up["model"]  # the model is pinned
        key = request_key(up, route, req)
        price = _price(up)
        t0 = self.clock()
        with self.lock:
            phase = scope.current_phase
        ctx = {"adapter": scope.adapter, "config_label": scope.config_label, "case_id": scope.case_id,
               "phase": phase, "route": route, "upstream": str(up["base_url"]).rstrip("/"), "model": up["model"],
               "requested_model": _logged_model(requested), "request_sha256": key}
        while True:
            elsewhere = float(self.spent_elsewhere()) if price is not None else 0.0  # outside our lock
            with self.lock:
                hit = self.cache.get(key) if self._cache_open else None
                if hit is not None:
                    reply = hit.encode("utf-8")
                    tin, tout, est = meter(route, req, reply)
                    self._log({**ctx, **self._result(reply, tin, tout, 0.0, True, est, 200, t0, 0)})
                    scope._add(route, tin, tout)
                    return 200, reply
                wait = self._inflight.get(key)
                if wait is None:
                    worst = worst_case(up, route, req)
                    if worst > 0 and elsewhere + self._spent + self._reserved + worst > self.cap_usd + 1e-12:
                        self.cap_reached = True
                        self.refused.add((scope.adapter, scope.config_label, scope.case_id))
                        self.cap_detail = (f"cap {self.cap_usd:.4f} USD: decider side {elsewhere:.6f}, memory side "
                                           f"spent {self._spent:.6f}, in flight up to {self._reserved:.6f}, next "
                                           f"memory-side call could cost up to {worst:.6f} (adapter {scope.adapter}, "
                                           f"case {scope.case_id}, {route})")
                        return 400, _err("bench_cap_reached", self.cap_detail)
                    self._reserved += worst
                    mine = self._inflight[key] = threading.Event()
                    break
            wait.wait()  # the same request is in flight; then read it from the cache
        try:
            data = json.dumps(req, ensure_ascii=False).encode("utf-8")
            status, reply, retries = self._send(up, route, data)
        except BaseException:
            with self.lock:
                self._settle(key, worst, mine)
            raise
        with self.lock:
            try:
                ok = 200 <= status < 300
                tin, tout, est = meter(route, req, reply) if ok else (0, 0, False)
                usd = _usd(price, tin, tout) if ok else 0.0
                self._log({**ctx, **self._result(reply, tin, tout, usd, False, est, status, t0, retries)})
                if ok:
                    scope._add(route, tin, tout)
                    try:
                        text = reply.decode("utf-8")
                    except UnicodeDecodeError:
                        text = None
                    if text is not None and self._cache_open:
                        self.cache.put(key, up["model"], f"{CACHE_TAG}/{route}", text)
            finally:
                self._settle(key, worst, mine)
        return status, reply

    def _result(self, reply: bytes, tin: int, tout: int, usd: float, cached: bool, est: bool, status: int,
                t0: float, retries: int) -> dict[str, Any]:
        return {"reply_sha256": hashlib.sha256(reply).hexdigest(), "input_tokens": int(tin),
                "output_tokens": int(tout), "usd": usd, "cached": cached, "estimated": bool(est),
                "status": int(status), "seconds": round(self.clock() - t0, 6), "retries": int(retries)}

    def _settle(self, key: str, worst: float, event: threading.Event) -> None:
        """Release a live call's reservation and wake callers waiting on the same request. Under the lock."""
        self._reserved = max(0.0, self._reserved - worst)
        if self._inflight.get(key) is event:
            del self._inflight[key]
        if not self._inflight:
            self._reserved = 0.0
        event.set()

    def _log(self, line: dict) -> None:
        """Append one line to the meter and memory_calls.jsonl (whole line, flushed). Under the lock."""
        line = {"at": _now(), **line}
        self.lines.append(line)
        self._spent += line["usd"]
        if self.run_dir is None:
            return
        with (self.run_dir / MEMORY_CALLS).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- upstream ----------------------------------------------------------

    def _headers(self, up: dict[str, Any]) -> dict[str, str]:
        """Upstream request headers. The only function that holds the key value; it goes straight to the opener."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        ref = up.get("key")
        if not ref:
            return headers
        if "env" in ref:
            key = self.environ.get(ref["env"], "")
            if not key:
                raise _KeyFailure(f"environment variable {ref['env']} is not set")
        else:
            try:
                code, out, _err_text = self.runner(list(ref["command"]), None, 60.0)
            except TimeoutError:
                raise _KeyFailure("key command timed out") from None
            if code != 0:
                raise _KeyFailure(f"key command exited {code}")
            key = out.strip()
            if not key:
                raise _KeyFailure("key command printed nothing")
        headers["Authorization"] = f"Bearer {key}"
        return headers

    def _send(self, up: dict[str, Any], route: str, data: bytes) -> tuple[int, bytes, int]:
        """One forwarded call with retries on timeouts, connection faults, 429 and 5xx. Returns
        (status, body, retries). The final error status and the upstream's JSON error body pass through."""
        url = str(up["base_url"]).rstrip("/") + UPSTREAM_PATH[route]
        last: tuple[int, bytes] = (502, _err("bench_upstream_unreachable", "no attempt made"))
        for attempt in range(self.retries + 1):
            try:
                status, body = self.opener(url, data, self._headers(up), self.timeout_s)
            except _KeyFailure as exc:
                return 500, _err("bench_key_error", f"memory side {route}: {exc}"), attempt
            except (TimeoutError, ConnectionError) as exc:
                what = type(exc).__name__
                last = (504 if isinstance(exc, TimeoutError) else 502,
                        _err("bench_upstream_unreachable", f"memory side {route} upstream: {what}: {str(exc)[:SNIP]}"))
            else:
                if not (status == 429 or status >= 500):
                    return status, self._error_body(status, body), attempt
                last = (status, body)
                what = f"HTTP {status}"
            if attempt < self.retries:
                delay = self.backoff_s * (2 ** attempt)
                log.warning("product proxy %s %s: %s; retry %d of %d in %.1f s", route, up["model"], what,
                            attempt + 1, self.retries, delay)
                self.sleep(delay)
        status, body = last
        return status, self._error_body(status, body), self.retries

    @staticmethod
    def _error_body(status: int, body: bytes) -> bytes:
        """A 2xx body or a JSON error body passes through unchanged; a non-JSON error body is wrapped."""
        if 200 <= status < 300:
            return body
        try:
            json.loads(body)
            return body
        except ValueError:
            return _err("upstream_error", f"upstream HTTP {status}: {_snip(body)}")
