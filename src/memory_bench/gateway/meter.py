"""The model gateway: cache, meter, cap and call log (docs/deciders.md section 4).

Every decider call goes through `Gateway.call`. Order for a call that is not cached:
cap check and reservation -> transport -> calls.jsonl line (flushed) -> cache store -> settle. A crash
between the log line and the cache store can only over-count dollars, never under-count them.

Thread safety (parallel decision calls): one lock guards the cache, the meter, the reservations and the
call log; the transport call runs outside it. Before a live call starts, its worst-case dollars are
reserved, and the cap check counts spent plus reserved dollars, so parallel calls can never jointly pass
the cap; the reservation is released when the call ends (its real dollars are then in the meter). A
request already in flight on another thread is not sent twice: the second caller waits for the first and
then reads the reply from the cache, as a sequential run would.

The cap covers the whole run folder: the meter is loaded from the folder's calls.jsonl when a run is
opened, so a stopped run resumes only with a higher cap. With a product proxy in the run, `extra_spent`
adds the memory side's spent and reserved dollars to the cap check (docs/product-proxy.md section 5).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .cache import DEFAULT_PATH, CallCache, make_key

__all__ = ["Gateway", "CapReached", "UnpricedDecider", "CALLS", "call_key", "worst_case_usd", "price_of",
           "DEFAULT_PATH"]

CALLS = "calls.jsonl"
CHARS_PER_TOKEN = 2.2
ENVELOPE = "memory-bench/cached-reply/1"
STAGES = ("gate", "floor", "view")


class CapReached(RuntimeError):
    """The next live call could pass the run's dollar cap. Nothing was sent."""


class UnpricedDecider(ValueError):
    """The decider has a null price and the gateway was not built with allow_unpriced."""


class _Reply(Protocol):
    text: str
    input_tokens: int
    output_tokens: int
    estimated: bool


class _Sender(Protocol):
    def send(self, messages: list[dict[str, str]], *, repeat: int, retry_reminder: bool = False) -> Any: ...


@dataclass(frozen=True)
class _Cached:
    """What a cache hit returns. Same fields as transports.RawReply."""
    text: str
    input_tokens: int
    output_tokens: int
    estimated: bool = False
    retries: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def price_of(cfg: dict[str, Any]) -> dict[str, Any] | None:
    p = cfg.get("price")
    if p is None:
        return None
    return {"input_per_m": float(p["input_per_m"]), "output_per_m": float(p["output_per_m"])}


def _usd(price: dict | None, tin: int, tout: int) -> float:
    if price is None:
        return 0.0
    return round(tin * price["input_per_m"] / 1e6 + tout * price["output_per_m"] / 1e6, 6)


def worst_case_usd(cfg: dict[str, Any], messages: list[dict[str, str]]) -> float:
    """Upper-bound dollars of one live call: input estimate ceil(chars / 2.2) plus max_tokens of output."""
    price = price_of(cfg)
    if price is None:
        return 0.0
    tin = math.ceil(sum(len(m["content"]) for m in messages) / CHARS_PER_TOKEN)
    tout = int((cfg.get("params") or {}).get("max_tokens") or 0)
    return tin * price["input_per_m"] / 1e6 + tout * price["output_per_m"] / 1e6


def call_key(cfg: dict[str, Any], messages: list[dict[str, str]], repeat: int) -> str:
    """Cache key: sha256 of decider id, model, params, prompt version, messages and repeat index."""
    return make_key(cfg["model"], cfg.get("prompt_version", "v1"), messages, dict(cfg.get("params") or {}),
                    {"decider": cfg["id"], "repeat": int(repeat)})


class _SharedCache(CallCache):
    """The lifted CallCache with a connection usable from worker threads. The Gateway serialises access."""

    def __init__(self, path: str | Path):
        super().__init__(path)
        old = self.db
        new = sqlite3.connect(self.path, check_same_thread=False)
        if self.path == ":memory:":  # a fresh in-memory database: copy the table the parent created
            old.backup(new)
        old.close()
        self.db = new


def _no_extra() -> float:
    return 0.0


