"""Step 5 OpenCode NDJSON metering wrapper and Zen HTTP opener (Round 5 A2).

``src/memory_bench/deciders/transports.py`` is frozen in ``protocol/v2.json`` Step 1 and MUST NOT
be edited. This module wraps frozen ``OpenCodeCliTransport`` (keeping its exact hermetic XDG
isolation, ``tools: {"*": false}``, ``permission: {"*": "deny"}``, ``OPENCODE_DB=:memory:``, and
retry logic untouched) and fixes input-token metering for OpenCode NDJSON replies:

    billable_input_tokens  = tokens.input + tokens.cache.read + tokens.cache.write
    billable_output_tokens = tokens.output + tokens.reasoning

All input tokens (uncached, cache-read, and cache-write) are priced conservatively at the model's
full ``input_per_m`` rate via the frozen ``Gateway`` meter. Full token breakdowns (including
``cache_read``, ``cache_write``, ``reasoning``, and ``provider_cost_usd``) are tracked thread-safely
across ``parallel > 1`` calls.

Also provides ``zen_urllib_opener`` for OpenCode Zen / Inference HTTP endpoints behind Cloudflare
(adds a standard ``User-Agent`` header and delegates to frozen ``urllib_opener``).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..deciders.model import ModelDecider, load_config
from ..deciders.transports import (
    AnthropicCommandTransport,
    OpenCodeCliTransport,
    Opener,
    RawReply,
    Runner,
    Transport,
    make_transport,
    subprocess_runner,
    urllib_opener,
)
from ..gateway.meter import Gateway

METER_STEP = 5
METER_FORMULA = (
    "input_tokens=tokens.input+tokens.cache.read+tokens.cache.write;"
    "output_tokens=tokens.output+tokens.reasoning"
)
ZEN_USER_AGENT = "memory-bench/0.0.1"


@dataclass(frozen=True)
class OpenCodeTokenUsage:
    """Parsed token breakdown across all ``step_finish`` events in an OpenCode NDJSON stream."""

    raw_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    raw_output: int = 0
    reasoning: int = 0
    provider_cost_usd: float = 0.0
    steps: int = 0
    has_input: bool = False
    has_output: bool = False

    @property
    def billable_input(self) -> int:
        return self.raw_input + self.cache_read + self.cache_write

    @property
    def billable_output(self) -> int:
        return self.raw_output + self.reasoning

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_input": self.raw_input,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "billable_input": self.billable_input,
            "raw_output": self.raw_output,
            "reasoning": self.reasoning,
            "billable_output": self.billable_output,
            "provider_cost_usd": round(self.provider_cost_usd, 8),
            "steps": self.steps,
        }


def _opt_int(val: Any) -> int | None:
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def parse_opencode_ndjson_tokens(stdout: str) -> OpenCodeTokenUsage:
    """Parse all ``step_finish`` NDJSON lines and sum input, cache read/write, output, reasoning, and cost."""
    raw_in = 0
    cache_r = 0
    cache_w = 0
    raw_out = 0
    reas = 0
    cost = 0.0
    steps = 0
    has_in = False
    has_out = False

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "step_finish":
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        toks = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        steps += 1

        c_val = part.get("cost")
        if isinstance(c_val, (int, float)) and not isinstance(c_val, bool):
            cost += float(c_val)

        tin = _opt_int(toks.get("input"))
        if tin is not None:
            raw_in += tin
            has_in = True

        cache = toks.get("cache") if isinstance(toks.get("cache"), dict) else {}
        cr = _opt_int(cache.get("read"))
        if cr is None:
            cr = _opt_int(toks.get("cache_read"))
        if cr is not None:
            cache_r += cr
            has_in = True

        cw = _opt_int(cache.get("write"))
        if cw is None:
            cw = _opt_int(toks.get("cache_write"))
        if cw is None:
            cw5 = _opt_int(toks.get("cache_write_5m")) or 0
            cw1 = _opt_int(toks.get("cache_write_1h")) or 0
            if cw5 or cw1:
                cw = cw5 + cw1
        if cw is not None:
            cache_w += cw
            has_in = True

        tout = _opt_int(toks.get("output"))
        if tout is not None:
            raw_out += tout
            has_out = True

        rt = _opt_int(toks.get("reasoning"))
        if rt is not None:
            reas += rt
            has_out = True

    return OpenCodeTokenUsage(
        raw_input=raw_in,
        cache_read=cache_r,
        cache_write=cache_w,
        raw_output=raw_out,
        reasoning=reas,
        provider_cost_usd=cost,
        steps=steps,
        has_input=has_in,
        has_output=has_out,
    )


def price_opencode_usage(price: Mapping[str, Any] | None, usage: OpenCodeTokenUsage) -> float:
    """Conservatively price ``billable_input`` at full ``input_per_m`` and ``billable_output`` at ``output_per_m``."""
    if price is None:
        return 0.0
    in_m = float(price["input_per_m"])
    out_m = float(price["output_per_m"])
    return round(usage.billable_input * in_m / 1e6 + usage.billable_output * out_m / 1e6, 6)


class MeteredOpenCodeTransport(Transport):
    """Step 5 OpenCode transport wrapper around frozen ``OpenCodeCliTransport``.

    Delegates command invocation, isolated XDG config (``tools: {"*": false}``), and retry
    handling entirely to frozen ``OpenCodeCliTransport`` while replacing ``RawReply.input_tokens``
    with ``tokens.input + tokens.cache.read + tokens.cache.write`` (thread-safe for parallel calls).
    """

    name = "opencode_cli"
    meter_step = METER_STEP

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        runner: Runner | None = None,
        environ: "dict[str, str] | None" = None,
        sleep: Callable[[float], None] | None = None,
        backoff_s: float = 2.0,
    ) -> None:
        super().__init__(cfg, sleep=sleep, backoff_s=backoff_s)
        self._base_runner = runner or subprocess_runner
        self._tls = threading.local()
        self._stats_lock = threading.Lock()
        self._totals: dict[str, Any] = {
            "calls": 0,
            "raw_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "billable_input": 0,
            "raw_output": 0,
            "reasoning": 0,
            "billable_output": 0,
            "provider_cost_usd": 0.0,
        }
        self._inner = OpenCodeCliTransport(
            cfg,
            runner=self._capturing_runner,
            environ=environ,
            sleep=sleep,
            backoff_s=backoff_s,
        )
        self.command = self._inner.command
        self.num_ctx = self._inner.num_ctx

    def _capturing_runner(
        self,
        argv: Sequence[str],
        stdin: str | None,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        try:
            code, out, err = self._base_runner(argv, stdin, timeout, env=env)  # type: ignore[call-arg]
        except TypeError:
            code, out, err = self._base_runner(argv, stdin, timeout)
        self._tls.last_stdout = out
        return code, out, err

    def send(
        self,
        messages: list[dict[str, str]],
        *,
        repeat: int,
        retry_reminder: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> RawReply:
        self._tls.last_stdout = ""
        reply = self._inner.send(
            messages,
            repeat=repeat,
            retry_reminder=retry_reminder,
            response_schema=response_schema,
        )
        raw_stdout = getattr(self._tls, "last_stdout", "") or ""
        usage = parse_opencode_ndjson_tokens(raw_stdout)

        billable_in = usage.billable_input if usage.has_input else reply.input_tokens
        billable_out = usage.billable_output if usage.has_output else reply.output_tokens
        estimated = reply.estimated and not (usage.has_input and usage.has_output)
        truncated = reply.context_truncated or (usage.has_input and billable_in >= self.num_ctx)

        with self._stats_lock:
            self._totals["calls"] += 1
            if usage.has_input:
                self._totals["raw_input"] += usage.raw_input
                self._totals["cache_read"] += usage.cache_read
                self._totals["cache_write"] += usage.cache_write
                self._totals["billable_input"] += usage.billable_input
            else:
                self._totals["raw_input"] += billable_in
                self._totals["billable_input"] += billable_in
            if usage.has_output:
                self._totals["raw_output"] += usage.raw_output
                self._totals["reasoning"] += usage.reasoning
                self._totals["billable_output"] += usage.billable_output
            else:
                self._totals["raw_output"] += billable_out
                self._totals["billable_output"] += billable_out
            self._totals["provider_cost_usd"] += usage.provider_cost_usd

        return RawReply(
            text=reply.text,
            input_tokens=billable_in,
            output_tokens=billable_out,
            estimated=estimated,
            retries=reply.retries,
            context_truncated=truncated,
        )

    def token_breakdown(self) -> dict[str, Any]:
        """Thread-safe snapshot of cumulative metered tokens (including cache & reasoning)."""
        with self._stats_lock:
            snap = dict(self._totals)
        snap["provider_cost_usd"] = round(float(snap["provider_cost_usd"]), 8)
        snap["meter_step"] = METER_STEP
        return snap


class MeteredAnthropicTransport(Transport):
    """Thin wrapper around frozen ``AnthropicCommandTransport`` that records cache & thinking tokens.

    Frozen ``AnthropicCommandTransport`` already adds ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens`` into ``RawReply.input_tokens``; this wrapper leaves ``RawReply``
    identical while exposing ``token_breakdown()`` (including ``thinking_tokens`` as ``reasoning``).
    """

    name = "anthropic_command"
    meter_step = METER_STEP

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        runner: Runner | None = None,
        environ: "dict[str, str] | None" = None,
        sleep: Callable[[float], None] | None = None,
        backoff_s: float = 2.0,
    ) -> None:
        super().__init__(cfg, sleep=sleep, backoff_s=backoff_s)
        self._base_runner = runner or subprocess_runner
        self._tls = threading.local()
        self._stats_lock = threading.Lock()
        self._totals: dict[str, Any] = {
            "calls": 0,
            "raw_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "billable_input": 0,
            "raw_output": 0,
            "reasoning": 0,
            "billable_output": 0,
        }
        self._inner = AnthropicCommandTransport(
            cfg,
            runner=self._capturing_runner,
            environ=environ,
            sleep=sleep,
            backoff_s=backoff_s,
        )
        self.command = self._inner.command

    def _capturing_runner(
        self,
        argv: Sequence[str],
        stdin: str | None,
        timeout: float,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if env is None:
            code, out, err = self._base_runner(argv, stdin, timeout)
        else:
            try:
                code, out, err = self._base_runner(argv, stdin, timeout, env=env)  # type: ignore[call-arg]
            except TypeError:
                code, out, err = self._base_runner(argv, stdin, timeout)
        self._tls.last_stdout = out
        return code, out, err

    def send(
        self,
        messages: list[dict[str, str]],
        *,
        repeat: int,
        retry_reminder: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> RawReply:
        self._tls.last_stdout = ""
        reply = self._inner.send(
            messages,
            repeat=repeat,
            retry_reminder=retry_reminder,
            response_schema=response_schema,
        )
        raw_stdout = getattr(self._tls, "last_stdout", "") or ""
        raw_in = reply.input_tokens
        cr = 0
        cw = 0
        out_t = reply.output_tokens
        thinking_t = 0
        try:
            msg = json.loads(raw_stdout)
            u = msg.get("usage") if isinstance(msg, dict) and isinstance(msg.get("usage"), dict) else {}
            if isinstance(u.get("input_tokens"), int):
                raw_in = u["input_tokens"]
                cr = int(u.get("cache_read_input_tokens") or 0)
                cw = int(u.get("cache_creation_input_tokens") or 0)
            if isinstance(u.get("output_tokens"), int):
                out_t = u["output_tokens"]
            details = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
            if isinstance(details.get("thinking_tokens"), int):
                thinking_t = details["thinking_tokens"]
        except ValueError:
            pass

        with self._stats_lock:
            self._totals["calls"] += 1
            self._totals["raw_input"] += raw_in
            self._totals["cache_read"] += cr
            self._totals["cache_write"] += cw
            self._totals["billable_input"] += reply.input_tokens
            self._totals["raw_output"] += max(0, out_t - thinking_t)
            self._totals["reasoning"] += thinking_t
            self._totals["billable_output"] += reply.output_tokens

        return reply

    def token_breakdown(self) -> dict[str, Any]:
        with self._stats_lock:
            snap = dict(self._totals)
        snap["meter_step"] = METER_STEP
        return snap


def make_step5_transport(
    cfg: dict[str, Any],
    *,
    runner: Runner | None = None,
    opener: Opener | None = None,
    sleep: Callable[[float], None] | None = None,
    environ: dict[str, str] | None = None,
) -> Transport:
    """Build a transport using Step 5 OpenCode metering (frozen transports.py untouched)."""
    kind = cfg.get("transport")
    if kind == "opencode_cli":
        return MeteredOpenCodeTransport(cfg, runner=runner, environ=environ, sleep=sleep)
    if kind == "anthropic_command":
        return MeteredAnthropicTransport(cfg, runner=runner, environ=environ, sleep=sleep)
    return make_transport(cfg, runner=runner, opener=opener, sleep=sleep, environ=environ)


def load_step5_decider(
    path_or_dict: str | Path | dict[str, Any],
    gateway: Gateway,
    *,
    runner: Runner | None = None,
    opener: Opener | None = None,
    sleep: Callable[[float], None] | None = None,
    environ: dict[str, str] | None = None,
) -> ModelDecider:
    """Load a ModelDecider backed by the Step 5 OpenCode meter."""
    cfg = load_config(path_or_dict)
    gateway.check_decider(cfg)
    transport = make_step5_transport(cfg, runner=runner, opener=opener, sleep=sleep, environ=environ)
    decider = ModelDecider(cfg, transport, gateway)
    decider.meter_step = METER_STEP  # type: ignore[attr-defined]
    return decider


def zen_urllib_opener(url: str, data: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """HTTP POST opener adding a standard User-Agent for OpenCode Zen endpoints (avoids Cloudflare 1010)."""
    req_headers = dict(headers)
    if not any(k.lower() == "user-agent" for k in req_headers):
        req_headers["User-Agent"] = ZEN_USER_AGENT
    return urllib_opener(url, data, req_headers, timeout)


def needs_zen_opener(memory_side_cfg: Mapping[str, Any] | None) -> bool:
    """True when either chat or embeddings upstream targets opencode.ai."""
    if not isinstance(memory_side_cfg, Mapping):
        return False
    for where in ("chat", "embeddings"):
        up = memory_side_cfg.get(where)
        if isinstance(up, Mapping):
            url = str(up.get("base_url") or "").lower()
            if "opencode.ai" in url:
                return True
    return False


__all__ = [
    "METER_STEP",
    "METER_FORMULA",
    "ZEN_USER_AGENT",
    "OpenCodeTokenUsage",
    "parse_opencode_ndjson_tokens",
    "price_opencode_usage",
    "MeteredOpenCodeTransport",
    "MeteredAnthropicTransport",
    "make_step5_transport",
    "load_step5_decider",
    "zen_urllib_opener",
    "needs_zen_opener",
]
