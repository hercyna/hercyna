"""Transports against fake runners and openers. No network, no real command."""

from __future__ import annotations

import json

import pytest

from memory_bench.cases.schema import ACTIONS
from memory_bench.deciders.base import DecisionInput
from memory_bench.deciders.prompts import build_prompt
from memory_bench.deciders.transports import (
    AnthropicCommandTransport, OllamaChatTransport, OpenAICompatTransport, TransportError, estimate_tokens,
)

MSGS = build_prompt(DecisionInput(case_id="c1", query_text="Why is it higher?", records_text="e0001 2025-01-01 x"))
GOOD = json.dumps({"action": "EXPLAIN", "reason": "x.", "event_ids": []})
NOSLEEP = {"sleep": lambda s: None}


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append((list(argv), stdin, timeout))
        r = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(r, BaseException):
            raise r
        return r


class FakeOpener:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(data), "headers": dict(headers), "timeout": timeout})
        r = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(r, BaseException):
            raise r
        status, body = r
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def anthropic_reply(text=GOOD, usage=None):
    return {"type": "message", "content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}],
            "stop_reason": "end_turn", "usage": usage if usage is not None else {"input_tokens": 1200, "output_tokens": 80}}


HAIKU = {"id": "haiku-5-5", "transport": "anthropic_command", "model": "claude-haiku-5-5",
         "params": {"max_tokens": 6000, "effort": "high"}, "command": ["fake-wrapper"], "timeout_s": 300, "retries": 3}


# -- anthropic_command -------------------------------------------------------

def test_anthropic_body_and_argv():
    run = FakeRunner([(0, json.dumps(anthropic_reply()), "")])
    t = AnthropicCommandTransport(HAIKU, runner=run, **NOSLEEP)
    r = t.send(MSGS, repeat=2)
    assert (r.text, r.input_tokens, r.output_tokens, r.estimated, r.retries) == (GOOD, 1200, 80, False, 0)
    argv, stdin, _ = run.calls[0]
    assert argv == ["fake-wrapper", "POST", "/v1/messages", "--body", "-", "--timeout", "300"]
    body = json.loads(stdin)
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert "thinking" not in body and "seed" not in body
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 6000
    assert body["system"] == MSGS[0]["content"]
    assert body["messages"] == [{"role": "user", "content": MSGS[1]["content"]}]
    assert body["output_config"]["effort"] == "high"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["action"]["enum"] == list(ACTIONS)
    # a repeat is a fresh call with the same body: no seed
    t.send(MSGS, repeat=3)
    assert run.calls[1][1] == stdin


def test_anthropic_usage_adds_prompt_cache_tokens_and_estimates_when_missing():
    u = {"input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1, "output_tokens": 7}
    t = AnthropicCommandTransport(HAIKU, runner=FakeRunner([(0, json.dumps(anthropic_reply(usage=u)), "")]))
    assert (t.send(MSGS, repeat=0).input_tokens, t.send(MSGS, repeat=0).output_tokens) == (16, 7)
    t = AnthropicCommandTransport(HAIKU, runner=FakeRunner([(0, json.dumps(anthropic_reply(usage={})), "")]))
    r = t.send(MSGS, repeat=0)
    assert r.estimated and r.output_tokens == estimate_tokens(GOOD)
    assert r.input_tokens == estimate_tokens(MSGS[0]["content"] + MSGS[1]["content"])


def test_anthropic_retries_rate_limit_and_timeout_then_succeeds():
    err = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
    sleeps = []
    run = FakeRunner([(1, err, ""), TimeoutError("t"), (0, json.dumps(anthropic_reply()), "")])
    r = AnthropicCommandTransport(HAIKU, runner=run, sleep=sleeps.append).send(MSGS, repeat=0)
    assert r.text == GOOD and r.retries == 2 and len(run.calls) == 3
    assert sleeps == [2.0, 4.0]


def test_anthropic_non_retryable_errors():
    err = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "x" * 1000}})
    run = FakeRunner([(1, err, "")])
    with pytest.raises(TransportError) as ei:
        AnthropicCommandTransport(HAIKU, runner=run, **NOSLEEP).send(MSGS, repeat=0)
    assert "exit 1" in str(ei.value) and len(run.calls) == 1
    assert len(str(ei.value)) < 300 + 100
    with pytest.raises(TransportError, match="exit 2"):
        AnthropicCommandTransport(HAIKU, runner=FakeRunner([(2, "", "usage error")]), **NOSLEEP).send(MSGS, repeat=0)


