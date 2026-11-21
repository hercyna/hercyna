"""The product proxy (docs/product-proxy.md section 6): routes, model pin, cache, meter, cap, scopes,
retries, keys and the text-free call log. A fake upstream on 127.0.0.1; the product side talks to the proxy
over a real local socket with urllib. No network, no real key command."""

from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path

import pytest
from proxy_fakes import CHAT_MODEL, EMBED_MODEL, FakeUpstream, call, memory_side

from memory_bench.gateway import Gateway, ProductProxy, UnpricedMemorySide, load_memory_side, memory_side_spec
from memory_bench.gateway.proxy import DUMMY_KEY, MEMORY_CALLS, STREAM_MESSAGE, meter, worst_case
from memory_bench.submit.textfree import RELEASED_FILES, scan_file

ROOT = Path(__file__).resolve().parents[1]
LOG_FIELDS = {"at", "adapter", "config_label", "case_id", "phase", "route", "upstream", "model", "requested_model",
              "request_sha256", "reply_sha256", "input_tokens", "output_tokens", "usd", "cached", "estimated",
              "status", "seconds", "retries"}
CHAT_BODY = {"model": "gpt-4o-mini", "messages": [{"role": "system", "content": "You extract facts."},
                                                  {"role": "user", "content": "Alice moved to Lisbon in May."}],
             "temperature": 0.2, "max_tokens": 64, "response_format": {"type": "json_object"},
             "tools": [{"type": "function", "function": {"name": "add_memory", "parameters": {"type": "object"}}}]}


@pytest.fixture
def upstream():
    with FakeUpstream() as up:
        yield up


def make_proxy(tmp_path, up, cap=5.0, cache="cache.sqlite", **kw):
    cfg = kw.pop("cfg", None) or memory_side(up)
    p = ProductProxy(cfg, run_dir=tmp_path / "run", cache_path=tmp_path / cache, cap_usd=cap,
                     sleep=kw.pop("sleep", lambda s: None), **kw)
    p.start()
    return p


def log_lines(tmp_path):
    p = tmp_path / "run" / MEMORY_CALLS
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# -- routes and the model pin ---------------------------------------------------------------

def test_model_rewrite_requested_model_logged_body_unchanged(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "case-1")
    try:
        status, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
        assert status == 200 and out["choices"][0]["message"]["content"] == "a short summary"
        (req,) = upstream.requests
        assert req["path"] == "/v1/chat/completions"
        assert req["body"]["model"] == CHAT_MODEL
        assert {k: v for k, v in req["body"].items() if k != "model"} == {k: v for k, v in CHAT_BODY.items()
                                                                          if k != "model"}
        status, _ = call(f"{s.base_url}/embeddings", {"model": "text-embedding-3-small", "input": ["a", "b"]})
        assert status == 200 and upstream.requests[1]["body"]["model"] == EMBED_MODEL
        # a model name that is not a plain token is logged as a short hash, never as text
        call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "model": "my model, the best one"})
    finally:
        p.stop()
    a, b, c = log_lines(tmp_path)
    assert set(a) == LOG_FIELDS
    assert (a["route"], a["model"], a["requested_model"], a["upstream"]) == ("chat", CHAT_MODEL, "gpt-4o-mini",
                                                                             upstream.base_url)
    assert (b["route"], b["model"], b["requested_model"]) == ("embeddings", EMBED_MODEL, "text-embedding-3-small")
    assert c["requested_model"].startswith("sha256:") and "best" not in json.dumps(c)
    assert all(x["status"] == 200 for x in (a, b, c))


