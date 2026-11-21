"""The run engine with a product adapter behind the product proxy (docs/product-proxy.md sections 5 and 6):
end to end, spend and manifest, one cap across decider and memory side, stop and resume, CLI and dry run.
A fake upstream on 127.0.0.1; no network, no real key command."""

from __future__ import annotations

import json

import pytest
from model_fakes import HAIKU_CFG, anthropic_runner
from proxy_fakes import FakeUpstream, ProbeProductAdapter, memory_side

from memory_bench import adapters as adapter_registry
from memory_bench.cases.sample import dev_cases
from memory_bench.cli import main
from memory_bench.deciders.mock import MockOracleDecider
from memory_bench.deciders.model import load_decider
from memory_bench.engine import RunConfig, RunError, run
from memory_bench.engine.dryrun import dry_run, format_dry_run
from memory_bench.gateway import Gateway, MEMORY_CALLS
from memory_bench.submit.textfree import scan_run

N = 4


@pytest.fixture(scope="module")
def cases():
    return dev_cases()[:N]


@pytest.fixture
def upstream():
    with FakeUpstream() as up:
        yield up


class Probe(ProbeProductAdapter):
    built: list["Probe"] = []

    def __init__(self, proxy, **config):
        super().__init__(proxy, **config)
        Probe.built.append(self)


def lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def mock_run(tmp_path, name, cases, up, cap=5.0, cache="cache.sqlite", **cfg_kw):
    return run(RunConfig(out_dir=tmp_path / name, cases=cases, decider=MockOracleDecider(cases),
                         adapters=["bm25", Probe], budgets=[300, 1000], repeats=2, boot_reps=30, cap_usd=cap,
                         memory_side=memory_side(up, **cfg_kw), proxy_cache=tmp_path / cache))


def test_end_to_end_with_a_mock_decider(tmp_path, cases, upstream):
    m = mock_run(tmp_path, "run", cases, upstream)
    d = tmp_path / "run"
    assert m["state"] == "complete" and not m["incomplete_cells"]
    assert m["memory_side"]["id"] == "test-mem" and m["memory_side"]["chat_model"] == "upstream-chat-1"
    assert m["memory_side"]["embeddings_model"] == "upstream-embed-1" and len(m["memory_side"]["config_sha256"]) == 64
    assert MEMORY_CALLS in m["files"] and "calls.jsonl" not in m["files"]
    assert [a["name"] for a in m["adapters"]] == ["full", "bm25", "probe"]
    calls = lines(d / MEMORY_CALLS)
    # the view stage: one reset per case for both budgets (docs/view-stage.md): an embeddings call at ingest,
    # a chat call at retrieve, nothing repeated for the second budget.
    live = [c for c in calls if not c["cached"]]
    assert len(calls) == N * 2 and len(live) == upstream.count() == N * 2
    assert sum(c["route"] == "embeddings" for c in calls) == N
    assert {(c["phase"], c["route"]) for c in calls} == {("ingest", "embeddings"), ("retrieve", "chat")}
    assert {c["case_id"] for c in calls} == {c.case_id for c in cases}
    assert all(c["adapter"] == "probe" and c["model"] in ("upstream-chat-1", "upstream-embed-1") for c in calls)
    spend = json.loads((d / "spend.json").read_text())
    ms = spend["by_adapter"]["probe"]["memory_side"]
    assert ms["calls"] == len(calls) and ms["embed_calls"] == N
    assert ms["cached_calls"] == len(calls) - len(live)
    assert ms["input_tokens"] == sum(c["input_tokens"] for c in live)
    assert ms["output_tokens"] == sum(c["output_tokens"] for c in live)
    assert ms["usd"] == pytest.approx(sum(c["usd"] for c in calls), abs=1e-9) and ms["usd"] > 0
    assert spend["memory_side_usd"] == pytest.approx(ms["usd"], abs=1e-9)
    assert spend["total_usd"] == pytest.approx(spend["memory_side_usd"], abs=1e-9)  # the mock decider is free
    assert spend["by_adapter"]["bm25"]["memory_side"]["calls"] == 0
    # the adapter reported the usage the proxy measured (Scope.usage counts cached calls too)
    assert spend["by_adapter"]["probe"]["calls"] == len(calls)
    assert spend["by_adapter"]["probe"]["input_tokens"] == sum(c["input_tokens"] for c in calls)
    assert scan_run(d) == []

    # a second run on the same cache: identical result files, every memory call cached and free
    m2 = mock_run(tmp_path, "again", cases, upstream)
    assert m2["state"] == "complete" and upstream.count() == len(live)
    for name in ("metrics.jsonl", "summary.json"):
        assert (tmp_path / "again" / name).read_bytes() == (d / name).read_bytes()
    again = lines(tmp_path / "again" / MEMORY_CALLS)
    assert all(c["cached"] for c in again) and sum(c["usd"] for c in again) == 0
    assert json.loads((tmp_path / "again" / "spend.json").read_text())["memory_side_usd"] == 0.0