def test_anthropic_bounded_retries():
    err = json.dumps({"type": "error", "error": {"type": "overloaded_error"}})
    run = FakeRunner([(1, err, "")])
    with pytest.raises(TransportError, match="failed after 4 tries"):
        AnthropicCommandTransport(HAIKU, runner=run, **NOSLEEP).send(MSGS, repeat=0)
    assert len(run.calls) == 4


# -- openai_compat -----------------------------------------------------------

OAI = {"id": "x", "transport": "openai_compat", "model": "m-1", "base_url": "http://endpoint.test/v1/",
       "params": {"temperature": 0, "max_tokens": 500}, "retries": 2}


def oai_reply(text=GOOD, usage=True):
    out = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage:
        out["usage"] = {"prompt_tokens": 900, "completion_tokens": 40}
    return out


def test_openai_body_seed_and_usage():
    op = FakeOpener([(200, oai_reply())])
    t = OpenAICompatTransport(OAI, opener=op, **NOSLEEP)
    r = t.send(MSGS, repeat=3)
    assert (r.text, r.input_tokens, r.output_tokens, r.estimated) == (GOOD, 900, 40, False)
    c = op.calls[0]
    assert c["url"] == "http://endpoint.test/v1/chat/completions"
    assert c["body"]["seed"] == 4 and c["body"]["temperature"] == 0 and c["body"]["max_tokens"] == 500
    assert c["body"]["messages"] == MSGS
    rf = c["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"]["properties"]["action"]["enum"] == list(ACTIONS)
    assert "Authorization" not in c["headers"]
    t.send(MSGS, repeat=0)
    assert op.calls[1]["body"]["seed"] == 1  # the gate run (repeat 0) must not send seed 0


def test_openai_response_format_fallback_once():
    op = FakeOpener([(400, b'{"error": {"message": "response_format json_schema is not supported"}}'),
                     (200, oai_reply(usage=False)), (200, oai_reply())])
    t = OpenAICompatTransport(OAI, opener=op, **NOSLEEP)
    r = t.send(MSGS, repeat=1)
    assert r.text == GOOD and r.estimated
    assert "response_format" in op.calls[0]["body"] and "response_format" not in op.calls[1]["body"]
    t.send(MSGS, repeat=2)
    assert "response_format" not in op.calls[2]["body"] and len(op.calls) == 3


def test_openai_other_400_is_an_error():
    op = FakeOpener([(400, b'{"error": "bad model"}')])
    with pytest.raises(TransportError, match="HTTP 400"):
        OpenAICompatTransport(OAI, opener=op, **NOSLEEP).send(MSGS, repeat=0)
    assert len(op.calls) == 1


def test_openai_429_then_success_and_bounded_5xx():
    op = FakeOpener([(429, b"busy"), (200, oai_reply())])
    r = OpenAICompatTransport(OAI, opener=op, **NOSLEEP).send(MSGS, repeat=0)
    assert r.text == GOOD and r.retries == 1 and len(op.calls) == 2
    op = FakeOpener([(503, b"down " * 200)])
    with pytest.raises(TransportError) as ei:
        OpenAICompatTransport(OAI, opener=op, **NOSLEEP).send(MSGS, repeat=0)
    assert len(op.calls) == 3 and "HTTP 503" in str(ei.value) and len(str(ei.value)) < 420
    op = FakeOpener([ConnectionError("refused"), TimeoutError("slow"), (200, oai_reply())])
    assert OpenAICompatTransport(OAI, opener=op, **NOSLEEP).send(MSGS, repeat=0).retries == 2