def test_stream_refused_models_and_healthz(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "case-1")
    try:
        status, out = call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "stream": True})
        assert status == 400 and out["error"]["message"] == STREAM_MESSAGE
        assert upstream.count() == 0
        status, out = call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "stream": False})
        assert status == 200 and upstream.requests[0]["body"]["stream"] is False
        status, out = call(f"{s.base_url}/models")
        assert status == 200 and out["object"] == "list"
        assert [m["id"] for m in out["data"]] == [CHAT_MODEL, EMBED_MODEL]
        assert call(f"{p.base_url}/healthz") == (200, {"ok": True})
        status, out = call(f"{s.base_url}/completions", {"prompt": "x"})
        assert status == 404 and "/completions" in out["error"]["message"]
        status, out = call(f"{p.base_url}/v1/chat/completions", CHAT_BODY)
        assert status == 404 and "/v1/chat/completions" in out["error"]["message"]
        status, out = call(f"{s.base_url}/chat/completions", raw=b"not json")
        assert status == 400
    finally:
        p.stop()
    assert len(log_lines(tmp_path)) == 1  # refusals and 404s are not forwarded calls


# -- cache --------------------------------------------------------------------------------

def test_cache_hit_makes_no_upstream_call(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "c1")
    try:
        st1, out1 = call(f"{s.base_url}/chat/completions", CHAT_BODY)
        st2, out2 = call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "model": "another-name"})  # same after pin
        assert st1 == st2 == 200 and out1 == out2 and upstream.count() == 1
        spent = p.spent_usd()
    finally:
        p.stop()
    a, b = log_lines(tmp_path)
    assert (a["cached"], b["cached"]) == (False, True) and b["usd"] == 0.0 and a["usd"] > 0
    assert a["request_sha256"] == b["request_sha256"] and a["reply_sha256"] == b["reply_sha256"]
    assert (b["input_tokens"], b["output_tokens"]) == (a["input_tokens"], a["output_tokens"])
    assert spent == a["usd"]
    # the cache is the shared sqlite file: a new proxy on the same file answers from it
    p2 = make_proxy(tmp_path, upstream, cap=0.0)  # cap 0 does not refuse a cached call
    try:
        assert call(f"{p2.scope('x', 'default', 'c2').base_url}/chat/completions", CHAT_BODY)[0] == 200
    finally:
        p2.stop()
    assert upstream.count() == 1


def test_only_2xx_is_cached(tmp_path, upstream):
    upstream.script["chat"] = [(400, {"error": {"message": "bad tool schema", "type": "invalid_request_error"}})]
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "c1")
    try:
        st, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
        assert st == 400 and out["error"]["message"] == "bad tool schema"
        assert call(f"{s.base_url}/chat/completions", CHAT_BODY)[0] == 200
    finally:
        p.stop()
    assert upstream.count() == 2
    a, b = log_lines(tmp_path)
    assert (a["status"], a["usd"], a["cached"], b["cached"]) == (400, 0.0, False, False)


# -- meter --------------------------------------------------------------------------------

def test_meter_tokens_dollars_reasoning_and_estimates(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream, cfg=memory_side(upstream, chat_price=(1.25, 2.50), embed_price=(0.02, 0)))
    s = p.scope("mem0", "default", "c1")
    try:
        upstream.usage_override["chat"] = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}
        call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "temperature": 0.1})
        # reasoning tokens reported outside completion_tokens count as output
        upstream.usage_override["chat"] = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1600}
        call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "temperature": 0.2})
        upstream.usage_override["chat"] = {}  # no usage: estimated
        call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "temperature": 0.3})
        upstream.usage_override["embeddings"] = {"total_tokens": 500}  # total only
        call(f"{s.base_url}/embeddings", {"model": "e", "input": "one text"})
        upstream.usage_override["embeddings"] = {}
        call(f"{s.base_url}/embeddings", {"model": "e", "input": ["abcdefghij", "klm"]})
        u = s.usage()
    finally:
        p.stop()
    a, b, c, d, e = log_lines(tmp_path)
    assert (a["input_tokens"], a["output_tokens"], a["estimated"]) == (1000, 100, False)
    assert a["usd"] == pytest.approx(1000 * 1.25e-6 + 100 * 2.5e-6)
    assert (b["input_tokens"], b["output_tokens"]) == (1000, 600)
    assert b["usd"] == pytest.approx(1000 * 1.25e-6 + 600 * 2.5e-6)
    chars = sum(len(m["content"]) for m in CHAT_BODY["messages"])
    assert c["estimated"] is True and c["input_tokens"] == math.ceil(chars / 2.2)
    assert c["output_tokens"] == math.ceil(len("a short summary") / 2.2)
    assert (d["input_tokens"], d["output_tokens"], d["estimated"]) == (500, 0, False)
    assert d["usd"] == pytest.approx(500 * 0.02e-6)
    assert (e["input_tokens"], e["estimated"]) == (math.ceil(13 / 2.2), True)
    assert p.spent_usd() == pytest.approx(sum(x["usd"] for x in (a, b, c, d, e)), abs=1e-9)
    assert (u.model_calls, u.embed_calls) == (3, 2)
    assert u.input_tokens == sum(x["input_tokens"] for x in (a, b, c, d, e))
    assert u.output_tokens == sum(x["output_tokens"] for x in (a, b, c, d, e))
    sp = p.spend()["mem0"]
    assert sp["calls"] == 5 and sp["embed_calls"] == 2 and sp["cached_calls"] == 0
    assert sp["usd"] == pytest.approx(p.spent_usd(), abs=1e-9)


