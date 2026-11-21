"""Gateway cache, cap, meter and call log; ModelDecider parse and reminder rules."""

from __future__ import annotations

import json

import pytest
from model_fakes import HAIKU_CFG, ScriptedTransport, reply

from memory_bench.deciders.base import PARSE_ERROR, DecisionInput
from memory_bench.deciders.lifted import REMINDER
from memory_bench.deciders.model import ModelDecider, decider_spec, load_config, load_decider
from memory_bench.deciders.prompts import build_prompt
from memory_bench.gateway import CapReached, Gateway, UnpricedDecider, worst_case_usd

CFG = load_config(HAIKU_CFG)
MSGS = build_prompt(DecisionInput(case_id="c1", query_text="Why?", records_text="e0001 a line"))
CTX = {"case_id": "c1", "kind": "gate", "system": "full", "config_label": "default", "budget": None}


def gw(tmp_path, cap=1.0, **kw):
    return Gateway(tmp_path / "cache" / "calls.sqlite", cap, run_dir=tmp_path / "run", **kw)


def lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()]


def test_second_identical_call_is_a_cache_hit(tmp_path):
    t = ScriptedTransport([reply("EXPLAIN")], usage=(1000, 100))
    with gw(tmp_path) as g:
        a = g.call(CFG, t, MSGS, repeat=1, context=CTX)
        usd = g.spend()["total_usd"]
        b = g.call(CFG, t, MSGS, repeat=1, context=CTX)
        s = g.spend()
    assert len(t.sent) == 1 and a.text == b.text
    assert (b.input_tokens, b.output_tokens) == (1000, 100)  # usage comes back from the cache
    assert usd == pytest.approx(1000 * 0.1e-6 + 100 * 0.5e-6) and s["total_usd"] == usd
    assert s["calls"] == 2 and s["cached_calls"] == 1 and s["input_tokens"] == 1000
    assert s["cached_input_tokens"] == 1000
    ls = lines(tmp_path)
    assert [x["cached"] for x in ls] == [False, True] and ls[1]["usd"] == 0.0
    assert ls[0]["request_sha256"] == ls[1]["request_sha256"]


def test_cache_survives_a_new_gateway_and_repeat_is_a_miss(tmp_path):
    t = ScriptedTransport([reply("EXPLAIN")])
    with gw(tmp_path) as g:
        g.call(CFG, t, MSGS, repeat=1, context=CTX)
    with Gateway(tmp_path / "cache" / "calls.sqlite", 1.0) as g2:
        g2.call(CFG, t, MSGS, repeat=1, context=CTX)
        assert len(t.sent) == 1
        g2.call(CFG, t, MSGS, repeat=2, context=CTX)
        assert len(t.sent) == 2 and t.sent[1][1] == 2


def test_cap_refuses_the_call_that_would_cross_it(tmp_path):
    worst = worst_case_usd(CFG, MSGS)
    assert worst > 0.003  # 6000 max_tokens at 0.50 per million
    t = ScriptedTransport([reply("EXPLAIN")], usage=(1000, 1000))
    real = 1000 * 0.1e-6 + 1000 * 0.5e-6
    with gw(tmp_path, cap=real + worst - 1e-7) as g:
        g.call(CFG, t, MSGS, repeat=1, context=CTX)  # 0 + worst <= cap
        with pytest.raises(CapReached):
            g.call(CFG, t, MSGS, repeat=2, context=CTX)  # real + worst > cap
        assert len(t.sent) == 1
        g.call(CFG, t, MSGS, repeat=1, context=CTX)  # a cached call is never refused
        assert g.spend()["total_usd"] == pytest.approx(real)


def test_cap_zero_refuses_priced_live_calls_but_not_free_ones(tmp_path):
    t = ScriptedTransport([reply("EXPLAIN")])
    with gw(tmp_path, cap=0.0) as g:
        with pytest.raises(CapReached):
            g.call(CFG, t, MSGS, repeat=0, context=CTX)
        free = {**CFG, "id": "local", "price": {"input_per_m": 0, "output_per_m": 0}}
        g.call(free, t, MSGS, repeat=0, context=CTX)
    assert len(t.sent) == 1


def test_unpriced_needs_the_flag(tmp_path):
    cfg = {**CFG, "price": None}
    t = ScriptedTransport([reply("EXPLAIN")])
    with gw(tmp_path) as g:
        with pytest.raises(UnpricedDecider):
            g.call(cfg, t, MSGS, repeat=0, context=CTX)
        with pytest.raises(UnpricedDecider):
            load_decider(cfg, g)
    with gw(tmp_path, cap=0.0, allow_unpriced=True) as g:
        g.call(cfg, t, MSGS, repeat=0, context=CTX)
        s = g.spend()
    assert s["total_usd"] == 0 and s["unpriced_calls"] == 1


def test_estimated_usage_is_marked(tmp_path):
    t = ScriptedTransport([reply("EXPLAIN")], usage=(50, 5), estimated=True)
    with gw(tmp_path) as g:
        g.call(CFG, t, MSGS, repeat=0, context=CTX)
        g.call(CFG, t, MSGS, repeat=0, context=CTX)  # the cache keeps the flag
        assert g.spend()["estimated_calls"] == 2
    assert [x["estimated"] for x in lines(tmp_path)] == [True, True]


