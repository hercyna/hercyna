"""Validity run: sample, scoring, gate, confusion, no text on disk, cap stop, cache rerun, CLI."""

from __future__ import annotations

import json

import pytest
from model_fakes import HAIKU_CFG, reply

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import ACTIONS, MECHANISMS
from memory_bench.cli import main
from memory_bench.deciders.mock import MockOracleDecider
from memory_bench.deciders.model import load_decider
from memory_bench.engine.validity import run_validity, validity_sample
from memory_bench.gateway import Gateway


@pytest.fixture(scope="module")
def sample():
    return validity_sample(dev_cases(), 10)


def fixed_runner(action, log=None):
    def run(argv, stdin, timeout):
        if log is not None:
            log.append(1)
        out = {"content": [{"type": "text", "text": reply(action)}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 400, "output_tokens": 50}}
        return 0, json.dumps(out), ""

    return run


def test_sample_shape_and_determinism(sample):
    assert len(sample) == 60
    assert [c.mechanism for c in sample] == [m for m in MECHANISMS for _ in range(10)]
    assert [c.case_id for c in sample] == [c.case_id for c in validity_sample(dev_cases(), 10)]
    allc = dev_cases()
    for m in MECHANISMS:
        mine = [c.case_id for c in allc if c.mechanism == m][:10]
        assert [c.case_id for c in sample if c.mechanism == m] == mine
    assert len(validity_sample(allc, 3)) == 18


def test_mock_oracle_passes(tmp_path, sample):
    res = run_validity(MockOracleDecider(sample), sample, tmp_path / "v")
    assert res["schema"] == "memory_bench.validity/1" and res["complete"] is True
    assert res["n"] == 60 and res["accuracy"]["overall"] == {"correct": 60, "n": 60, "accuracy": 1.0}
    assert list(res["accuracy"]["per_mechanism"]) == list(MECHANISMS)
    assert all(v == {"correct": 10, "n": 10, "accuracy": 1.0} for v in res["accuracy"]["per_mechanism"].values())
    assert res["gate"]["passes"] is True and res["parse_errors"] == 0
    assert res["decider"]["id"] == "mock-oracle"
    on_disk = json.loads((tmp_path / "v" / "validity.json").read_text())
    assert on_disk == res
    rows = [json.loads(x) for x in (tmp_path / "v" / "decisions.jsonl").read_text().splitlines()]
    assert len(rows) == 60 and all(set(r) == {"case_id", "mechanism", "a_star", "action", "correct"} for r in rows)


def test_fixed_action_fails_gate(tmp_path, sample):
    log: list = []
    with Gateway(tmp_path / "c.sqlite", 5.0) as g:
        d = load_decider(HAIKU_CFG, g, runner=fixed_runner(ACTIONS[0], log), sleep=lambda s: None)
        res = run_validity(d, sample, tmp_path / "v", gateway=g)
    assert len(log) == 60
    assert res["gate"]["passes"] is False
    conf = res["confusion"]
    assert sum(n for row in conf.values() for n in row.values()) == 60
    assert all(set(row) == {ACTIONS[0]} for row in conf.values())
    assert sum(conf.get(ACTIONS[0], {}).values()) == res["accuracy"]["overall"]["correct"]
    assert res["usd"] > 0 and res["tokens"]["input"] == 60 * 400 and res["tokens"]["output"] == 60 * 50
    assert res["seconds_per_call"] is not None
    assert res["decider"]["id"] == "haiku-5-5"


def test_no_text_on_disk(tmp_path, sample):
    with Gateway(tmp_path / "c.sqlite", 5.0) as g:
        d = load_decider(HAIKU_CFG, g, runner=fixed_runner(ACTIONS[1]), sleep=lambda s: None)
        run_validity(d, sample, tmp_path / "v", gateway=g)
    blob = ((tmp_path / "v" / "validity.json").read_text() + (tmp_path / "v" / "decisions.jsonl").read_text())
    assert "Records:" not in blob
    for c in sample[:20]:
        assert c.query_text not in blob
        for e in c.events[:3]:
            assert getattr(e, "text", "x" * 40) not in blob


def test_cap_stop_then_resume_uses_cache(tmp_path, sample):
    log: list = []
    cache = tmp_path / "c.sqlite"
    with Gateway(cache, 0.0075) as g:
        d = load_decider(HAIKU_CFG, g, runner=fixed_runner(ACTIONS[0], log), sleep=lambda s: None)
        res = run_validity(d, sample, tmp_path / "v", gateway=g)
    assert res["complete"] is False and 0 < res["n"] < 60 and res["n_planned"] == 60
    assert res["gate"]["passes"] is False and "CapReached" in res["stop_reason"]
    assert json.loads((tmp_path / "v" / "validity.json").read_text())["complete"] is False
    done = res["n"]
    assert len(log) == done
    with Gateway(cache, 5.0) as g:
        d = load_decider(HAIKU_CFG, g, runner=fixed_runner(ACTIONS[0], log), sleep=lambda s: None)
        res2 = run_validity(d, sample, tmp_path / "v", gateway=g)
    assert res2["complete"] is True and res2["n"] == 60
    assert len(log) == 60  # finished cases came from the cache
    with Gateway(cache, 0.0) as g:  # a full rerun costs nothing and needs no live call
        d = load_decider(HAIKU_CFG, g, runner=fixed_runner(ACTIONS[0], log), sleep=lambda s: None)
        res3 = run_validity(d, sample, tmp_path / "v", gateway=g)
    assert res3["complete"] is True and len(log) == 60 and res3["usd"] == res2["usd"]


def test_cli_mock(tmp_path, capsys):
    assert main(["validity", "--mock", "--n-per-mechanism", "2", "--out", str(tmp_path / "v")]) == 0
    out = capsys.readouterr().out
    assert "12 of 12" in out and "passes" in out
    assert json.loads((tmp_path / "v" / "validity.json").read_text())["n"] == 12


def test_cli_needs_exactly_one_decider(tmp_path):
    assert main(["validity", "--out", str(tmp_path / "v")]) == 2
    assert not (tmp_path / "v").exists()