def test_product_adapter_without_memory_side_is_an_error_before_any_call(tmp_path, cases, upstream):
    with pytest.raises(RunError, match="memory-side"):
        run(RunConfig(out_dir=tmp_path / "x", cases=cases, decider=MockOracleDecider(cases),
                      adapters=["bm25", Probe]))
    assert not (tmp_path / "x").exists() and upstream.count() == 0
    with pytest.raises(RunError, match="registry name or its class"):
        run(RunConfig(out_dir=tmp_path / "y", cases=cases, decider=MockOracleDecider(cases),
                      adapters=[Probe(proxy=None)], memory_side=memory_side(upstream),
                      proxy_cache=tmp_path / "c.sqlite"))
    assert upstream.count() == 0


def test_runs_without_a_product_adapter_are_unchanged(tmp_path, cases, upstream):
    m = run(RunConfig(out_dir=tmp_path / "plain", cases=cases, decider=MockOracleDecider(cases),
                      adapters=["bm25"], repeats=2, boot_reps=30, memory_side=memory_side(upstream),
                      proxy_cache=tmp_path / "c.sqlite"))
    assert m["memory_side"] is None and MEMORY_CALLS not in m["files"]
    spend = json.loads((tmp_path / "plain" / "spend.json").read_text())
    assert "memory_side_usd" not in spend and "memory_side" not in spend["by_adapter"]["bm25"]
    assert not (tmp_path / "c.sqlite").exists() and upstream.count() == 0


def test_memory_side_cap_stops_the_run_and_resume_matches(tmp_path, cases, upstream):
    full = mock_run(tmp_path, "uncapped", cases, upstream, cache="a.sqlite")
    total = json.loads((tmp_path / "uncapped" / "spend.json").read_text())["memory_side_usd"]
    assert full["state"] == "complete" and total > 0

    # one ingest per case (the view stage) leaves N chat calls in `total`; a third of it is below one call's
    # worst case, so half is used: the stage pays for some calls, then stops
    m1 = mock_run(tmp_path, "capped", cases, upstream, cap=total / 2, cache="b.sqlite")
    d = tmp_path / "capped"
    assert m1["state"] == "stopped_at_cap" and m1["incomplete_cells"] and "CapReached" in m1["error"]
    assert "memory-side call" in m1["error"]
    s1 = json.loads((d / "spend.json").read_text())
    assert 0 < s1["memory_side_usd"] <= total / 2 + 1e-9
    assert s1["total_usd"] == pytest.approx(sum(c["usd"] for c in lines(d / MEMORY_CALLS)), abs=1e-9)

    m2 = mock_run(tmp_path, "capped", cases, upstream, cap=5.0, cache="b.sqlite")
    assert m2["state"] == "complete" and m2["attempts"] == 2 and not m2["incomplete_cells"]
    for name in ("metrics.jsonl", "summary.json"):
        assert (d / name).read_bytes() == (tmp_path / "uncapped" / name).read_bytes()
    s2 = json.loads((d / "spend.json").read_text())
    assert s2["memory_side_usd"] == pytest.approx(total, abs=1e-6)  # nothing was paid for twice


