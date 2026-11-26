"""v2 tests: two-ledger caps and the Step 5 OpenCode meter."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memory_bench.deciders.transports import RawReply
from memory_bench.engine import RunConfig, run
from memory_bench.engine.freeze import check_protocol
from memory_bench.engine.ledgers import (
    LEDGERS,
    ledger_for_decider,
    run_headroom_usd,
    stamp_spend,
    sum_ledger_spend,
)
from memory_bench.engine.opencode_meter import (
    METER_STEP,
    MeteredAnthropicTransport,
    MeteredOpenCodeTransport,
    load_step5_decider,
    needs_zen_opener,
    parse_opencode_ndjson_tokens,
    price_opencode_usage,
    zen_urllib_opener,
)
from memory_bench.gateway import Gateway
from memory_bench.submit.textfree import scan_file, scan_run

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def test_r5_a2_saved_qwen_cache_read_ndjson_counts_cache_as_full_input():
    """A2: test Step 5 OpenCode meter with a real saved Qwen NDJSON reply that has cache reads."""
    raw_read = (FIXTURES / "qwen_cache_read_ndjson.jsonl").read_text(encoding="utf-8")
    usage = parse_opencode_ndjson_tokens(raw_read)
    assert usage.raw_input == 6
    assert usage.cache_read == 5081
    assert usage.cache_write == 0
    assert usage.billable_input == 5087
    assert usage.raw_output == 1407
    assert usage.reasoning == 0
    assert usage.billable_output == 1407
    assert usage.steps == 1
    assert usage.provider_cost_usd == pytest.approx(0.00074349, abs=1e-9)

    qwen_price = {"input_per_m": 0.15, "output_per_m": 0.47}
    usd = price_opencode_usage(qwen_price, usage)
    # Conservative full-input-rate billing: 5087 * 0.15 / 1e6 + 1407 * 0.47 / 1e6 = 0.00076305 + 0.00066129 = 0.001424
    assert usd == pytest.approx(0.001424, abs=1e-6)
    assert usd > usage.provider_cost_usd  # conservative vs provider's cache-read discount

    # Also verify saved cache-write NDJSON reply
    raw_write = (FIXTURES / "qwen_cache_write_ndjson.jsonl").read_text(encoding="utf-8")
    u_write = parse_opencode_ndjson_tokens(raw_write)
    assert u_write.raw_input == 6
    assert u_write.cache_write == 5081
    assert u_write.billable_input == 5087
    assert u_write.billable_output == 758


def test_r5_a2_metered_opencode_transport_gateway_and_parallel_safety(tmp_path):
    """MeteredOpenCodeTransport wraps frozen OpenCodeCliTransport cleanly and is thread-safe."""
    raw_read = (FIXTURES / "qwen_cache_read_ndjson.jsonl").read_text(encoding="utf-8")
    multi_step = "\n".join([
        json.dumps({"type": "step_finish", "part": {"cost": 0.0005, "tokens": {
            "input": 1000, "output": 20, "reasoning": 30, "cache": {"read": 200, "write": 50}}}}),
        json.dumps({"type": "text", "part": {"text": '{"action": "proceed"}'}}),
        json.dumps({"type": "step_finish", "part": {"cost": 0.0007, "tokens": {
            "input": 100, "output": 10, "reasoning": 440, "cache": {"read": 1200, "write": 0}}}}),
    ])

    cfg = {
        "id": "opencode-qwen3.8-flash",
        "transport": "opencode_cli",
        "command": ["opencode"],
        "model": "opencode/qwen3.8-flash",
        "prompt_version": "v1",
        "params": {"max_tokens": 2048, "temperature": 0},
        "price": {"input_per_m": 0.15, "output_per_m": 0.47},
    }

    def fake_runner(argv, stdin, timeout, env=None):
        prompt = argv[-1]
        if "multi" in prompt:
            return 0, multi_step, ""
        return 0, raw_read, ""

    with Gateway(tmp_path / "cache.sqlite", cap_usd=1.0, run_dir=tmp_path / "run") as gw:
        dec = load_step5_decider(cfg, gw, runner=fake_runner, sleep=lambda _: None)
        assert dec.meter_step == METER_STEP
        assert isinstance(dec.transport, MeteredOpenCodeTransport)

        def call_one(i: int) -> RawReply:
            tag = "multi" if i % 2 else "single"
            return gw.call(
                cfg,
                dec.transport,
                [{"role": "system", "content": "sys"}, {"role": "user", "content": f"case {i} {tag}"}],
                repeat=0,
                context={"case_id": f"c{i}", "kind": "gate"},
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(call_one, range(4)))

        # Even indices: single Qwen cache-read reply (6 + 5081 = 5087 in, 1407 out)
        # Odd indices: multi-step reply (1000+200+50+100+1200 = 2550 in, 20+30+10+440 = 500 out)
        assert replies[0].input_tokens == 5087 and replies[0].output_tokens == 1407
        assert replies[1].input_tokens == 2550 and replies[1].output_tokens == 500
        assert replies[2].input_tokens == 5087 and replies[2].output_tokens == 1407
        assert replies[3].input_tokens == 2550 and replies[3].output_tokens == 500

        tb = dec.transport.token_breakdown()
        assert tb["calls"] == 4
        assert tb["cache_read"] == 2 * 5081 + 2 * 1400
        assert tb["cache_write"] == 2 * 50
        assert tb["reasoning"] == 2 * 470
        assert tb["billable_input"] == 2 * 5087 + 2 * 2550
        assert tb["billable_output"] == 2 * 1407 + 2 * 500

        sp = gw.spend()
        assert sp["input_tokens"] == 2 * 5087 + 2 * 2550
        assert sp["output_tokens"] == 2 * 1407 + 2 * 500
        assert sp["total_usd"] == pytest.approx(2 * 0.001424 + 2 * 0.000618, abs=1e-5)


def test_r5_a2_metered_anthropic_captures_thinking_tokens(tmp_path):
    cfg = {
        "id": "sonnet-5-5",
        "transport": "anthropic_command",
        "command": ["anthropic-api"],
        "env_unset": ["ANTHROPIC_API_KEY"],
        "model": "claude-sonnet-5-5",
        "prompt_version": "v1",
        "params": {"max_tokens": 6000, "effort": "high"},
        "price": {"input_per_m": 2.0, "output_per_m": 10.0},
    }
    reply_json = json.dumps({
        "content": [{"type": "text", "text": '{"action":"proceed"}'}],
        "usage": {
            "input_tokens": 3000,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 200,
            "output_tokens": 600,
            "output_tokens_details": {"thinking_tokens": 450},
        },
    })

    def fake_runner(argv, stdin, timeout, env=None):
        return 0, reply_json, ""

    with Gateway(tmp_path / "cache.sqlite", cap_usd=1.0, run_dir=tmp_path / "run") as gw:
        dec = load_step5_decider(cfg, gw, runner=fake_runner, sleep=lambda _: None)
        assert isinstance(dec.transport, MeteredAnthropicTransport)
        rep = dec.transport.send([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], repeat=0)
        assert rep.input_tokens == 3300
        assert rep.output_tokens == 600
        tb = dec.transport.token_breakdown()
        assert tb["reasoning"] == 450
        assert tb["cache_read"] == 200
        assert tb["cache_write"] == 100


def test_r5_a1_two_separate_ledgers_and_qwen_metering_note(tmp_path):
    assert LEDGERS["main"] == {"cap_usd": 60.0, "stop_usd": 55.0}
    assert LEDGERS["sonnet"] == {"cap_usd": 20.0, "stop_usd": 18.0}
    assert ledger_for_decider("sonnet-5-5") == "sonnet"
    assert ledger_for_decider("haiku-5-5") == "main"
    assert ledger_for_decider("opencode-deepseek-v4.1-flash") == "main"

    # Separate folders: one main with Qwen metering note, one sonnet, one ignored dryrun-pack duplicate
    (tmp_path / "validity" / "qwen").mkdir(parents=True)
    (tmp_path / "validity" / "qwen" / "spend.json").write_text(json.dumps({
        "total_usd": 0.023658,
        "metering_note": {"corrected_total_usd": 0.062155},
    }))
    (tmp_path / "validity" / "sonnet-5-5").mkdir(parents=True)
    (tmp_path / "validity" / "sonnet-5-5" / "spend.json").write_text(json.dumps(
        stamp_spend({"total_usd": 1.25, "cap_usd": 5.0}, "sonnet")
    ))
    (tmp_path / "dryrun-pack" / "dup").mkdir(parents=True)
    (tmp_path / "dryrun-pack" / "dup" / "spend.json").write_text(json.dumps({"total_usd": 99.0}))

    main_s = sum_ledger_spend(tmp_path, "main")
    sonnet_s = sum_ledger_spend(tmp_path, "sonnet")
    assert main_s["recorded_usd"] == pytest.approx(0.023658)
    assert main_s["effective_usd"] == pytest.approx(0.062155)
    assert main_s["remaining_to_stop_usd"] == pytest.approx(55.0 - 0.062155, abs=1e-6)
    assert sonnet_s["recorded_usd"] == pytest.approx(1.25)
    assert sonnet_s["effective_usd"] == pytest.approx(1.25)
    assert sonnet_s["remaining_to_stop_usd"] == pytest.approx(16.75)
    assert run_headroom_usd(tmp_path, "sonnet", max_run_usd=10.0) == pytest.approx(10.0)
    assert run_headroom_usd(tmp_path, "sonnet", max_run_usd=25.0) == pytest.approx(16.75)

    # Stamped spend.json passes textfree scanner
    sp_path = tmp_path / "spend.json"
    sp_path.write_text(json.dumps(stamp_spend({
        "cap_usd": 5.0, "total_usd": 0.1, "calls": 1, "input_tokens": 100, "output_tokens": 50,
        "by_stage": {}, "by_decider": {"sonnet-5-5": {"usd": 0.1}}, "by_adapter": {},
    }, "sonnet")))
    assert scan_file(sp_path) == []


def test_r5_zen_opener_detection_and_b1_extractor_probe_textfree():
    assert needs_zen_opener({"chat": {"base_url": "https://opencode.ai/zen/v1"}, "embeddings": {"base_url": "https://opencode.ai/zen/v1"}})
    assert not needs_zen_opener({"chat": {"base_url": "http://127.0.0.1:11535/v1"}, "embeddings": {"base_url": "http://127.0.0.1:11535/v1"}})

    # Frozen protocol/v2.json hashes remain valid (transports.py untouched)
    assert check_protocol(REPO_ROOT / "protocol" / "v2.json") == []

    # B1 & Qwen metering checks on private v2 runs (omitted in public export)
    ep_calls = REPO_ROOT / "results" / "v2" / "extractor_probe" / "chat_agent" / "opencode-deepseek-v4.1-flash" / "calls.jsonl"
    qwen_spend_path = REPO_ROOT / "results" / "v2" / "validity" / "chat_agent" / "opencode-qwen3.8-flash" / "spend.json"
    if not ep_calls.is_file() or not qwen_spend_path.is_file():
        pytest.skip("private results/v2 runs omitted from public export")
    assert scan_file(ep_calls) == []

    # Qwen spend.json carries metering_note with corrected figure
    qwen_spend = json.loads(qwen_spend_path.read_text())
    assert "metering_note" in qwen_spend
    assert 0.055 <= qwen_spend["metering_note"]["corrected_total_usd"] <= 0.070