def test_meter_and_worst_case_are_pure():
    req = {"messages": [{"role": "user", "content": "x" * 21}]}  # ceil(21 / 2.2) = 10
    assert meter("chat", req, b"not json")[2] is True
    assert meter("chat", req, json.dumps({"usage": {"prompt_tokens": 7, "total_tokens": 19}}).encode()) == (7, 12, False)
    up = {"base_url": "http://u", "model": "m", "price": {"input_per_m": 1.0, "output_per_m": 2.0},
          "assumed_max_output_tokens": 300}
    assert worst_case(up, "chat", req) == pytest.approx(10e-6 + 300 * 2e-6)
    assert worst_case(up, "chat", {**req, "max_completion_tokens": 50}) == pytest.approx(10e-6 + 50 * 2e-6)
    assert worst_case(up, "chat", {**req, "max_tokens": 20}) == pytest.approx(10e-6 + 20 * 2e-6)
    assert worst_case({**up, "assumed_max_output_tokens": None}, "chat", req) == pytest.approx(10e-6 + 4000 * 2e-6)
    assert worst_case(up, "embeddings", {"input": "x" * 21}) == pytest.approx(10e-6)
    parts = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 21}]}]}
    assert worst_case(up, "chat", parts) == worst_case(up, "chat", req)
    assert worst_case({**up, "price": None}, "chat", req) == 0.0


# -- cap ----------------------------------------------------------------------------------

def test_cap_refuses_nothing_sent_and_flag_set(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream, cap=0.0)
    s = p.scope("mem0", "default", "c1")
    try:
        st, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
        assert st == 400 and out["error"]["type"] == "bench_cap_reached"
        assert p.cap_reached and upstream.count() == 0
        # a free upstream is never refused, even at cap 0
        assert call(f"{s.base_url}/embeddings", {"model": "e", "input": "x"})[0] == 200
    finally:
        p.stop()
    assert upstream.count() == 1 and p.spent_usd() == 0.0


def test_cap_counts_spent_elsewhere(tmp_path, upstream):
    body = {**CHAT_BODY, "max_tokens": 1000}
    worst = worst_case(memory_side(upstream)["chat"], "chat", {**body, "model": CHAT_MODEL})
    elsewhere = {"usd": 0.0}
    p = make_proxy(tmp_path, upstream, cap=worst + 0.001, spent_elsewhere=lambda: elsewhere["usd"])
    s = p.scope("mem0", "default", "c1")
    try:
        elsewhere["usd"] = 0.0011  # the decider side has spent enough that this call could pass the cap
        st, out = call(f"{s.base_url}/chat/completions", body)
        assert st == 400 and out["error"]["type"] == "bench_cap_reached" and upstream.count() == 0
        assert p.cap_reached and "decider side 0.001100" in out["error"]["message"]
        p.cap_reached = False
        elsewhere["usd"] = 0.0009
        assert call(f"{s.base_url}/chat/completions", body)[0] == 200 and upstream.count() == 1
        assert not p.cap_reached and p.reserved_usd() == 0.0
    finally:
        p.stop()


