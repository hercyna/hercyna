"""Decider transports (docs/deciders.md section 3).

One interface: `send(messages, *, repeat, retry_reminder) -> RawReply`. `messages` are the final
[system, user] messages (the reminder, when any, is already in them; `retry_reminder` is information).

Low-level calls are injectable so tests never touch a network or a real command:
- `runner(argv, stdin, timeout) -> (returncode, stdout, stderr)` runs a command; when the config has
  `env_unset`, the anthropic_command transport also passes `env=` (the child environment) as a keyword;
- `opener(url, data, headers, timeout) -> (status, body_bytes)` sends one HTTP POST. It returns non-2xx
  statuses instead of raising; it raises TimeoutError or ConnectionError (URLError) on transport faults.

Retries with backoff on timeouts, connection faults, 429 and 5xx, at most `retries` times. Any other error
raises TransportError with the status or exit code and at most 300 characters of the response body.
A key value never appears in a log line, an exception or a return value (section 3, Keys).
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .lifted import REPLY_FORMAT
from .prompts import ACTION_SCHEMA

log = logging.getLogger("memory_bench.transport")

__all__ = ["RawReply", "TransportError", "Transport", "AnthropicCommandTransport", "OpenAICompatTransport",
           "OllamaChatTransport", "make_transport", "estimate_tokens", "TRANSPORTS"]

CHARS_PER_TOKEN = 2.2
BODY_CHARS = 300
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_RETRIES = 3
DEFAULT_NUM_CTX = 32768
ANTHROPIC_RETRY_TYPES = frozenset({"rate_limit_error", "overloaded_error", "api_error", "timeout_error"})

Runner = Callable[[Sequence[str], "str | None", float], "tuple[int, str, str]"]
Opener = Callable[[str, bytes, "dict[str, str]", float], "tuple[int, bytes]"]


@dataclass(frozen=True)
class RawReply:
    text: str
    input_tokens: int
    output_tokens: int
    estimated: bool = False  # True when the endpoint gave no usage and tokens are ceil(chars / 2.2)
    retries: int = 0  # transport-level retries this call needed


class TransportError(RuntimeError):
    """A call failed for good. The message holds a status or exit code and at most 300 body characters."""


class _Retryable(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _messages_chars(messages: list[dict[str, str]]) -> str:
    return "".join(m["content"] for m in messages)


def _snip(text: str | bytes) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return text[:BODY_CHARS]


# ---------------------------------------------------------------------------
# Default low-level callables (the only places a real command or socket is used)
# ---------------------------------------------------------------------------

def subprocess_runner(argv: Sequence[str], stdin: str | None, timeout: float,
                      env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run a command. env None inherits this process's environment."""
    try:
        proc = subprocess.run(list(argv), input=stdin, capture_output=True, text=True, timeout=timeout, check=False,
                              env=env)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"command timed out after {timeout:g} s") from None
    return proc.returncode, proc.stdout, proc.stderr