def test_resume_refuses_a_different_memory_side(tmp_path, cases, upstream):
    assert mock_run(tmp_path, "r", cases[:1], upstream)["state"] == "complete"
    cfg = memory_side(upstream)
    cfg["chat"]["model"] = "another-upstream-model"
    with pytest.raises(RunError, match="memory side"):
        run(RunConfig(out_dir=tmp_path / "r", cases=cases[:1], decider=MockOracleDecider(cases),
                      adapters=["bm25", Probe], budgets=[300, 1000], repeats=2, boot_reps=30, cap_usd=5.0,
                      memory_side=cfg, proxy_cache=tmp_path / "cache.sqlite"))


# -- one cap across the decider and the memory side ---------------------------------------------

DECIDER = {**HAIKU_CFG, "params": {"max_tokens": 300, "effort": "high"},
           "price": {"input_per_m": 10.0, "output_per_m": 10.0, "as_of": "2026-11-19", "source": "test"}}
MEM_PRICE = (30.0, 30.0)


def model_run(tmp_path, name, cases, up, cap, cache, chat_price=MEM_PRICE, decider=True):
    g = Gateway(tmp_path / cache, cap)
    d = load_decider(DECIDER, g, runner=anthropic_runner(), sleep=lambda s: None) if decider \
        else MockOracleDecider(cases)
    try:
        m = run(RunConfig(out_dir=tmp_path / name, cases=cases, decider=d, adapters=[Probe], budgets=[1000],
                          repeats=1, boot_reps=30, gateway=g if decider else None, cap_usd=cap,
                          memory_side=memory_side(up, chat_price=chat_price), proxy_cache=tmp_path / cache))
    finally:
        g.close()
    assert g.extra_spent() == 0.0  # the run unwired the gateway
    return m


def test_one_cap_across_decider_and_memory_side(tmp_path, cases, upstream):
    Probe.built.clear()
    m = model_run(tmp_path, "uncapped", cases, upstream, 50.0, "a.sqlite")
    d = tmp_path / "uncapped"
    assert m["state"] == "complete"
    spend = json.loads((d / "spend.json").read_text())
    dec = sum(c["usd"] for c in lines(d / "calls.jsonl"))
    mem = sum(c["usd"] for c in lines(d / MEMORY_CALLS))
    assert dec > 0 and mem > 0
    assert spend["memory_side_usd"] == pytest.approx(mem, abs=1e-9)
    assert spend["total_usd"] == pytest.approx(dec + mem, abs=1e-6)
    assert spend["by_adapter"]["probe"]["memory_side"]["usd"] == pytest.approx(mem, abs=1e-9)
    assert set(m["files"]) >= {"calls.jsonl", MEMORY_CALLS}
    # the view stage runs before any decide call, so in a fresh run the decider has spent nothing yet; that the
    # proxy sees the decider's dollars is checked on a resume in test_proxy_sees_the_decider_dollars_on_resume
    assert [x for p in Probe.built for x in p.seen_elsewhere] == [0.0] * len(cases)

    cap = dec + mem - 0.5 * min(dec, mem)
    # each side alone fits under this cap...
    alone_mem = model_run(tmp_path, "mem-alone", cases, upstream, cap, "b.sqlite", decider=False)
    assert alone_mem["state"] == "complete"
    alone_dec = model_run(tmp_path, "dec-alone", cases, upstream, cap, "c.sqlite", chat_price=(0, 0))
    assert alone_dec["state"] == "complete"
    # ...both together do not: the run stops at the one cap and the joint spend stays under it
    both = model_run(tmp_path, "both", cases, upstream, cap, "d.sqlite")
    b = tmp_path / "both"
    assert both["state"] == "stopped_at_cap" and "CapReached" in both["error"]
    paid = sum(c["usd"] for c in lines(b / "calls.jsonl")) + sum(c["usd"] for c in lines(b / MEMORY_CALLS))
    assert 0 < paid <= cap + 1e-9
    assert json.loads((b / "spend.json").read_text())["total_usd"] == pytest.approx(paid, abs=1e-6)

    resumed = model_run(tmp_path, "both", cases, upstream, 50.0, "d.sqlite")
    assert resumed["state"] == "complete"
    for name in ("metrics.jsonl", "summary.json"):
        assert (b / name).read_bytes() == (d / name).read_bytes()


