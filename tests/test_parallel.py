"""Parallel decision calls: byte-identical results, real overlap, wall time, cap under concurrency,
single-flight cache, worker failure, env_unset and the CLI flag. Fakes and tmp_path only."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest
from model_fakes import HAIKU_CFG, action_for, anthropic_runner, reply

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import ACTIONS
from memory_bench.cli import main
from memory_bench.deciders import transports
from memory_bench.deciders.mock import MockNoisyDecider, MockOracleDecider
from memory_bench.deciders.model import ModelDecider, load_config, load_decider
from memory_bench.deciders.transports import AnthropicCommandTransport, RawReply
from memory_bench.engine import RunConfig, RunError, run
from memory_bench.engine.validity import run_validity, validity_sample
from memory_bench.gateway import CapReached, Gateway

N, R = 5, 2


@pytest.fixture(scope="module")
def cases():
    return dev_cases()[:N]


def files(d):
    return (d / "metrics.jsonl").read_bytes(), (d / "summary.json").read_bytes()


def lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


# -- byte identity -------------------------------------------------------------------

@pytest.mark.parametrize("make", [MockOracleDecider, lambda cs: MockNoisyDecider(cs, flip_rate=0.3, seed=4)])
def test_mock_parallel_is_byte_identical(tmp_path, cases, make):
    out = {}
    for p in (1, 4):
        m = run(RunConfig(out_dir=tmp_path / f"p{p}", cases=cases, decider=make(cases), adapters=["bm25", "recent"],
                          budgets=[300, 1000], repeats=R, boot_reps=50, parallel=p))
        assert m["state"] == "complete"
        out[p] = files(tmp_path / f"p{p}")
    assert out[1] == out[4]


def model_run(tmp_path, name, cases, cap, parallel, cache, runner=None, budgets=(1000,)):
    g = Gateway(tmp_path / cache, cap)
    d = load_decider(HAIKU_CFG, g, runner=runner or anthropic_runner(), sleep=lambda s: None)
    try:
        return run(RunConfig(out_dir=tmp_path / name, cases=cases, decider=d, adapters=["bm25", "recent"],
                             budgets=list(budgets), repeats=R, boot_reps=50, parallel=parallel))
    finally:
        g.close()


def test_model_decider_parallel_is_byte_identical(tmp_path, cases):
    # budget 100000 makes views that often equal the full view: the same request inside one case
    m1 = model_run(tmp_path, "p1", cases, 5.0, 1, "a.sqlite", budgets=(1000, 100000))
    m4 = model_run(tmp_path, "p4", cases, 5.0, 4, "b.sqlite", budgets=(1000, 100000))
    assert m1["state"] == m4["state"] == "complete"
    assert files(tmp_path / "p1") == files(tmp_path / "p4")
    s1 = json.loads((tmp_path / "p1" / "spend.json").read_text())
    s4 = json.loads((tmp_path / "p4" / "spend.json").read_text())
    for k in ("total_usd", "calls", "cached_calls", "input_tokens", "output_tokens"):
        assert s1[k] == s4[k], k  # single-flight: parallel pays for exactly what sequential pays for
    assert s1["cached_calls"] > 0  # the shared requests really happened
    c4 = lines(tmp_path / "p4" / "calls.jsonl")
    assert sum(not c["cached"] for c in c4) == len({c["request_sha256"] for c in c4})


# -- overlap and wall time -------------------------------------------------------------

class SlowTransport:
    """A thread-safe fake transport: sleeps, counts calls in flight, answers deterministically."""

    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.now = self.peak = self.calls = 0

    def send(self, messages, *, repeat, retry_reminder=False):
        with self.lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)
        try:
            time.sleep(self.delay)
            user = messages[-1]["content"]
            a = action_for(user)
            return RawReply(reply(ACTIONS[0] if a == "BAD" else a), len(user) // 3, 40)
        finally:
            with self.lock:
                self.now -= 1


def test_calls_really_overlap_and_never_exceed_parallel(tmp_path, cases):
    t = SlowTransport(0.02)
    g = Gateway(tmp_path / "c.sqlite", 5.0)
    d = ModelDecider(load_config(HAIKU_CFG), t, g)
    m = run(RunConfig(out_dir=tmp_path / "r", cases=cases[:3], decider=d, adapters=["bm25", "recent"],
                      budgets=[1000], repeats=3, boot_reps=20, parallel=4))
    g.close()
    assert m["state"] == "complete"
    assert 1 < t.peak <= 4


class SleepyDecider:
    id, model, params = "sleepy", "sleepy", {}

    def __init__(self, delay):
        self.delay = delay
        self.n = 0
        self.lock = threading.Lock()

    def decide(self, inp):
        with self.lock:
            self.n += 1
        time.sleep(self.delay)
        return ACTIONS[len(inp.records_text) % 4]


def test_parallel_wall_time(tmp_path, cases):
    # 4 cases x (1 gate + 3 floor + 2 adapters x 1 budget x 3 repeats) = 40 calls of 0.05 s
    secs = {}
    for p in (1, 4):
        d = SleepyDecider(0.05)
        t0 = time.perf_counter()
        run(RunConfig(out_dir=tmp_path / f"p{p}", cases=cases[:4], decider=d, adapters=["bm25", "recent"],
                      budgets=[1000], repeats=3, boot_reps=20, parallel=p))
        secs[p] = time.perf_counter() - t0
        assert d.n == 40
    assert secs[1] >= 2.0
    assert secs[4] < 0.6 * secs[1], secs
    assert files(tmp_path / "p1") == files(tmp_path / "p4")


# -- cap under concurrency ------------------------------------------------------------------

def test_cap_never_exceeded_in_parallel_and_resume_matches(tmp_path):
    cs = dev_cases()[:8]

    def slow_runner():
        inner = anthropic_runner()

        def run_(argv, stdin, timeout):
            time.sleep(0.005)
            return inner(argv, stdin, timeout)
        return run_

    full = model_run(tmp_path, "uncapped", cs, 5.0, 1, "a.sqlite")
    assert full["state"] == "complete"
    total = json.loads((tmp_path / "uncapped" / "spend.json").read_text())["total_usd"]

    cap = total / 3
    m1 = model_run(tmp_path, "capped", cs, cap, 4, "b.sqlite", runner=slow_runner())
    d = tmp_path / "capped"
    assert m1["state"] == "stopped_at_cap" and "CapReached" in m1["error"]
    paid = sum(c["usd"] for c in lines(d / "calls.jsonl"))
    assert 0 < paid <= cap + 1e-12
    assert json.loads((d / "spend.json").read_text())["total_usd"] <= cap + 1e-9

    m2 = model_run(tmp_path, "capped", cs, 5.0, 4, "b.sqlite", runner=slow_runner())
    assert m2["state"] == "complete" and not m2["incomplete_cells"]
    assert files(d) == files(tmp_path / "uncapped")
    assert json.loads((d / "spend.json").read_text())["total_usd"] == pytest.approx(total, abs=1e-6)


def test_gateway_reservation_under_threads(tmp_path):
    """16 threads, distinct requests, a cap that fits about 5 worst cases: jointly never past the cap."""
    cfg = load_config(HAIKU_CFG)
    t = SlowTransport(0.02)
    g = Gateway(tmp_path / "c.sqlite", 0.0158, run_dir=tmp_path / "run")  # worst case is ~0.003 per call
    results, errors = [], []
    barrier = threading.Barrier(16)

    def work(i):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": f"request {i}"}]
        barrier.wait()
        try:
            results.append(g.call(cfg, t, msgs, repeat=1, context={"kind": "view"}))
        except CapReached as exc:
            errors.append(exc)

    ths = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert len(results) + len(errors) == 16 and errors and results
    assert t.peak <= 5  # spent + reserved never let more than 5 worst cases in flight
    assert g.reserved_usd() == 0.0
    paid = sum(c["usd"] for c in lines(tmp_path / "run" / "calls.jsonl"))
    assert paid <= g.cap_usd and paid == pytest.approx(g.spent_usd(), abs=1e-9)
    g.close()


def test_same_request_in_flight_is_sent_once(tmp_path):
    cfg = load_config(HAIKU_CFG)
    t = SlowTransport(0.05)
    g = Gateway(tmp_path / "c.sqlite", 5.0, run_dir=tmp_path / "run")
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "the same request"}]
    barrier = threading.Barrier(8)
    texts = []

    def work():
        barrier.wait()
        texts.append(g.call(cfg, t, msgs, repeat=1, context={"kind": "floor"}).text)

    ths = [threading.Thread(target=work) for _ in range(8)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert t.calls == 1 and len(set(texts)) == 1
    calls = lines(tmp_path / "run" / "calls.jsonl")
    assert len(calls) == 8 and sum(not c["cached"] for c in calls) == 1
    sp = g.spend()
    assert sp["calls"] == 8 and sp["cached_calls"] == 7
    assert sp["total_usd"] == pytest.approx(calls[[c["cached"] for c in calls].index(False)]["usd"])
    g.close()


# -- failures ---------------------------------------------------------------------------------

class Boom(RuntimeError):
    pass


class FailingDecider(SleepyDecider):
    id = "failing"

    def decide(self, inp):
        with self.lock:
            self.n += 1
            k = self.n
        time.sleep(self.delay)
        if k == 7:
            raise Boom("decider broke")
        return ACTIONS[0]


def test_worker_exception_fails_the_run(tmp_path, cases):
    d = FailingDecider(0.01)
    with pytest.raises(Boom):
        run(RunConfig(out_dir=tmp_path / "f", cases=cases, decider=d, adapters=["bm25", "recent"], budgets=[1000],
                      repeats=R, boot_reps=20, parallel=4))
    m = json.loads((tmp_path / "f" / "manifest.json").read_text())
    assert m["state"] == "failed" and "Boom" in m["error"]
    rows = lines(tmp_path / "f" / "metrics.jsonl") if (tmp_path / "f" / "metrics.jsonl").exists() else []
    assert len(rows) % (1 + R + 2 * R) == 0  # only whole cases were written


def test_parallel_bounds(tmp_path, cases):
    for bad in (0, 9):
        with pytest.raises(RunError):
            run(RunConfig(out_dir=tmp_path / f"b{bad}", cases=cases, decider=MockOracleDecider(cases),
                          parallel=bad))


# -- validity --------------------------------------------------------------------------------

def test_validity_parallel_matches_and_stops_at_cap(tmp_path):
    cs = validity_sample(dev_cases(), 3)

    def go(name, cap, parallel, cache):
        g = Gateway(tmp_path / cache, cap)
        d = load_decider(HAIKU_CFG, g, runner=anthropic_runner(), sleep=lambda s: None)
        try:
            return run_validity(d, cs, tmp_path / name, parallel=parallel)
        finally:
            g.close()

    r1 = go("v1", 5.0, 1, "a.sqlite")
    r4 = go("v4", 5.0, 4, "b.sqlite")
    assert r1["complete"] and r4["complete"]
    assert (tmp_path / "v1" / "decisions.jsonl").read_bytes() == (tmp_path / "v4" / "decisions.jsonl").read_bytes()
    assert r1["accuracy"] == r4["accuracy"] and r1["usd"] == pytest.approx(r4["usd"])

    rc = go("vc", r1["usd"] / 3, 4, "c.sqlite")
    assert not rc["complete"] and rc["stop_reason"].startswith("CapReached") and rc["n"] < len(cs)
    assert rc["usd"] <= r1["usd"] / 3 + 1e-9
    got = lines(tmp_path / "vc" / "decisions.jsonl")
    assert [x["case_id"] for x in got] == [c.case_id for c in cs[: len(got)]]  # a prefix in sample order
    rr = go("vc", 5.0, 4, "c.sqlite")
    assert rr["complete"]
    assert (tmp_path / "vc" / "decisions.jsonl").read_bytes() == (tmp_path / "v1" / "decisions.jsonl").read_bytes()


# -- env_unset ---------------------------------------------------------------------------------

def test_env_unset_reaches_runner_and_leaves_environ(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-not-real")
    monkeypatch.setenv("BENCH_KEEP_ME", "1")
    before = dict(os.environ)
    seen = []

    def runner(argv, stdin, timeout, env=None):
        seen.append(env)
        return anthropic_runner()(argv, stdin, timeout)

    cfg = load_config({**HAIKU_CFG, "env_unset": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]})
    t = AnthropicCommandTransport(cfg, runner=runner, sleep=lambda s: None)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    t.send(msgs, repeat=0)
    assert dict(os.environ) == before
    env = seen[0]
    assert set(env) == set(before) - {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
    assert env["BENCH_KEEP_ME"] == "1" and all(env[k] == before[k] for k in env)

    # without env_unset a three-argument runner keeps working (no env keyword is passed)
    AnthropicCommandTransport(load_config(HAIKU_CFG), runner=anthropic_runner(), sleep=lambda s: None).send(
        msgs, repeat=0)


def test_default_runner_passes_env_to_subprocess(monkeypatch):
    got = {}

    class Done:
        returncode, stdout, stderr = 0, "{}", ""

    def fake_run(argv, **kw):
        got.update(kw)
        return Done()

    monkeypatch.setattr(transports.subprocess, "run", fake_run)
    assert transports.subprocess_runner(["x"], "in", 5.0, env={"A": "1"}) == (0, "{}", "")
    assert got["env"] == {"A": "1"}
    transports.subprocess_runner(["x"], "in", 5.0)
    assert got["env"] is None


def test_env_unset_config_checks():
    from pathlib import Path

    for bad in ("ANTHROPIC_API_KEY", [""], [1]):
        with pytest.raises(ValueError, match="env_unset"):
            load_config({**HAIKU_CFG, "env_unset": bad})
    with pytest.raises(ValueError, match="env_unset"):
        load_config({"id": "o", "transport": "ollama", "model": "m", "price": None, "env_unset": ["X"]})
    root = Path(__file__).resolve().parents[1] / "configs" / "deciders"
    assert load_config(root / "haiku-5-5.json")["env_unset"] == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]


# -- CLI -----------------------------------------------------------------------------------------

def test_cli_parallel_flag(tmp_path, capsys):
    base = ["run", "--mock", "--n", "3", "--repeats", "2", "--boot-reps", "30"]
    assert main(base + ["--parallel", "9", "--out", str(tmp_path / "x")]) == 2
    assert "--parallel must be between 1 and 8" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()
    assert main(base + ["--out", str(tmp_path / "a")]) == 0
    assert main(base + ["--parallel", "4", "--out", str(tmp_path / "b")]) == 0
    assert files(tmp_path / "a") == files(tmp_path / "b")
    assert main(["validity", "--mock", "--n-per-mechanism", "1", "--parallel", "9", "--out", str(tmp_path / "v")]) == 2
    assert main(["validity", "--mock", "--n-per-mechanism", "2", "--parallel", "4", "--out", str(tmp_path / "v")]) == 0