def test_reservation_under_threads(tmp_path):
    """12 distinct requests at once, a cap that fits about 3 worst cases: never more than 3 in flight."""
    with FakeUpstream(delay=0.05) as up:
        body = {**CHAT_BODY, "max_tokens": 1000}
        worst = worst_case(memory_side(up)["chat"], "chat", {**body, "model": CHAT_MODEL})
        p = make_proxy(tmp_path, up, cap=3.5 * worst)
        s = p.scope("mem0", "default", "c1")
        res, barrier = [], threading.Barrier(12)

        def work(i):
            barrier.wait()
            res.append(call(f"{s.base_url}/chat/completions", {**body, "temperature": i / 10})[0])

        ths = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        try:
            for t in ths:
                t.start()
            for t in ths:
                t.join()
        finally:
            p.stop()
        assert 200 in res and 400 in res and up.peak <= 3
        assert p.reserved_usd() == 0.0 and p.spent_usd() <= p.cap_usd


def test_same_request_in_flight_is_sent_once(tmp_path):
    with FakeUpstream(delay=0.1) as up:
        p = make_proxy(tmp_path, up)
        s = p.scope("mem0", "default", "c1")
        out, barrier = [], threading.Barrier(6)

        def work():
            barrier.wait()
            out.append(call(f"{s.base_url}/chat/completions", CHAT_BODY))

        ths = [threading.Thread(target=work) for _ in range(6)]
        try:
            for t in ths:
                t.start()
            for t in ths:
                t.join()
        finally:
            p.stop()
        assert up.count() == 1 and len({json.dumps(o) for o in out}) == 1
        assert sum(not x["cached"] for x in log_lines(tmp_path)) == 1


# -- scopes -------------------------------------------------------------------------------