class ProbeFailsOnce(Probe):
    """Fails at ingest on one case while `fail` is set (the first attempt), so a resume has stage work left."""

    fail: str | None = None

    def ingest(self, records):
        if self.scope.case_id == ProbeFailsOnce.fail:
            raise ConnectionError("product timed out")
        super().ingest(records)


def test_proxy_sees_the_decider_dollars_on_resume(tmp_path, cases, upstream):
    def go():
        g = Gateway(tmp_path / "a.sqlite", 50.0)
        d = load_decider(DECIDER, g, runner=anthropic_runner(), sleep=lambda s: None)
        try:
            return run(RunConfig(out_dir=tmp_path / "r", cases=cases, decider=d, adapters=[ProbeFailsOnce],
                                 budgets=[1000], repeats=1, boot_reps=30, gateway=g, cap_usd=50.0,
                                 memory_side=memory_side(upstream, chat_price=MEM_PRICE),
                                 proxy_cache=tmp_path / "a.sqlite"))
        finally:
            g.close()

    Probe.built.clear()
    ProbeFailsOnce.fail = cases[1].case_id
    m1 = go()
    assert m1["state"] == "complete" and m1["incomplete_cells"]
    assert sum(c["usd"] for c in lines(tmp_path / "r" / "calls.jsonl")) > 0
    Probe.built.clear()
    ProbeFailsOnce.fail = None
    m2 = go()
    assert m2["state"] == "complete" and not m2["incomplete_cells"]
    seen = [x for p in Probe.built for x in p.seen_elsewhere]
    assert len(seen) == 1 and seen[0] > 0  # the one retried case; the gateway reloaded the first attempt's spend


# -- CLI and dry run ----------------------------------------------------------------------------

def test_cli_run_memory_side_and_dryrun_line(tmp_path, upstream, monkeypatch, capsys):
    monkeypatch.setitem(adapter_registry.ADAPTERS, "probe", ProbeProductAdapter)
    cfg_path = tmp_path / "mem.json"
    cfg_path.write_text(json.dumps(memory_side(upstream)))
    base = ["run", "--mock", "--n", "2", "--adapters", "bm25,probe", "--repeats", "1", "--boot-reps", "20",
            "--cache", str(tmp_path / "c.sqlite")]
    assert main(base + ["--out", str(tmp_path / "no")]) == 2
    assert "--memory-side" in capsys.readouterr().err and not (tmp_path / "no").exists()
    assert main(base + ["--cap-usd", "0", "--memory-side", str(cfg_path), "--out", str(tmp_path / "cap")]) == 3
    assert json.loads((tmp_path / "cap" / "manifest.json").read_text())["state"] == "stopped_at_cap"
    assert main(base + ["--cap-usd", "1", "--memory-side", str(cfg_path), "--out", str(tmp_path / "ok")]) == 0
    m = json.loads((tmp_path / "ok" / "manifest.json").read_text())
    assert m["state"] == "complete" and m["memory_side"]["id"] == "test-mem"
    unpriced = tmp_path / "unpriced.json"
    unpriced.write_text(json.dumps(memory_side(upstream, chat_price=None)))
    capsys.readouterr()
    assert main(base + ["--memory-side", str(unpriced), "--out", str(tmp_path / "u")]) == 2
    assert "allow-unpriced" in capsys.readouterr().err

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    decider = str(root / "configs" / "deciders" / "haiku-5-5.json")
    assert main(["dryrun", "--decider", decider, "--n", "2", "--adapters", "bm25,probe", "--repeats", "1"]) == 0
    out = capsys.readouterr().out
    assert "memory side: not estimated, run a five-case probe" in out
    assert main(["dryrun", "--decider", decider, "--n", "2", "--adapters", "bm25", "--repeats", "1"]) == 0
    assert "memory side" not in capsys.readouterr().out
    res = dry_run({"cases": dev_cases()[:2], "decider": decider, "adapters": [Probe], "repeats": 1})
    assert res["memory_side"] == "not estimated" and res["stages"]["view"]["calls"] == 2
    assert "memory side: not estimated" in format_dry_run(res)
    n_live = upstream.count()
    assert main(["dryrun", "--decider", decider, "--n", "2", "--adapters", "probe", "--repeats", "1"]) == 0
    assert upstream.count() == n_live > 0  # the dry run made no call


