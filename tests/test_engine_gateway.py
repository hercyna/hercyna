"""The run engine with a ModelDecider behind the gateway: spend, cap stop, resume, key safety, CLI."""

from __future__ import annotations

import json
import logging
import sys

import pytest
from model_fakes import HAIKU_CFG, anthropic_runner, reply

from memory_bench.cases.sample import dev_cases
from memory_bench.cli import main
from memory_bench.deciders.model import load_decider
from memory_bench.deciders.transports import TransportError
from memory_bench.engine import RunConfig, run
from memory_bench.gateway import Gateway

N, R = 5, 2


@pytest.fixture(scope="module")
def cases():
    return dev_cases()[:N]


def make_run(tmp_path, name, cases, cap, cache="cache.sqlite", runner_log=None):
    g = Gateway(tmp_path / cache, cap)
    d = load_decider(HAIKU_CFG, g, runner=anthropic_runner(runner_log), sleep=lambda s: None)
    try:
        m = run(RunConfig(out_dir=tmp_path / name, cases=cases, decider=d, adapters=["bm25", "recent"],
                          budgets=[1000], repeats=R, boot_reps=50))
    finally:
        g.close()
    return m


def read_lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_spend_equals_call_log(tmp_path, cases):
    log: list = []
    m = make_run(tmp_path, "run", cases, cap=5.0, runner_log=log)
    d = tmp_path / "run"
    assert m["state"] == "complete" and m["cap_usd"] == 5.0
    assert m["deciders"][0]["id"] == "haiku-5-5" and m["deciders"][0]["transport"] == "anthropic_command"
    assert "calls.jsonl" in m["files"]
    spend = json.loads((d / "spend.json").read_text())
    calls = read_lines(d / "calls.jsonl")
    live = [c for c in calls if not c["cached"]]
    assert spend["total_usd"] > 0
    assert spend["total_usd"] == pytest.approx(sum(c["usd"] for c in calls), abs=1e-9)
    assert spend["calls"] == len(calls) == len(log)
    assert spend["input_tokens"] == sum(c["input_tokens"] for c in live)
    assert spend["output_tokens"] == sum(c["output_tokens"] for c in live)
    rows = read_lines(d / "metrics.jsonl")
    reminders = sum(c["reminder"] for c in calls)
    assert reminders > 0 and len(calls) == len(rows) + reminders
    by = spend["by_stage"]
    assert by["gate"]["calls"] == sum(c["kind"] == "gate" for c in calls) >= N
    assert sum(b["usd"] for b in by.values()) == pytest.approx(spend["total_usd"], abs=1e-9)
    assert set(spend["by_adapter"]) == {"full", "bm25", "recent"}
    assert spend["by_adapter"]["full"]["calls"] == sum(c["system"] == "full" for c in calls)
    assert spend["by_decider"]["haiku-5-5"]["usd"] == pytest.approx(spend["total_usd"], abs=1e-9)
    # metrics.jsonl and summary.json stay free of text and timestamps
    for name in ("metrics.jsonl", "summary.json"):
        raw = (d / name).read_text()
        assert "reason" not in raw and "Records:" not in raw
        assert not any(k in raw for k in ('"at"', '"created"', '"finished"', '"seconds"'))


def test_cap_stop_then_resume_matches_uncapped(tmp_path, cases):
    full = make_run(tmp_path, "uncapped", cases, cap=5.0, cache="a.sqlite")
    total = json.loads((tmp_path / "uncapped" / "spend.json").read_text())["total_usd"]

    m1 = make_run(tmp_path, "capped", cases, cap=total / 3, cache="b.sqlite")
    d = tmp_path / "capped"
    assert m1["state"] == "stopped_at_cap" and m1["incomplete_cells"] and "CapReached" in m1["error"]
    s1 = json.loads((d / "spend.json").read_text())
    assert 0 < s1["total_usd"] <= total / 3
    assert s1["total_usd"] == pytest.approx(sum(c["usd"] for c in read_lines(d / "calls.jsonl")), abs=1e-9)

    m_same = make_run(tmp_path, "capped", cases, cap=total / 3, cache="b.sqlite")  # same cap: still stopped
    assert m_same["state"] == "stopped_at_cap"

    m2 = make_run(tmp_path, "capped", cases, cap=5.0, cache="b.sqlite")
    assert m2["state"] == "complete" and m2["attempts"] == 3 and not m2["incomplete_cells"]
    assert (d / "metrics.jsonl").read_bytes() == (tmp_path / "uncapped" / "metrics.jsonl").read_bytes()
    assert (d / "summary.json").read_bytes() == (tmp_path / "uncapped" / "summary.json").read_bytes()
    s2 = json.loads((d / "spend.json").read_text())
    assert s2["total_usd"] == pytest.approx(total, abs=1e-6)  # nothing was paid for twice
    assert s2["total_usd"] == pytest.approx(sum(c["usd"] for c in read_lines(d / "calls.jsonl")), abs=1e-9)
    assert full["state"] == "complete"


def test_transport_failure_fails_the_run_and_keeps_spend(tmp_path, cases):
    g = Gateway(tmp_path / "c.sqlite", 5.0)
    good = anthropic_runner()
    n = {"calls": 0}

    def runner(argv, stdin, timeout):
        n["calls"] += 1
        return good(argv, stdin, timeout) if n["calls"] <= 4 else (2, "", "usage error")

    d = load_decider(HAIKU_CFG, g, runner=runner, sleep=lambda s: None)
    with pytest.raises(TransportError):
        run(RunConfig(out_dir=tmp_path / "f", cases=cases, decider=d, repeats=R, boot_reps=50))
    g.close()
    m = json.loads((tmp_path / "f" / "manifest.json").read_text())
    assert m["state"] == "failed"
    assert json.loads((tmp_path / "f" / "spend.json").read_text())["calls"] == 4