def test_openai_env_key():
    cfg = {**OAI, "key": {"env": "BENCH_TEST_KEY"}}
    op = FakeOpener([(200, oai_reply())])
    OpenAICompatTransport(cfg, opener=op, environ={"BENCH_TEST_KEY": "envkey-abc"}, **NOSLEEP).send(MSGS, repeat=0)
    assert op.calls[0]["headers"]["Authorization"] == "Bearer envkey-abc"
    with pytest.raises(TransportError, match="BENCH_TEST_KEY is not set"):
        OpenAICompatTransport(cfg, opener=op, environ={}, **NOSLEEP).send(MSGS, repeat=0)


def test_openai_key_command_failure_hides_output():
    cfg = {**OAI, "key": {"command": ["fake-key"]}}
    run = FakeRunner([(1, "partial-secret-xyz", "err")])
    with pytest.raises(TransportError) as ei:
        OpenAICompatTransport(cfg, opener=FakeOpener([(200, oai_reply())]), runner=run, **NOSLEEP).send(MSGS, repeat=0)
    assert "exited 1" in str(ei.value) and "partial-secret-xyz" not in str(ei.value)


# -- ollama ------------------------------------------------------------------

OLL = {"id": "g", "transport": "ollama", "model": "gpt-oss:20b", "base_url": "http://localhost:11434",
       "params": {"temperature": 0, "num_ctx": 32768}, "retries": 1}


def test_ollama_body_seed_num_ctx_usage():
    op = FakeOpener([(200, {"message": {"role": "assistant", "content": GOOD}, "prompt_eval_count": 700,
                            "eval_count": 30})])
    r = OllamaChatTransport(OLL, opener=op, **NOSLEEP).send(MSGS, repeat=2)
    assert (r.text, r.input_tokens, r.output_tokens, r.estimated) == (GOOD, 700, 30, False)
    b = op.calls[0]["body"]
    assert op.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert b["stream"] is False and b["think"] is False
    assert b["format"]["properties"]["action"]["enum"] == list(ACTIONS)
    assert b["options"] == {"temperature": 0, "seed": 2, "num_ctx": 32768}


def test_ollama_num_ctx_always_sent_and_estimates():
    cfg = {**OLL, "params": {"temperature": 0}}
    op = FakeOpener([(200, {"message": {"content": GOOD}})])
    r = OllamaChatTransport(cfg, opener=op, **NOSLEEP).send(MSGS, repeat=0)
    assert op.calls[0]["body"]["options"]["num_ctx"] > 0 and op.calls[0]["body"]["options"]["seed"] == 0
    assert r.estimated


def test_ollama_bounded_retries():
    op = FakeOpener([(500, b"oops")])
    with pytest.raises(TransportError, match="failed after 2 tries"):
        OllamaChatTransport(OLL, opener=op, **NOSLEEP).send(MSGS, repeat=0)
    assert len(op.calls) == 2


def test_openai_reasoning_tokens_count_as_output():
    # reasoning reported outside completion_tokens: output is total minus prompt
    apart = oai_reply()
    apart["usage"] = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 1740,
                      "completion_tokens_details": {"reasoning_tokens": 800}}
    # reasoning already inside completion_tokens: nothing is added twice
    inside = oai_reply()
    inside["usage"] = {"prompt_tokens": 900, "completion_tokens": 840, "total_tokens": 1740}
    op = FakeOpener([(200, apart), (200, inside)])
    t = OpenAICompatTransport(OAI, opener=op, **NOSLEEP)
    assert [t.send(MSGS, repeat=r).output_tokens for r in (0, 1)] == [840, 840]