def test_cli_proxy_serve(tmp_path, upstream, monkeypatch, capsys):
    from memory_bench import cli

    cfg_path = tmp_path / "mem.json"
    cfg_path.write_text(json.dumps(memory_side(upstream)))
    got = {}

    def fake_wait(proxy, scope):
        from proxy_fakes import call

        got["status"] = call(f"{scope.base_url}/chat/completions",
                             {"model": "x", "messages": [{"role": "user", "content": "hi"}]})[0]
        got["base_url"] = scope.base_url

    monkeypatch.setattr(cli, "_serve_wait", fake_wait)
    assert main(["proxy", "serve", "--memory-side", str(cfg_path), "--cap-usd", "1", "--log", str(tmp_path / "log"),
                 "--cache", str(tmp_path / "c.sqlite")]) == 0
    out = capsys.readouterr().out
    assert got["status"] == 200 and f"base_url {got['base_url']}" in out and "/s/dev.default.dev-" in out
    assert "1 chat and 0 embedding calls" in out
    assert len(lines(tmp_path / "log" / MEMORY_CALLS)) == 1
    assert main(["proxy", "serve", "--memory-side", str(tmp_path / "missing.json")]) == 2


def test_product_warnings_are_counted_without_text(tmp_path):
    """A product that logs a warning during a call (a dropped memory, a refused patch) is counted in
    health.json by logger name; builtin adapters and their timing rows are untouched."""
    import json
    import logging

    from memory_bench.engine.run import _Health

    class Product:
        name, config_label, product_version, needs_proxy = "p", "default", "1", True

    class Builtin:
        name, config_label, product_version = "b", "default", "builtin"

    def noisy():
        logging.getLogger("product.sdk").error("Error parsing extraction response: secret record text")
        logging.getLogger("product.sdk").warning("again")
        logging.getLogger("other").info("not counted")
        return 7

    h = _Health([Product(), Builtin()])
    assert h.timed(Product(), "c1", 1000, "ingest", noisy) == 7
    assert h.timed(Builtin(), "c1", 1000, "ingest", noisy) == 7
    rep = h.report("r", {})
    by = {a["name"]: a["product_warnings"] for a in rep["adapters"]}
    assert by == {"p": {"total": 2, "by_logger": {"product.sdk": 2}}, "b": {"total": 0, "by_logger": {}}}
    assert [r.get("warnings") for r in h.timings] == [2, None]
    assert "secret" not in json.dumps(rep) and "secret" not in json.dumps(h.timings)
    assert not any(isinstance(x, logging.Handler) and type(x).__name__ == "_WarningCounter" for x in logging.getLogger().handlers)