# -- key safety ----------------------------------------------------------------

FAKE_KEY = "sk-FAKE-KEY-7f3a9c1e5b2d"


def test_command_key_reaches_header_and_nowhere_else(tmp_path, cases, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    cfg = {"id": "compat", "transport": "openai_compat", "model": "m-1", "base_url": "http://endpoint.test/v1",
           "key": {"command": ["fake-key-command", "--print"]}, "params": {"temperature": 0, "max_tokens": 400},
           "price": {"input_per_m": 1.0, "output_per_m": 2.0, "as_of": "2026-11-19", "source": "test"},
           "retries": 2}
    headers_seen: list[dict] = []
    state = {"n": 0, "fail_auth": False}

    def runner(argv, stdin, timeout):
        assert argv == ["fake-key-command", "--print"]
        return 0, FAKE_KEY + "\n", ""

    def opener(url, data, headers, timeout):
        headers_seen.append(dict(headers))
        state["n"] += 1
        if state["fail_auth"]:
            return 401, b'{"error": "invalid api key"}'
        if state["n"] == 2:
            return 429, b"rate limited"  # one retry, so a log line is written
        body = json.loads(data)
        user = body["messages"][-1]["content"]
        text = reply(["EXPLAIN", "REQUEST_INFO"][len(user) % 2])
        return 200, json.dumps({"choices": [{"message": {"content": text}}],
                                "usage": {"prompt_tokens": 300, "completion_tokens": 20}}).encode()

    g = Gateway(tmp_path / "cache.sqlite", 5.0)
    d = load_decider(cfg, g, runner=runner, opener=opener, sleep=lambda s: None)
    m = run(RunConfig(out_dir=tmp_path / "run", cases=cases[:2], decider=d, repeats=1, boot_reps=20))
    assert m["state"] == "complete"
    assert headers_seen and all(h["Authorization"] == f"Bearer {FAKE_KEY}" for h in headers_seen)
    assert any("retry" in r.getMessage() for r in caplog.records)

    # an auth failure: the exception and the failed manifest must not carry the key either
    state["fail_auth"] = True
    with pytest.raises(TransportError) as ei:
        run(RunConfig(out_dir=tmp_path / "run2", cases=cases[2:3], decider=d, repeats=1, boot_reps=20))
    g.close()
    assert "HTTP 401" in str(ei.value)
    texts = [str(ei.value), repr(ei.value), repr(d), repr(vars(d.transport)), json.dumps(d.cfg)]
    texts += [r.getMessage() for r in caplog.records] + [caplog.text]
    out = capsys.readouterr()
    texts += [out.out, out.err]
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert any(p.name == "calls.jsonl" for p in files) and any(p.name == "spend.json" for p in files)
    assert any(p.name == "manifest.json" for p in files) and any(p.suffix == ".sqlite" for p in files)
    for p in files:
        assert FAKE_KEY.encode() not in p.read_bytes(), p
    for t in texts:
        assert FAKE_KEY not in t


# -- CLI -----------------------------------------------------------------------

FAKE_WRAPPER = r'''
import json, sys
argv = sys.argv[1:]
assert argv[:5] == ["POST", "/v1/messages", "--body", "-", "--timeout"], argv
body = json.loads(sys.stdin.read())
user = body["messages"][0]["content"]
action = ["EXPLAIN", "OPEN_REVIEW", "REQUEST_INFO"][len(user) % 3]
print(json.dumps({"content": [{"type": "text", "text": json.dumps({"action": action, "reason": "r", "event_ids": []})}],
                  "usage": {"input_tokens": len(user) // 2, "output_tokens": 90}}))
'''


def test_cli_decider_cap_and_resume(tmp_path, capsys):
    script = tmp_path / "fake_wrapper.py"
    script.write_text(FAKE_WRAPPER)
    cfg = {**HAIKU_CFG, "command": [sys.executable, "-I", str(script)]}
    cfg_path = tmp_path / "decider.json"
    cfg_path.write_text(json.dumps(cfg))
    out, cache = tmp_path / "run", tmp_path / "calls.sqlite"
    base = ["run", "--decider", str(cfg_path), "--cache", str(cache), "--n", "2", "--adapters", "bm25",
            "--repeats", "1", "--boot-reps", "20", "--out", str(out)]
    assert main(base + ["--cap-usd", "0"]) == 3
    assert json.loads((out / "manifest.json").read_text())["state"] == "stopped_at_cap"
    assert main(base + ["--cap-usd", "1"]) == 0
    m = json.loads((out / "manifest.json").read_text())
    assert m["state"] == "complete" and m["cap_usd"] == 1.0
    assert json.loads((out / "spend.json").read_text())["total_usd"] > 0
    capsys.readouterr()
    assert main(["run", "--mock", "--decider", str(cfg_path), "--out", str(tmp_path / "x")]) == 2
    unpriced = tmp_path / "unpriced.json"
    unpriced.write_text(json.dumps({**cfg, "price": None}))
    assert main(["run", "--decider", str(unpriced), "--cache", str(cache), "--n", "1", "--out",
                 str(tmp_path / "u")]) == 2
    assert "allow-unpriced" in capsys.readouterr().err