def urllib_opener(url: str, data: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return exc.code, body
    except (socket.timeout, TimeoutError):
        raise TimeoutError("request timed out") from None
    except urllib.error.URLError as exc:
        raise ConnectionError(f"connection failed: {exc.reason}") from None


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Transport:
    name = "transport"

    def __init__(self, cfg: dict[str, Any], *, sleep: Callable[[float], None] | None = None,
                 backoff_s: float = 2.0):
        self.cfg = cfg
        self.model = cfg["model"]
        self.params: dict[str, Any] = dict(cfg.get("params") or {})
        self.timeout_s = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
        self.retries = int(cfg.get("retries", DEFAULT_RETRIES))
        self.sleep = sleep or time.sleep
        self.backoff_s = backoff_s

    def _once(self, messages: list[dict[str, str]], repeat: int) -> RawReply:
        raise NotImplementedError

    def send(self, messages: list[dict[str, str]], *, repeat: int, retry_reminder: bool = False) -> RawReply:
        last = ""
        for attempt in range(self.retries + 1):
            try:
                reply = self._once(messages, repeat)
            except _Retryable as exc:
                last = exc.what
            except (TimeoutError, ConnectionError) as exc:
                last = f"{type(exc).__name__}: {_snip(str(exc))}"
            else:
                return RawReply(reply.text, reply.input_tokens, reply.output_tokens, reply.estimated, attempt)
            if attempt < self.retries:
                delay = self.backoff_s * (2 ** attempt)
                log.warning("%s %s: %s; retry %d of %d in %.1f s", self.name, self.model, last, attempt + 1,
                            self.retries, delay)
                self.sleep(delay)
        raise TransportError(f"{self.name} {self.model}: failed after {self.retries + 1} tries; last: {last}")

    def _usage(self, messages: list[dict[str, str]], text: str, tin: Any, tout: Any) -> RawReply:
        if isinstance(tin, int) and isinstance(tout, int):
            return RawReply(text, tin, tout, False)
        return RawReply(text, estimate_tokens(_messages_chars(messages)), estimate_tokens(text), True)


def _http_status(status: int, body: bytes, what: str) -> None:
    """Raise for a non-2xx status: retryable for 429 and 5xx, TransportError otherwise."""
    if 200 <= status < 300:
        return
    if status == 429 or status >= 500:
        raise _Retryable(f"HTTP {status}: {_snip(body)}")
    raise TransportError(f"{what}: HTTP {status}: {_snip(body)}")


def _json(body: bytes | str, what: str) -> dict:
    try:
        obj = json.loads(body)
    except ValueError:
        raise TransportError(f"{what}: reply is not JSON: {_snip(body)}") from None
    if not isinstance(obj, dict):
        raise TransportError(f"{what}: reply is not a JSON object: {_snip(body)}")
    return obj


# ---------------------------------------------------------------------------
# anthropic_command
# ---------------------------------------------------------------------------

class AnthropicCommandTransport(Transport):
    """`<command> POST /v1/messages --body - --timeout S`, JSON body on stdin, JSON reply on stdout.

    The wrapper holds the key; this class never handles one. Wrapper exit codes: 0 for 2xx, 1 for another
    HTTP status with the error JSON on stdout, 2 for a usage or environment error.

    `env_unset` (config, optional): names removed from the wrapper's environment, for example so a stray
    ANTHROPIC_API_KEY in the shell cannot override the wrapper's own key. This process's environment is not
    changed and the removed values are never read.
    """

    name = "anthropic_command"

    def __init__(self, cfg: dict[str, Any], *, runner: Runner | None = None,
                 environ: "os._Environ[str] | dict[str, str] | None" = None, **kw: Any):
        super().__init__(cfg, **kw)
        self.command = list(cfg.get("command") or ["anthropic-api"])
        self.runner = runner or subprocess_runner
        self.environ = environ  # None: os.environ at call time
        self.env_unset = frozenset(cfg.get("env_unset") or ())

    def child_env(self) -> dict[str, str] | None:
        """The wrapper's environment: this process's minus env_unset, or None (inherit) without env_unset."""
        if not self.env_unset:
            return None
        src = self.environ if self.environ is not None else os.environ
        return {k: src[k] for k in src if k not in self.env_unset}

    def body(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """The Messages API body, as LENS batch.decide_params built it."""
        system = next(m["content"] for m in messages if m["role"] == "system")
        user = next(m["content"] for m in messages if m["role"] == "user")
        p = self.params
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(p.get("max_tokens", 6000)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": p.get("effort", "high"),
                "format": {"type": "json_schema", "schema": json.loads(json.dumps(ACTION_SCHEMA))},
            },
        }
        if p.get("thinking") is not None:
            body["thinking"] = p["thinking"]
        if p.get("temperature") is not None:
            body["temperature"] = p["temperature"]
        return body

    def argv(self) -> list[str]:
        return [*self.command, "POST", "/v1/messages", "--body", "-", "--timeout", str(int(self.timeout_s))]

    def _once(self, messages: list[dict[str, str]], repeat: int) -> RawReply:
        data = json.dumps(self.body(messages), ensure_ascii=True)
        env = self.child_env()
        if env is None:
            code, out, err = self.runner(self.argv(), data, self.timeout_s + 30.0)
        else:
            code, out, err = self.runner(self.argv(), data, self.timeout_s + 30.0, env=env)
        what = f"{self.name} {self.model}"
        if code == 1:
            try:
                etype = (json.loads(out).get("error") or {}).get("type")
            except (ValueError, AttributeError):
                etype = None
            if etype in ANTHROPIC_RETRY_TYPES:
                raise _Retryable(f"exit 1 ({etype}): {_snip(out)}")
            raise TransportError(f"{what}: exit 1: {_snip(out)}")
        if code != 0:
            raise TransportError(f"{what}: exit {code}: {_snip(err or out)}")
        msg = _json(out, what)
        text = next((b.get("text", "") for b in msg.get("content") or [] if b.get("type") == "text"), "")
        u = msg.get("usage") or {}
        tin = u.get("input_tokens")
        if isinstance(tin, int):
            tin += int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        return self._usage(messages, text, tin, u.get("output_tokens"))


# ---------------------------------------------------------------------------
# openai_compat
# ---------------------------------------------------------------------------

class OpenAICompatTransport(Transport):
    """POST {base_url}/chat/completions. seed = repeat + 1 (one endpoint refuses seed 0). JSON schema response_format, dropped once (and for
    the rest of this transport's life) when a 400 names response_format."""

    name = "openai_compat"

    def __init__(self, cfg: dict[str, Any], *, opener: Opener | None = None, runner: Runner | None = None,
                 environ: dict[str, str] | None = None, **kw: Any):
        super().__init__(cfg, **kw)
        if not cfg.get("base_url"):
            raise ValueError(f"decider {cfg.get('id')!r}: openai_compat needs base_url")
        self.url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
        self.key_ref = cfg.get("key")
        self.opener = opener or urllib_opener
        self.runner = runner or subprocess_runner
        self.environ = environ if environ is not None else os.environ
        self.use_response_format = True

    def body(self, messages: list[dict[str, str]], repeat: int) -> dict[str, Any]:
        p = self.params
        body: dict[str, Any] = {"model": self.model, "messages": [dict(m) for m in messages], "seed": int(repeat) + 1}
        for k in ("temperature", "max_tokens"):
            if p.get(k) is not None:
                body[k] = p[k]
        if self.use_response_format:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "decision", "strict": True, "schema": json.loads(json.dumps(ACTION_SCHEMA))}}
        return body

    def _headers(self) -> dict[str, str]:
        """Request headers. The only function that holds the key value; it goes straight to the opener."""
        headers = {"Content-Type": "application/json"}
        ref = self.key_ref
        if not ref:
            return headers
        if "env" in ref:
            key = self.environ.get(ref["env"], "")
            if not key:
                raise TransportError(f"{self.name}: environment variable {ref['env']} is not set")
        else:
            try:
                code, out, _err = self.runner(list(ref["command"]), None, 60.0)
            except TimeoutError:
                raise TransportError(f"{self.name}: key command timed out") from None
            if code != 0:
                raise TransportError(f"{self.name}: key command exited {code}")
            key = out.strip()
            if not key:
                raise TransportError(f"{self.name}: key command printed nothing")
        headers["Authorization"] = f"Bearer {key}"
        return headers

    def _once(self, messages: list[dict[str, str]], repeat: int) -> RawReply:
        what = f"{self.name} {self.model}"
        status, raw = self.opener(self.url, json.dumps(self.body(messages, repeat)).encode("utf-8"),
                                  self._headers(), self.timeout_s)
        if status == 400 and self.use_response_format and b"response_format" in raw:
            log.warning("%s: endpoint refused response_format; sending without it from now on", what)
            self.use_response_format = False
            status, raw = self.opener(self.url, json.dumps(self.body(messages, repeat)).encode("utf-8"),
                                      self._headers(), self.timeout_s)
        _http_status(status, raw, what)
        obj = _json(raw, what)
        try:
            text = obj["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise TransportError(f"{what}: reply has no choices[0].message: {_snip(raw)}") from None
        u = obj.get("usage") or {}
        tin, tout, total = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
        if isinstance(tin, int) and isinstance(tout, int) and isinstance(total, int):
            # Some endpoints report reasoning tokens outside completion_tokens. They are billed as output,
            # so output is everything in the total that is not prompt.
            tout = max(tout, total - tin)
        return self._usage(messages, text, tin, tout)


# ---------------------------------------------------------------------------
# ollama
# ---------------------------------------------------------------------------

class OllamaChatTransport(Transport):
    """POST {base_url}/api/chat with the lifted OllamaTransport body: stream false, think false, format = the
    reply schema, options with temperature, seed = repeat and num_ctx (always sent).

    A separate class from lifted.backends.OllamaTransport because that one returns only the reply text
    (no prompt_eval_count / eval_count) and calls urlopen directly (no injectable opener)."""

    name = "ollama"

    def __init__(self, cfg: dict[str, Any], *, opener: Opener | None = None, **kw: Any):
        super().__init__(cfg, **kw)
        self.url = str(cfg.get("base_url") or "http://localhost:11434").rstrip("/") + "/api/chat"
        self.opener = opener or urllib_opener
        self.truncated = 0

    def body(self, messages: list[dict[str, str]], repeat: int) -> dict[str, Any]:
        p = self.params
        options: dict[str, Any] = {"temperature": p.get("temperature", 0), "seed": int(repeat),
                                   "num_ctx": int(p.get("num_ctx") or DEFAULT_NUM_CTX)}
        if p.get("max_tokens") is not None:
            options["num_predict"] = int(p["max_tokens"])
        return {"model": self.model, "messages": [dict(m) for m in messages], "stream": False, "think": False,
                "format": json.loads(json.dumps(REPLY_FORMAT)), "options": options}

    def _once(self, messages: list[dict[str, str]], repeat: int) -> RawReply:
        what = f"{self.name} {self.model}"
        body = self.body(messages, repeat)
        status, raw = self.opener(self.url, json.dumps(body).encode("utf-8"),
                                  {"Content-Type": "application/json"}, self.timeout_s)
        _http_status(status, raw, what)
        obj = _json(raw, what)
        try:
            text = obj["message"]["content"] or ""
        except (KeyError, TypeError):
            raise TransportError(f"{what}: reply has no message.content: {_snip(raw)}") from None
        tin = obj.get("prompt_eval_count")
        if isinstance(tin, int) and tin >= body["options"]["num_ctx"]:
            self.truncated += 1
            print("warning: prompt filled num_ctx, the model may have lost the start of it", file=sys.stderr)
        return self._usage(messages, text, tin, obj.get("eval_count"))


TRANSPORTS: dict[str, type[Transport]] = {
    "anthropic_command": AnthropicCommandTransport,
    "openai_compat": OpenAICompatTransport,
    "ollama": OllamaChatTransport,
}


def make_transport(cfg: dict[str, Any], *, runner: Runner | None = None, opener: Opener | None = None,
                   sleep: Callable[[float], None] | None = None, environ: dict[str, str] | None = None) -> Transport:
    kind = cfg.get("transport")
    if kind == "anthropic_command":
        return AnthropicCommandTransport(cfg, runner=runner, environ=environ, sleep=sleep)
    if kind == "openai_compat":
        return OpenAICompatTransport(cfg, opener=opener, runner=runner, environ=environ, sleep=sleep)
    if kind == "ollama":
        return OllamaChatTransport(cfg, opener=opener, sleep=sleep)
    raise ValueError(f"unknown transport {kind!r}; known: {', '.join(TRANSPORTS)}")