def test_scopes_attribute_calls_and_404_when_unknown_or_closed(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    a = p.scope("mem0", "default", "case-1")
    b = p.scope("zep", "vendor-tuned", "case/2 x")
    try:
        assert a.base_url.startswith(f"http://127.0.0.1:{p.port}/s/mem0.default.case-1-") and a.base_url.endswith("/v1")
        assert a.api_key == DUMMY_KEY and a.base_url != p.scope("mem0", "default", "case-1").base_url
        assert "/s/zep.vendor-tuned.case-2-x-" in b.base_url
        call(f"{a.base_url}/embeddings", {"model": "e", "input": ["r1", "r2"]})
        a.phase("retrieve")
        call(f"{a.base_url}/chat/completions", CHAT_BODY)
        call(f"{b.base_url}/chat/completions", {**CHAT_BODY, "temperature": 0.9})
        with pytest.raises(ValueError):
            a.phase("decide")
        st, out = call(f"{p.base_url}/s/nope-123/v1/chat/completions", CHAT_BODY)
        assert st == 404 and "/s/nope-123/v1/chat/completions" in out["error"]["message"]
        b.close()
        st, out = call(f"{b.base_url}/chat/completions", CHAT_BODY)
        assert st == 404 and b.id in out["error"]["message"]
        assert call(f"{b.base_url}/models")[0] == 404
        ua, ub = a.usage(), b.usage()
        sp = p.spend()
    finally:
        p.stop()
    x, y, z = log_lines(tmp_path)
    assert (x["adapter"], x["case_id"], x["phase"], x["route"]) == ("mem0", "case-1", "ingest", "embeddings")
    assert (y["adapter"], y["case_id"], y["phase"], y["route"]) == ("mem0", "case-1", "retrieve", "chat")
    assert (z["adapter"], z["config_label"], z["case_id"], z["phase"]) == ("zep", "vendor-tuned", "case/2 x", "ingest")
    assert (ua.model_calls, ua.embed_calls) == (1, 1) and (ub.model_calls, ub.embed_calls) == (1, 0)
    assert ua.input_tokens == x["input_tokens"] + y["input_tokens"] and ub.output_tokens == z["output_tokens"]
    assert set(sp) == {"mem0", "zep"} and sp["mem0"]["embed_calls"] == 1 and sp["zep"]["calls"] == 1
    assert upstream.count() == 3


def test_scope_needs_a_started_proxy(tmp_path, upstream):
    p = ProductProxy(memory_side(upstream), run_dir=None, cache_path=tmp_path / "c.sqlite")
    with pytest.raises(RuntimeError):
        p.scope("a", "default", "c")
    p.stop()


# -- upstream errors ----------------------------------------------------------------------

def test_retry_on_429_then_success(tmp_path, upstream, caplog):
    caplog.set_level(logging.WARNING)
    upstream.script["chat"] = [(429, {"error": {"message": "slow down", "type": "rate_limit"}})]
    delays: list[float] = []
    p = make_proxy(tmp_path, upstream, sleep=delays.append)
    s = p.scope("mem0", "default", "c1")
    try:
        st, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
    finally:
        p.stop()
    assert st == 200 and upstream.count() == 2 and delays == [2.0]
    (x,) = log_lines(tmp_path)
    assert x["retries"] == 1 and x["status"] == 200 and x["usd"] > 0
    assert any("retry 1 of 2" in r.getMessage() for r in caplog.records)


def test_final_error_passes_through(tmp_path, upstream):
    err = {"error": {"message": "upstream overloaded", "type": "server_error", "code": "overloaded"}}
    upstream.script["chat"] = [(503, err)] * 3
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "c1")
    try:
        st, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
        assert st == 503 and out == err and upstream.count() == 3  # 1 + retries 2
        upstream.script["embeddings"] = [(500, b"<html>oops</html>")] * 3
        st, out = call(f"{s.base_url}/embeddings", {"model": "e", "input": "x"})
        assert st == 500 and out["error"]["type"] == "upstream_error"
    finally:
        p.stop()
    a, b = log_lines(tmp_path)
    assert (a["status"], a["retries"], a["usd"], a["cached"]) == (503, 2, 0.0, False)
    assert (b["status"], b["retries"]) == (500, 2)


def test_unreachable_upstream_is_a_502(tmp_path, upstream):
    cfg = memory_side(upstream)
    cfg["chat"]["base_url"] = "http://127.0.0.1:9/v1"  # the discard port: connection refused
    p = make_proxy(tmp_path, upstream, cfg=cfg)
    s = p.scope("mem0", "default", "c1")
    try:
        st, out = call(f"{s.base_url}/chat/completions", CHAT_BODY)
    finally:
        p.stop()
    assert st == 502 and out["error"]["type"] == "bench_upstream_unreachable"
    assert p.reserved_usd() == 0.0 and log_lines(tmp_path)[0]["status"] == 502


# -- keys ---------------------------------------------------------------------------------

FAKE_KEY = "sk-FAKE-PROXY-KEY-91c3e5"