def _bucket() -> dict[str, Any]:
    return {"usd": 0.0, "calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0}


class Gateway:
    """Gateway(cache_path, cap_usd, run_dir=None, allow_unpriced=False). Use as a context manager or close()."""

    def __init__(self, cache_path: str | Path | None = None, cap_usd: float = 0.0, run_dir: str | Path | None = None,
                 allow_unpriced: bool = False, *, clock=time.perf_counter,
                 extra_spent: Callable[[], float] | None = None):
        self.cache_path = str(cache_path or DEFAULT_PATH)
        # Dollars spent outside this gateway that count against the same cap (the product proxy's spent plus
        # reserved). Read before the lock is taken, so the two meters' locks never nest. Default: none.
        self.extra_spent: Callable[[], float] = extra_spent or _no_extra
        self.cap_usd = float(cap_usd)
        self.allow_unpriced = bool(allow_unpriced)
        self.cache = _SharedCache(self.cache_path)
        self.clock = clock
        self.run_dir: Path | None = None
        self.lines: list[dict] = []
        self.lock = threading.RLock()
        self._reserved = 0.0  # worst-case dollars of live calls in flight
        self._inflight: dict[str, threading.Event] = {}
        if run_dir is not None:
            self.open_run(run_dir)

    # -- lifecycle ---------------------------------------------------------

    def open_run(self, run_dir: str | Path) -> None:
        """Point the call log at run_dir and load the meter from its calls.jsonl (a torn last line is cut)."""
        run_dir = Path(run_dir)
        with self.lock:
            if self.run_dir is not None and self.run_dir.resolve() == run_dir.resolve():
                return
            run_dir.mkdir(parents=True, exist_ok=True)
            self.run_dir = run_dir
            self.lines = []
            p = run_dir / CALLS
            if p.exists():
                data = p.read_bytes()
                if data and not data.endswith(b"\n"):
                    data = data[: data.rfind(b"\n") + 1]
                    p.write_bytes(data)
                self.lines = [json.loads(x) for x in data.decode("utf-8").splitlines() if x.strip()]

    def close(self) -> None:
        with self.lock:
            self.cache.close()

    def __enter__(self) -> "Gateway":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- checks ------------------------------------------------------------

    def check_decider(self, cfg: dict[str, Any]) -> None:
        if cfg.get("price") is None and not self.allow_unpriced:
            raise UnpricedDecider(f"decider {cfg.get('id')!r} has no price; pass --allow-unpriced to run it")

    def spent_usd(self) -> float:
        with self.lock:
            return round(sum(x["usd"] for x in self.lines), 6)

    def reserved_usd(self) -> float:
        """Worst-case dollars of live calls in flight right now."""
        with self.lock:
            return self._reserved

    # -- the call ----------------------------------------------------------

    def call(self, decider_cfg: dict[str, Any], transport: _Sender, messages: list[dict[str, str]], *, repeat: int,
             context: dict[str, Any] | None = None) -> Any:
        """One model call through cache, cap, transport and meter. Returns a reply with text and usage.

        context keys used in the log: case_id, kind (gate, floor, view), system, config_label, budget, reminder."""
        ctx = dict(context or {})
        self.check_decider(decider_cfg)
        key = call_key(decider_cfg, messages, repeat)
        priced = decider_cfg.get("price") is not None
        t0 = self.clock()
        while True:
            extra = float(self.extra_spent()) if priced else 0.0  # outside the lock
            with self.lock:
                hit = self.cache.get(key)
                if hit is not None:
                    reply: Any = self._unwrap(hit, messages)
                    self._log(self._line(decider_cfg, ctx, repeat, key, reply, True, self.clock() - t0))
                    return reply
                wait = self._inflight.get(key)
                if wait is None:
                    worst = worst_case_usd(decider_cfg, messages) if priced else 0.0
                    if worst > 0:
                        spent = self.spent_usd()
                        if spent + extra + self._reserved + worst > self.cap_usd + 1e-12:
                            other = f", memory side {extra:.6f}" if extra else ""
                            raise CapReached(
                                f"cap {self.cap_usd:.4f} USD: spent {spent:.6f}{other}, in flight up to "
                                f"{self._reserved:.6f}, next call could cost up to {worst:.6f} "
                                f"(decider {decider_cfg['id']})")
                    self._reserved += worst
                    mine = self._inflight[key] = threading.Event()
                    break
            wait.wait()  # the same request is in flight on another thread; then read it from the cache
        try:
            reply = transport.send(messages, repeat=repeat, retry_reminder=bool(ctx.get("reminder")))
        except BaseException:
            with self.lock:
                self._settle(key, worst, mine)
            raise
        with self.lock:
            try:
                self._log(self._line(decider_cfg, ctx, repeat, key, reply, False, self.clock() - t0))
                self.cache.put(key, decider_cfg["model"], decider_cfg.get("prompt_version", "v1"),
                               self._wrap(reply))
            finally:
                self._settle(key, worst, mine)
        return reply

    def _settle(self, key: str, worst: float, event: threading.Event) -> None:
        """Release a live call's reservation and wake the callers waiting on the same request. Under the lock."""
        self._reserved = max(0.0, self._reserved - worst)
        if self._inflight.get(key) is event:
            del self._inflight[key]
        if not self._inflight:
            self._reserved = 0.0  # no float residue once nothing is in flight
        event.set()

    @staticmethod
    def _line(cfg: dict[str, Any], ctx: dict[str, Any], repeat: int, key: str, reply: Any, cached: bool,
              seconds: float) -> dict:
        usd = 0.0 if cached else _usd(price_of(cfg), reply.input_tokens, reply.output_tokens)
        return {
            "at": _now(), "decider": cfg["id"], "case_id": ctx.get("case_id"), "kind": ctx.get("kind"),
            "system": ctx.get("system"), "config_label": ctx.get("config_label"), "budget": ctx.get("budget"),
            "repeat": int(repeat), "reminder": bool(ctx.get("reminder")),
            "request_sha256": key, "reply_sha256": _sha(reply.text),
            "input_tokens": int(reply.input_tokens), "output_tokens": int(reply.output_tokens), "usd": usd,
            "cached": cached, "estimated": bool(reply.estimated), "unpriced": cfg.get("price") is None,
            "seconds": round(seconds, 6), "retries": int(getattr(reply, "retries", 0)),
        }

    @staticmethod
    def _wrap(reply: _Reply) -> str:
        return json.dumps({"envelope": ENVELOPE, "text": reply.text, "input_tokens": int(reply.input_tokens),
                           "output_tokens": int(reply.output_tokens), "estimated": bool(reply.estimated)},
                          sort_keys=True, ensure_ascii=True)

    @staticmethod
    def _unwrap(stored: str, messages: list[dict[str, str]]) -> _Cached:
        try:
            obj = json.loads(stored)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get("envelope") == ENVELOPE:
            return _Cached(obj["text"], obj["input_tokens"], obj["output_tokens"], obj["estimated"])
        # A plain reply stored by the lifted LLMDecider: estimate its usage.
        tin = math.ceil(sum(len(m["content"]) for m in messages) / CHARS_PER_TOKEN)
        return _Cached(stored, tin, math.ceil(len(stored) / CHARS_PER_TOKEN), True)

    def _log(self, line: dict) -> None:
        """Append one call line to the meter and calls.jsonl (whole line, flushed). Called under the lock."""
        self.lines.append(line)
        if self.run_dir is None:
            return
        with (self.run_dir / CALLS).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- spend -------------------------------------------------------------

    def spend(self) -> dict[str, Any]:
        """The spend.json content over every call of the open run (all attempts).

        Dollars and the top-level token totals count live calls only; cached calls are counted in
        `cached_calls` and their stored usage in `cached_input_tokens` and `cached_output_tokens`."""
        out: dict[str, Any] = {"cap_usd": self.cap_usd, **_bucket(), "cached_input_tokens": 0,
                               "cached_output_tokens": 0, "estimated_calls": 0, "unpriced_calls": 0,
                               "by_stage": {s: _bucket() for s in STAGES}, "by_decider": {}, "by_adapter": {}}
        out["total_usd"] = 0.0
        del out["usd"]
        with self.lock:
            lines = list(self.lines)
        for x in lines:
            targets = [out]
            if x.get("kind") in STAGES:
                targets.append(out["by_stage"][x["kind"]])
            targets.append(out["by_decider"].setdefault(x["decider"], _bucket()))
            if x.get("system") is not None:
                targets.append(out["by_adapter"].setdefault(x["system"], _bucket()))
            for t in targets:
                t["calls"] += 1
                if x["cached"]:
                    t["cached_calls"] += 1
                    continue
                t["input_tokens"] += x["input_tokens"]
                t["output_tokens"] += x["output_tokens"]
                t["total_usd" if t is out else "usd"] += x["usd"]
            if x["cached"]:
                out["cached_input_tokens"] += x["input_tokens"]
                out["cached_output_tokens"] += x["output_tokens"]
            out["estimated_calls"] += bool(x.get("estimated"))
            out["unpriced_calls"] += bool(x.get("unpriced")) and not x["cached"]
        out["total_usd"] = round(out["total_usd"], 6)
        for group in ("by_stage", "by_decider", "by_adapter"):
            for b in out[group].values():
                b["usd"] = round(b["usd"], 6)
        return out