def test_call_log_fields_and_no_text(tmp_path):
    t = ScriptedTransport([reply("CHECK_DEVICE")])
    with gw(tmp_path) as g:
        g.call(CFG, t, MSGS, repeat=0, context=CTX)
    (x,) = lines(tmp_path)
    for k in ("at", "decider", "case_id", "kind", "repeat", "request_sha256", "reply_sha256", "input_tokens",
              "output_tokens", "usd", "cached", "estimated", "seconds", "retries"):
        assert k in x
    raw = (tmp_path / "run" / "calls.jsonl").read_text()
    assert "CHECK_DEVICE" not in raw and "a line" not in raw


def test_resumed_gateway_counts_earlier_spend(tmp_path):
    t = ScriptedTransport([reply("EXPLAIN")], usage=(1000, 1000))
    with gw(tmp_path) as g:
        g.call(CFG, t, MSGS, repeat=1, context=CTX)
        first = g.spend()["total_usd"]
    with open(tmp_path / "run" / "calls.jsonl", "a") as fh:
        fh.write('{"torn')  # a torn last line is cut on open
    with gw(tmp_path) as g:
        assert g.spent_usd() == first and g.spend()["calls"] == 1


# -- ModelDecider ------------------------------------------------------------

def _decider(tmp_path, texts):
    g = gw(tmp_path)
    t = ScriptedTransport(texts)
    return ModelDecider(CFG, t, g), t, g


INP = DecisionInput(case_id="c9", query_text="Why?", records_text="e0001 x", repeat=2)


def test_model_decider_valid_reply(tmp_path):
    d, t, g = _decider(tmp_path, [reply("APPLY_CREDIT")])
    assert d.decide(INP, context={"kind": "view", "system": "bm25"}) == "APPLY_CREDIT"
    assert len(t.sent) == 1 and t.sent[0][1] == 2 and t.sent[0][2] is False
    assert t.sent[0][0] == build_prompt(INP)
    (x,) = lines(tmp_path)
    assert (x["case_id"], x["kind"], x["system"], x["repeat"]) == ("c9", "view", "bm25", 2)
    g.close()


def test_model_decider_reminder_then_valid(tmp_path):
    d, t, g = _decider(tmp_path, ["no json here", reply("open_review")])
    assert d.decide(INP) == "OPEN_REVIEW"
    assert len(t.sent) == 2 and t.sent[1][2] is True
    assert t.sent[1][0][-1]["content"].endswith(REMINDER) and not t.sent[0][0][-1]["content"].endswith(REMINDER)
    assert [x["reminder"] for x in lines(tmp_path)] == [False, True]
    g.close()


def test_model_decider_two_bad_replies_is_parse_error(tmp_path):
    d, t, g = _decider(tmp_path, ["nope", '{"action": "PAY"}'])
    assert d.decide(INP) == PARSE_ERROR and len(t.sent) == 2
    g.close()


def test_config_checks_and_spec(tmp_path):
    with pytest.raises(ValueError, match="unknown keys"):
        load_config({**HAIKU_CFG, "api_key": "x"})
    with pytest.raises(ValueError) as ei:
        load_config({**HAIKU_CFG, "transport": "openai_compat", "base_url": "http://x", "key": "sk-literal-123"})
    assert "sk-literal-123" not in str(ei.value)
    with pytest.raises(ValueError, match="price"):
        load_config({k: v for k, v in HAIKU_CFG.items() if k != "price"})
    with gw(tmp_path) as g:
        d = load_decider(HAIKU_CFG, g)
        spec = decider_spec(d)
    assert spec["id"] == "haiku-5-5" and spec["model"] == "claude-haiku-5-5"
    assert spec["params"] == {"max_tokens": 6000, "effort": "high"} and spec["transport"] == "anthropic_command"


def test_shipped_configs_load(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "configs" / "deciders"
    cfgs = {p.name: load_config(p) for p in sorted(root.glob("*.json"))}
    assert set(cfgs) == {"haiku-5-5.json", "gpt-oss-20b-local.json", "openai-compat.example.json",
                         "grok-4-20-nr.json", "grok-4-20-r.json", "haiku-5-5-p2.json", "grok-4-20-r-p2.json"}
    for name, model in (("grok-4-20-nr.json", "grok-4.20-0309-non-reasoning"),
                        ("grok-4-20-r.json", "grok-4.20-0309-reasoning")):
        g = cfgs[name]
        assert g["transport"] == "openai_compat" and g["model"] == model and g["base_url"] == "https://api.x.ai/v1"
        assert set(g["key"]) == {"command"}  # read by the transport at call time; never an inline value
        assert (g["price"]["input_per_m"], g["price"]["output_per_m"]) == (1.25, 2.50)
    h = cfgs["haiku-5-5.json"]
    assert h["params"] == {"max_tokens": 6000, "effort": "high"} and "thinking" not in h["params"]
    assert (h["price"]["input_per_m"], h["price"]["output_per_m"]) == (0.10, 0.50)
    o = cfgs["gpt-oss-20b-local.json"]
    assert o["transport"] == "ollama" and o["params"] == {"temperature": 0, "num_ctx": 32768}
    assert o["price"]["input_per_m"] == 0 and o["price"]["output_per_m"] == 0
    assert cfgs["openai-compat.example.json"]["key"] == {"env": "BENCH_API_KEY"}