def test_command_key_reaches_upstream_header_and_nowhere_else(tmp_path, upstream, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    argv_seen: list = []
    state = {"fail": False}

    def runner(argv, stdin, timeout):
        argv_seen.append(list(argv))
        return (3, "", f"denied {FAKE_KEY}") if state["fail"] else (0, FAKE_KEY + "\n", "")

    cfg = memory_side(upstream, key={"command": ["fake-key-command", "--print"]})
    upstream.script["chat"] = [(429, {"error": {"message": "slow", "type": "rate_limit"}})]
    p = make_proxy(tmp_path, upstream, cfg=cfg, runner=runner)
    s = p.scope("mem0", "default", "c1")
    try:
        assert call(f"{s.base_url}/chat/completions", CHAT_BODY, api_key="product-dummy")[0] == 200
        assert call(f"{s.base_url}/embeddings", {"model": "e", "input": "x"})[0] == 200
        state["fail"] = True
        st, out = call(f"{s.base_url}/chat/completions", {**CHAT_BODY, "temperature": 0.7})
        assert st == 500 and out["error"]["type"] == "bench_key_error" and "exited 3" in out["error"]["message"]
        texts = [json.dumps(out), repr(p), repr(vars(p)), repr(s), json.dumps(p.cfg), json.dumps(p.spend())]
    finally:
        p.stop()
    chat = [r for r in upstream.requests if r["route"] == "chat"]
    assert len(chat) == 2 and all(r["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" for r in chat)
    assert argv_seen[0] == ["fake-key-command", "--print"] and len(argv_seen) == 3  # read before each attempt
    emb = [r for r in upstream.requests if r["route"] == "embeddings"]
    assert "Authorization" not in emb[0]["headers"]  # no key configured for embeddings: none sent
    assert all("product-dummy" not in json.dumps(r["headers"]) for r in upstream.requests)
    out = capsys.readouterr()
    texts += [out.out, out.err, caplog.text] + [r.getMessage() for r in caplog.records]
    for t in texts:
        assert FAKE_KEY not in t
    files = [x for x in tmp_path.rglob("*") if x.is_file()]
    assert any(x.name == MEMORY_CALLS for x in files) and any(x.suffix == ".sqlite" for x in files)
    for x in files:
        assert FAKE_KEY.encode() not in x.read_bytes(), x


def test_env_key(tmp_path, upstream):
    cfg = memory_side(upstream, key={"env": "BENCH_MEM_KEY"})
    p = make_proxy(tmp_path, upstream, cfg=cfg, environ={"BENCH_MEM_KEY": FAKE_KEY})
    s = p.scope("mem0", "default", "c1")
    try:
        assert call(f"{s.base_url}/chat/completions", CHAT_BODY)[0] == 200
    finally:
        p.stop()
    assert upstream.requests[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    p2 = make_proxy(tmp_path, upstream, cfg=cfg, environ={}, cache="other.sqlite")
    try:
        st, out = call(f"{p2.scope('m', 'default', 'c').base_url}/chat/completions", CHAT_BODY)
    finally:
        p2.stop()
    assert st == 500 and "BENCH_MEM_KEY is not set" in out["error"]["message"]


# -- text-free log and config ---------------------------------------------------------------

def test_memory_calls_log_is_text_free(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    s = p.scope("mem0", "default", "c1")
    secret = "Alice moved to Lisbon in May and her account number changed."
    try:
        call(f"{s.base_url}/embeddings", {"model": "text-embedding-3-small", "input": [secret]})
        s.phase("retrieve")
        call(f"{s.base_url}/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user",
                                                                                       "content": secret}]})
        upstream.script["chat"] = [(400, {"error": {"message": secret, "type": "invalid_request_error"}})]
        call(f"{s.base_url}/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user",
                                                                                       "content": "other"}]})
    finally:
        p.stop()
    path = tmp_path / "run" / MEMORY_CALLS
    assert MEMORY_CALLS in RELEASED_FILES
    assert len(path.read_text().splitlines()) == 3
    assert scan_file(path) == []
    assert "Lisbon" not in path.read_text() and "summary" not in path.read_text()


def test_meter_resumes_from_the_run_folder(tmp_path, upstream):
    p = make_proxy(tmp_path, upstream)
    call(f"{p.scope('m', 'default', 'c').base_url}/chat/completions", CHAT_BODY)
    p.stop()
    first = p.spent_usd()
    with open(tmp_path / "run" / MEMORY_CALLS, "a") as fh:
        fh.write('{"torn')
    p2 = ProductProxy(memory_side(upstream), run_dir=tmp_path / "run", cache_path=tmp_path / "x.sqlite")
    assert p2.spent_usd() == first > 0 and p2.spend()["m"]["calls"] == 1
    p2.stop()


