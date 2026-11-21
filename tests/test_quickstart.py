"""The 20-case quickstart set and `bench quickstart`. Mock decider or fakes only; files under tmp_path."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from model_fakes import HAIKU_CFG, anthropic_runner

from memory_bench.cases.quickstart import QUICKSTART_SHA256, quickstart_cases
from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import write_jsonl
from memory_bench.cli import main

DATA = Path(__file__).resolve().parent.parent / "data" / "quickstart" / "cases.jsonl"


def fresh_bytes(tmp_path) -> bytes:
    p = tmp_path / "fresh.jsonl"
    write_jsonl(p, quickstart_cases())
    return p.read_bytes()


def test_counts_and_order():
    qs = quickstart_cases()
    assert len(qs) == 20
    assert Counter(c.mechanism for c in qs) == {"M0": 10, "M1": 2, "M2": 2, "M3": 2, "U1": 2, "U2": 2}
    pos = {c.case_id: i for i, c in enumerate(dev_cases())}
    idx = [pos[c.case_id] for c in qs]
    assert idx == sorted(idx)
    assert [c.case_id for c in quickstart_cases()] == [c.case_id for c in qs]


def test_hash_and_file_on_disk(tmp_path):
    raw = fresh_bytes(tmp_path)
    assert hashlib.sha256(raw).hexdigest() == QUICKSTART_SHA256
    assert DATA.read_bytes() == raw


def test_mock_quickstart(tmp_path, capsys):
    out = tmp_path / "q1"
    assert main(["quickstart", "--mock", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "bm25/default@1000" in text and "recent/default@1000" in text and "wrong commit" in text
    assert "time: " in text and "(limit 600s) PASS" in text
    assert "cost: $0.0000 (limit $1.00) PASS" in text
    assert ("smoke test: 20 cases check the install, time and cost. "
            "The rates below are not a result; use bench run for one.") in text
    assert "validity" not in text
    assert "calls: 0 live, 0 cached" in text
    q = json.loads((out / "quickstart.json").read_text())
    assert set(q) == {"seconds", "total_usd", "live_calls", "cached_calls", "time_limit_s", "cost_limit_usd",
                      "time_pass", "cost_pass", "parallel", "smoke_test"}
    assert q["time_limit_s"] == 600 and q["cost_limit_usd"] == 1.0 and q["parallel"] == 4
    rows = (out / "metrics.jsonl").read_text().splitlines()
    assert len(rows) == 200
    assert "seconds" not in (out / "metrics.jsonl").read_text()
    assert "seconds" not in (out / "summary.json").read_text()

    assert main(["quickstart", "--mock", "--out", str(tmp_path / "q2"), "--parallel", "2"]) == 0
    for name in ("metrics.jsonl", "summary.json"):
        assert (out / name).read_bytes() == (tmp_path / "q2" / name).read_bytes()


def test_cap_exit_code(tmp_path, monkeypatch, capsys):
    from memory_bench.deciders import model

    real = model.load_decider
    monkeypatch.setattr(model, "load_decider",
                        lambda cfg, g, **kw: real(cfg, g, runner=anthropic_runner(), sleep=lambda s: None))
    cfg = tmp_path / "decider.json"
    cfg.write_text(json.dumps(HAIKU_CFG))
    out = tmp_path / "capped"
    rc = main(["quickstart", "--decider", str(cfg), "--cache", str(tmp_path / "c.sqlite"),
               "--cap-usd", "0.00001", "--out", str(out)])
    assert rc == 3
    assert "stopped at the cap" in capsys.readouterr().err
    assert json.loads((out / "manifest.json").read_text())["state"] == "stopped_at_cap"
