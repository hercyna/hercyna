"""Dry run: counts, no transport call, dollars, unpriced, CLI. Writes only under tmp_path."""

from __future__ import annotations

import copy
import json

import pytest
from model_fakes import HAIKU_CFG

from memory_bench.cases.sample import dev_cases
from memory_bench.cli import main
from memory_bench.deciders import transports
from memory_bench.deciders.model import load_config
from memory_bench.engine.dryrun import dry_run, format_dry_run

N, R = 4, 2


@pytest.fixture(autouse=True)
def no_calls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the dry run made a call")

    monkeypatch.setattr(transports, "make_transport", boom)
    monkeypatch.setattr(transports.Transport, "send", boom)
    monkeypatch.setattr(transports, "subprocess_runner", boom)
    monkeypatch.setattr(transports, "urllib_opener", boom)
    import memory_bench.deciders.model as model

    monkeypatch.setattr(model, "make_transport", boom)


def cfg_with(**kw):
    c = copy.deepcopy(HAIKU_CFG)
    c.update(kw)
    return c


def go(cfg=None, adapters=("bm25", "recent"), budgets=(500, 1000), repeats=R, n=N):
    return dry_run({"cases": dev_cases()[:n], "decider": cfg or HAIKU_CFG, "adapters": list(adapters),
                    "budgets": list(budgets), "repeats": repeats})


def test_call_counts():
    res = go()
    st = res["stages"]
    assert st["gate"]["calls"] == N
    assert st["floor"]["calls"] == N * R
    assert st["gate"]["calls"] + st["floor"]["calls"] == N * (1 + R)
    assert st["view"]["calls"] == N * 2 * 2 * R
    assert res["total"]["calls"] == N * (1 + R) + N * 2 * 2 * R
    assert res["total"]["input_tokens"] == sum(s["input_tokens"] for s in st.values()) > 0


def test_output_tokens_and_dollars():
    res = go(cfg_with(assumed_output_tokens=700))
    t = res["total"]
    assert t["output_tokens_likely"] == 700 * t["calls"]
    assert t["output_tokens_upper"] == 6000 * t["calls"]
    p = HAIKU_CFG["price"]
    assert t["usd_likely"] == pytest.approx(t["input_tokens"] * p["input_per_m"] / 1e6
                                            + t["output_tokens_likely"] * p["output_per_m"] / 1e6, abs=1e-6)
    assert t["usd_upper"] == pytest.approx(t["input_tokens"] * p["input_per_m"] / 1e6
                                           + t["output_tokens_upper"] * p["output_per_m"] / 1e6, abs=1e-6)
    assert t["usd_upper"] > t["usd_likely"] > 0 and res["unpriced"] is False
    assert res["stages"]["gate"]["usd_likely"] is not None
    assert go()["assumed_output_tokens"] == 1500


def test_unpriced():
    res = go(cfg_with(price=None))
    assert res["unpriced"] is True
    for st in (*res["stages"].values(), res["total"]):
        assert st["usd_likely"] is None and st["usd_upper"] is None and st["unpriced"] is True
    assert "unpriced" in format_dry_run(res)


def test_views_follow_budget_and_adapters():
    small = go(budgets=(200,), adapters=("recent",))
    large = go(budgets=(2000,), adapters=("recent",))
    assert small["stages"]["view"]["calls"] == large["stages"]["view"]["calls"] == N * R
    assert small["stages"]["view"]["input_tokens"] < large["stages"]["view"]["input_tokens"]
    assert small["stages"]["gate"]["input_tokens"] == large["stages"]["gate"]["input_tokens"]


def test_format_table():
    text = format_dry_run(go())
    for word in ("gate", "floor", "view", "total", "usd likely", "no model call made"):
        assert word in text


def test_cli_dryrun(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "d.json"
    cfg.write_text(json.dumps(HAIKU_CFG))
    monkeypatch.chdir(tmp_path)
    assert main(["dryrun", "--decider", str(cfg), "--n", "3", "--repeats", "2", "--budgets", "500,1000",
                 "--adapters", "bm25,recent"]) == 0
    out = capsys.readouterr().out
    assert "total" in out and "3 cases" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d.json"]  # no run folder


def test_cli_dryrun_bad_config(tmp_path, capsys):
    cfg = tmp_path / "d.json"
    cfg.write_text(json.dumps({"id": "x"}))
    assert main(["dryrun", "--decider", str(cfg), "--n", "1"]) == 2
    assert load_config(HAIKU_CFG)["id"] == "haiku-5-5"