def test_config_checks_and_shipped_configs(tmp_path):
    root = ROOT / "configs" / "memory-side"
    cfgs = {x.name: load_memory_side(x) for x in sorted(root.glob("*.json"))}
    assert set(cfgs) == {"grok-4-20-nr-mem.json", "local-ollama.example.json", "mistral-small-local.json"}
    assert cfgs["mistral-small-local.json"]["chat"].get("key") is None  # a local upstream needs no key
    g = cfgs["grok-4-20-nr-mem.json"]
    assert g["id"] == "grok-4-20-nr-mem" and g["chat"]["model"] == "grok-4.20-0309-non-reasoning"
    assert g["chat"]["base_url"] == "https://api.x.ai/v1" and set(g["chat"]["key"]) == {"command"}
    assert (g["chat"]["price"]["input_per_m"], g["chat"]["price"]["output_per_m"]) == (1.25, 2.50)
    assert g["chat"]["assumed_max_output_tokens"] == 4000 and (g["timeout_s"], g["retries"]) == (300, 3)
    assert g["embeddings"] == {"base_url": "http://127.0.0.1:11434/v1", "model": "nomic-embed-text", "dims": 768,
                               "price": {"input_per_m": 0, "output_per_m": 0, "as_of": "2026-11-19",
                                         "source": "local Ollama"}}
    o = cfgs["local-ollama.example.json"]
    assert o["chat"]["base_url"] == "http://127.0.0.1:11434/v1" and o["chat"]["model"] == "REPLACE-WITH-LOCAL-MODEL"
    assert "key" not in o["chat"] and o["chat"]["price"]["input_per_m"] == 0 and o["embeddings"] == g["embeddings"]
    spec = memory_side_spec(g)
    assert set(spec) == {"id", "chat_model", "embeddings_model", "config_sha256"} and len(spec["config_sha256"]) == 64

    base = memory_side(FakeUpstreamStub())
    with pytest.raises(ValueError, match="unknown keys"):
        load_memory_side({**base, "api_key": "x"})
    with pytest.raises(ValueError) as ei:
        load_memory_side({**base, "chat": {**base["chat"], "key": "sk-literal-123"}})
    assert "sk-literal-123" not in str(ei.value)
    with pytest.raises(ValueError, match="price"):
        load_memory_side({**base, "embeddings": {k: v for k, v in base["embeddings"].items() if k != "price"}})
    with pytest.raises(ValueError, match="embeddings"):
        load_memory_side({k: v for k, v in base.items() if k != "embeddings"})
    unpriced = memory_side(FakeUpstreamStub(), chat_price=None)
    with pytest.raises(UnpricedMemorySide, match="allow-unpriced"):
        ProductProxy(unpriced, run_dir=None, cache_path=tmp_path / "c.sqlite")
    ProductProxy(unpriced, run_dir=None, cache_path=tmp_path / "c.sqlite", allow_unpriced=True).stop()


class FakeUpstreamStub:
    base_url = "http://127.0.0.1:1/v1"


# -- the decider gateway's extra_spent --------------------------------------------------------

def test_gateway_extra_spent_counts_in_its_cap(tmp_path):
    from model_fakes import HAIKU_CFG, ScriptedTransport, reply

    from memory_bench.deciders.model import load_config
    from memory_bench.gateway import CapReached, worst_case_usd

    cfg = load_config(HAIKU_CFG)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    worst = worst_case_usd(cfg, msgs)
    t = ScriptedTransport([reply("EXPLAIN")], usage=(10, 10))
    with Gateway(tmp_path / "c.sqlite", worst * 2) as g:
        assert g.extra_spent() == 0.0  # default off
        g.extra_spent = lambda: worst * 1.5
        with pytest.raises(CapReached, match="memory side"):
            g.call(cfg, t, msgs, repeat=0, context={})
        assert t.sent == []
        g.extra_spent = lambda: worst * 0.5
        g.call(cfg, t, msgs, repeat=0, context={})
    assert len(t.sent) == 1
    with Gateway(tmp_path / "d.sqlite", 1.0, extra_spent=lambda: 2.0) as g2:
        with pytest.raises(CapReached):
            g2.call(cfg, t, msgs, repeat=5, context={})
