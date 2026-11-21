"""The view stage (docs/view-stage.md section 5): one ingest per case, parallel ingest, the view store, resume,
store reuse rules, product errors, the cap, warning attribution, released files and builtin-only runs.
A fake upstream on 127.0.0.1 and the test-only product adapter; no network, no key."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time

import pytest
from proxy_fakes import FakeUpstream, ProbeProductAdapter, memory_side

from memory_bench import adapters as adapter_registry
from memory_bench.adapters.base import Retrieved, enforce_budget
from memory_bench.cases.sample import dev_cases
from memory_bench.cli import main
from memory_bench.deciders.mock import MockOracleDecider
from memory_bench.engine import RunConfig, RunError, run
from memory_bench.engine.views import ThreadWarnings, ViewStore
from memory_bench.gateway import MEMORY_CALLS
from memory_bench.submit.textfree import RELEASED_FILES, scan_run

N = 4
BUDGETS = [300, 1000]


@pytest.fixture(scope="module")
def cases():
    return dev_cases()[:N]


@pytest.fixture
def upstream():
    with FakeUpstream() as up:
        yield up


@pytest.fixture
def slow_upstream():
    with FakeUpstream(delay=0.05) as up:
        yield up


class Counting(ProbeProductAdapter):
    """The probe adapter, logging every call with its instance, thread, case, budget and time."""

    log: list[dict] = []
    lock = threading.Lock()

    def _note(self, method: str, t0: float, budget: int | None = None) -> None:
        with Counting.lock:
            Counting.log.append({"m": method, "inst": id(self), "thread": threading.get_ident(),
                                 "case": self.scope.case_id if self.scope is not None else None, "budget": budget,
                                 "t0": t0, "t1": time.perf_counter()})

    def reset(self, namespace):
        t0 = time.perf_counter()
        super().reset(namespace)
        self._note("reset", t0)

    def ingest(self, records):
        t0 = time.perf_counter()
        super().ingest(records)
        self._note("ingest", t0)

    def retrieve(self, query, budget_tokens):
        t0 = time.perf_counter()
        got = super().retrieve(query, budget_tokens)
        self._note("retrieve", t0, budget_tokens)
        return got

    def close(self):
        t0 = time.perf_counter()
        self._note("close", t0)
        super().close()


@pytest.fixture(autouse=True)
def clear_log():
    Counting.log.clear()
    yield
    Counting.log.clear()


def calls_of(method: str) -> list[dict]:
    with Counting.lock:
        return [x for x in Counting.log if x["m"] == method]


def adapter_calls() -> int:
    return len([x for x in Counting.log if x["m"] in ("reset", "ingest", "retrieve")])


def lines(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def mrun(tmp_path, name, cases, up, *, adapters=None, workers=1, cap=5.0, decider=None, cache="cache.sqlite",
         budgets=None, **ms_kw):
    return run(RunConfig(out_dir=tmp_path / name, cases=cases, decider=decider or MockOracleDecider(cases),
                         adapters=adapters or ["bm25", Counting], budgets=budgets or BUDGETS, repeats=2,
                         boot_reps=30, cap_usd=cap, memory_side=memory_side(up, **ms_kw),
                         proxy_cache=tmp_path / cache, ingest_workers=workers))


def views(d):
    return lines(d / "private" / "views.jsonl")


def stored(line) -> Retrieved:
    return Retrieved(text=line["text"], record_ids=line["record_ids"], units=line["units"])


class DiesAtFirstDecide(MockOracleDecider):
    """Fails on the first decide call: the view stage has finished, no metric row is written."""

    def decide(self, inp):
        raise RuntimeError("decider killed")


# -- one ingest and one retrieve per case ------------------------------------------------------------------

def test_one_ingest_and_one_retrieve_per_case(tmp_path, cases, upstream):
    m = mrun(tmp_path, "r", cases, upstream)
    d = tmp_path / "r"
    assert m["state"] == "complete" and not m["incomplete_cells"]
    for method in ("reset", "ingest", "retrieve"):
        assert sorted(x["case"] for x in calls_of(method)) == sorted(c.case_id for c in cases), method
    assert {x["budget"] for x in calls_of("retrieve")} == {max(BUDGETS)}  # the adapter is asked at max(budgets)
    store = views(d)
    assert sorted(x["case_id"] for x in store) == sorted(c.case_id for c in cases)
    rows = [r for r in lines(d / "metrics.jsonl") if r["system"] == "probe"]
    cut_differs = False
    for line in store:
        assert line["system"] == "probe" and line["config_label"] == "default" and line["memory_side_id"] == "test-mem"
        assert set(line["usage"]) == {"model_calls", "input_tokens", "output_tokens", "embed_calls"}
        v = stored(line)
        shas = {}
        for b in BUDGETS:
            want = enforce_budget(v, b)
            got = {(r["view_sha256"], r["view_units"]) for r in rows if r["case_id"] == line["case_id"]
                   and r["budget"] == b}
            assert got == {(hashlib.sha256(want.text.encode()).hexdigest(), len(want.units))}
            shas[b] = want.text
        cut_differs |= shas[300] != shas[1000]
    assert cut_differs  # the smaller budget really is a cut of the same stored view
    timings = [t for t in lines(d / "timings.jsonl") if t["system"] == "probe"]
    assert sorted((t["case_id"], t["call"]) for t in timings) == sorted(
        (c.case_id, call) for c in cases for call in ("reset", "ingest", "retrieve"))
    assert all(t["budget"] is None and t["warnings"] == 0 for t in timings)
    # spend.json counts the stored lines' usage
    spend = json.loads((d / "spend.json").read_text())
    assert spend["by_adapter"]["probe"]["calls"] == sum(x["usage"]["model_calls"] + x["usage"]["embed_calls"]
                                                         for x in store) == N * 2


# -- parallel ingest ------------------------------------------------------------------------------------------

def test_ingest_workers_one_and_four_are_byte_identical(tmp_path, slow_upstream):
    cs = dev_cases()[:6]
    m1 = mrun(tmp_path, "w1", cs, slow_upstream, workers=1, cache="a.sqlite")
    log1 = list(Counting.log)
    Counting.log.clear()
    peak_before = slow_upstream.peak
    m4 = mrun(tmp_path, "w4", cs, slow_upstream, workers=4, cache="b.sqlite")
    log4 = list(Counting.log)
    assert m1["state"] == m4["state"] == "complete"
    for name in ("metrics.jsonl", "summary.json"):
        assert (tmp_path / "w1" / name).read_bytes() == (tmp_path / "w4" / name).read_bytes(), name
    key = lambda x: (x["case_id"], x["text"], x["units"], x["usage"])  # noqa: E731
    assert sorted(map(key, views(tmp_path / "w1"))) == sorted(map(key, views(tmp_path / "w4")))

    # with 4 workers, at least two items overlap in time
    spans = {}
    for x in log4:
        if x["m"] in ("reset", "ingest", "retrieve"):
            s = spans.setdefault(x["case"], [x["t0"], x["t1"]])
            s[0], s[1] = min(s[0], x["t0"]), max(s[1], x["t1"])
    ivs = sorted(spans.values())
    assert any(a[1] > b[0] for a, b in zip(ivs, ivs[1:]))
    assert slow_upstream.peak > 1 and peak_before == 1

    # no adapter instance is used by two threads (close included); each worker owns its own instances
    for log, n_threads in ((log1, 1), (log4, 4)):
        workers_inst = {x["inst"] for x in log if x["m"] == "reset"}  # not the run's own (never reset) instance
        threads_of: dict[int, set[int]] = {}
        for x in log:
            if x["inst"] in workers_inst:
                threads_of.setdefault(x["inst"], set()).add(x["thread"])
        assert all(len(t) == 1 for t in threads_of.values())
        assert len({next(iter(t)) for t in threads_of.values()}) == n_threads
        assert sum(x["m"] == "close" and x["inst"] in workers_inst for x in log) == len(threads_of)  # each closed once
    # with one worker the stage runs on the calling thread
    assert {x["thread"] for x in log1} == {threading.get_ident()}
    h4 = {h["name"]: h for h in json.loads((tmp_path / "w4" / "health.json").read_text())["adapters"]}
    assert h4["probe"]["cases_done"] == 6 and h4["probe"]["errors"] == 0


def test_ingest_workers_bounds_and_cli(tmp_path, upstream, monkeypatch, capsys):
    cs = dev_cases()[:2]
    for bad in (0, 9):
        with pytest.raises(RunError, match="ingest_workers"):
            run(RunConfig(out_dir=tmp_path / f"b{bad}", cases=cs, decider=MockOracleDecider(cs), ingest_workers=bad))
        assert not (tmp_path / f"b{bad}").exists()
    monkeypatch.setitem(adapter_registry.ADAPTERS, "probe", ProbeProductAdapter)
    cfg_path = tmp_path / "mem.json"
    cfg_path.write_text(json.dumps(memory_side(upstream)))
    base = ["run", "--mock", "--n", "3", "--adapters", "bm25,probe", "--repeats", "1", "--boot-reps", "20",
            "--cap-usd", "1", "--memory-side", str(cfg_path), "--cache", str(tmp_path / "c.sqlite")]
    assert main(base + ["--ingest-workers", "9", "--out", str(tmp_path / "x")]) == 2
    assert "--ingest-workers must be between 1 and 8" in capsys.readouterr().err and not (tmp_path / "x").exists()
    assert main(base + ["--out", str(tmp_path / "a")]) == 0
    assert main(base + ["--ingest-workers", "3", "--out", str(tmp_path / "b")]) == 0
    for name in ("metrics.jsonl", "summary.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


# -- resume -----------------------------------------------------------------------------------------------------

def test_resume_makes_no_adapter_or_memory_side_call(tmp_path, cases, upstream):
    assert mrun(tmp_path, "ref", cases, upstream)["state"] == "complete"
    ref = tmp_path / "ref"

    # a finished run: a second run on the folder calls nothing
    Counting.log.clear()
    n_mem, n_up = len(lines(ref / MEMORY_CALLS)), upstream.count()
    before = (ref / "metrics.jsonl").read_bytes()
    m = mrun(tmp_path, "ref", cases, upstream)
    assert m["state"] == "complete" and m["attempts"] == 2
    assert adapter_calls() == 0 and len(lines(ref / MEMORY_CALLS)) == n_mem and upstream.count() == n_up
    assert (ref / "metrics.jsonl").read_bytes() == before

    # a run that stopped after the stage: the resume reads every view from the store
    with pytest.raises(RuntimeError, match="decider killed"):
        mrun(tmp_path, "cut", cases, upstream, decider=DiesAtFirstDecide(cases), cache="other.sqlite")
    cut = tmp_path / "cut"
    assert len(views(cut)) == N and not lines(cut / "metrics.jsonl")
    Counting.log.clear()
    n_mem, n_up = len(lines(cut / MEMORY_CALLS)), upstream.count()
    m = mrun(tmp_path, "cut", cases, upstream, cache="other.sqlite")
    assert m["state"] == "complete" and not m["incomplete_cells"]
    assert adapter_calls() == 0 and len(lines(cut / MEMORY_CALLS)) == n_mem and upstream.count() == n_up
    for name in ("metrics.jsonl", "summary.json"):
        assert (cut / name).read_bytes() == (ref / name).read_bytes(), name
    spend = json.loads((cut / "spend.json").read_text())  # reused lines count in spend.json
    assert spend["by_adapter"]["probe"]["calls"] == N * 2


@pytest.mark.parametrize("field,value", [("config_sha256", "0" * 64), ("memory_side_id", "another-memory-side"),
                                         ("product_version", "test-1")])
def test_a_store_line_of_another_config_is_not_reused(tmp_path, cases, upstream, field, value):
    with pytest.raises(RuntimeError, match="decider killed"):
        mrun(tmp_path, "r", cases, upstream, decider=DiesAtFirstDecide(cases))
    p = tmp_path / "r" / "private" / "views.jsonl"
    old = views(tmp_path / "r")
    p.write_text("".join(json.dumps({**x, field: value}) + "\n" for x in old))
    Counting.log.clear()
    m = mrun(tmp_path, "r", cases, upstream)
    assert m["state"] == "complete" and not m["incomplete_cells"]
    assert sorted(x["case"] for x in calls_of("reset")) == sorted(c.case_id for c in cases)  # all computed again
    new = views(tmp_path / "r")
    assert len(new) == 2 * N and [x[field] for x in new[N:]] != [value] * N  # appended, the old lines kept


def test_view_store_rules(tmp_path):
    p = tmp_path / "private" / "views.jsonl"
    expect = {("p", "default"): ("sha-a", "1.0")}

    def line(case, text, **kw):
        return {"case_id": case, "system": "p", "config_label": "default", "config_sha256": "sha-a",
                "memory_side_id": "ms", "product_version": "1.0", "text": text, "units": [text], "record_ids": [],
                "usage": {"model_calls": 1, "input_tokens": 2, "output_tokens": 3, "embed_calls": 4}, **kw}

    s = ViewStore(p, expect, "ms")
    assert not p.parent.exists()  # made on the first append
    s.append(line("c1", "old"))
    s.append(line("c1", "new"))
    s.append(line("c2", "other config", config_sha256="sha-b"))
    s.append(line("c3", "other system", system="q"))
    with p.open("a") as fh:
        fh.write(json.dumps(line("c4", "torn"))[:40])  # a partial last line
    s2 = ViewStore(p, expect, "ms")
    assert s2.get("c1", "p", "default")["text"] == "new"  # the newest matching line wins
    assert s2.get("c2", "p", "default") is None and s2.get("c3", "q", "default") is None
    assert s2.get("c4", "p", "default") is None
    assert ViewStore(p, expect, "another")._lines == {}
    s2.append(line("c5", "after the torn line"))
    s3 = ViewStore(p, expect, "ms")
    assert s3.get("c5", "p", "default")["text"] == "after the torn line" and s3.get("c1", "p", "default")
    assert ViewStore.retrieved(s3.get("c1", "p", "default")).usage.embed_calls == 4


# -- product errors -----------------------------------------------------------------------------------------------

class FailsOn(Counting):
    bad: str = ""

    def retrieve(self, query, budget_tokens):
        if self.scope.case_id == FailsOn.bad:
            raise ConnectionError("product timed out")
        return super().retrieve(query, budget_tokens)


@pytest.mark.parametrize("workers", [1, 3])
def test_a_product_error_on_one_case(tmp_path, cases, upstream, workers):
    FailsOn.bad = bad = cases[1].case_id
    m = mrun(tmp_path, "r", cases, upstream, adapters=["bm25", FailsOn], workers=workers)
    d = tmp_path / "r"
    assert m["state"] == "complete"
    assert m["incomplete_cells"] == [{"cell": f"probe/default@{b}", "missing_cases": [bad]} for b in BUDGETS]
    rows = lines(d / "metrics.jsonl")
    assert not [r for r in rows if r["case_id"] == bad and r["system"] == "probe"]
    assert len([r for r in rows if r["case_id"] == bad and r["system"] == "bm25"]) == 2 * len(BUDGETS)
    assert len([r for r in rows if r["system"] == "probe"]) == (N - 1) * 2 * len(BUDGETS)
    health = {h["name"]: h for h in json.loads((d / "health.json").read_text())["adapters"]}
    assert health["probe"]["errors"] == 1 and not health["probe"]["ok"] and health["probe"]["cases_done"] == N - 1
    assert health["probe"]["last_error"]["case_id"] == bad and "product timed out" in health["probe"]["last_error"]["error"]
    assert sorted(x["case_id"] for x in views(d)) == sorted(c.case_id for c in cases if c.case_id != bad)


# -- the cap ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("workers", [1, 4])
def test_cap_mid_stage_then_resume(tmp_path, cases, slow_upstream, workers):
    assert mrun(tmp_path, "ref", cases, slow_upstream, cache="a.sqlite")["state"] == "complete"
    total = json.loads((tmp_path / "ref" / "spend.json").read_text())["memory_side_usd"]
    Counting.log.clear()

    m1 = mrun(tmp_path, "c", cases, slow_upstream, cap=total / 2, workers=workers, cache="b.sqlite")
    d = tmp_path / "c"
    assert m1["state"] == "stopped_at_cap" and "CapReached" in m1["error"] and m1["incomplete_cells"]
    done = {x["case_id"] for x in views(d)}
    assert 1 <= len(done) < N  # finished views are kept
    assert json.loads((d / "spend.json").read_text())["memory_side_usd"] <= total / 2 + 1e-9
    health = {h["name"]: h for h in json.loads((d / "health.json").read_text())["adapters"]}
    assert health["probe"]["errors"] == 0  # a refusal at the cap is a stop, not a product error
    assert not (d / "summary.json").exists()

    Counting.log.clear()
    m2 = mrun(tmp_path, "c", cases, slow_upstream, cap=5.0, workers=workers, cache="b.sqlite")
    assert m2["state"] == "complete" and not m2["incomplete_cells"] and m2["attempts"] == 2
    assert {x["case"] for x in calls_of("reset")} == {c.case_id for c in cases} - done  # no view repeated
    assert len(calls_of("reset")) == N - len(done)
    for name in ("metrics.jsonl", "summary.json"):
        assert (d / name).read_bytes() == (tmp_path / "ref" / name).read_bytes(), name
    assert json.loads((d / "spend.json").read_text())["memory_side_usd"] == pytest.approx(total, abs=1e-6)


# -- warnings -------------------------------------------------------------------------------------------------------

class Noisy(Counting):
    """Logs k warnings during ingest on case k (k = 1..N), spread out so parallel items interleave."""

    order: dict[str, int] = {}

    def ingest(self, records):
        super().ingest(records)
        k = Noisy.order[self.scope.case_id]
        for _ in range(k):
            logging.getLogger("probe.sdk").warning("dropped a memory: %s", records[0].text)
            logging.getLogger("probe.sdk").info("not counted")
            time.sleep(0.01)


def test_warnings_are_attributed_to_the_right_item_under_parallel_work(tmp_path, slow_upstream):
    cs = dev_cases()[:6]
    Noisy.order = {c.case_id: i + 1 for i, c in enumerate(cs)}
    m = mrun(tmp_path, "r", cs, slow_upstream, adapters=["bm25", Noisy], workers=4)
    d = tmp_path / "r"
    assert m["state"] == "complete"
    # the items really overlapped while they logged
    spans = sorted([x["t0"], x["t1"]] for x in calls_of("ingest"))
    assert any(a[1] > b[0] for a, b in zip(spans, spans[1:]))
    rows = [t for t in lines(d / "timings.jsonl") if t["system"] == "probe"]
    for t in rows:
        assert t["warnings"] == (Noisy.order[t["case_id"]] if t["call"] == "ingest" else 0), t
    for x in views(d):
        assert x["warnings"] == {"total": Noisy.order[x["case_id"]], "by_logger": {"probe.sdk": Noisy.order[x["case_id"]]}}
    rep = json.loads((d / "health.json").read_text())
    by = {h["name"]: h["product_warnings"] for h in rep["adapters"]}
    assert by["probe"] == {"total": sum(Noisy.order.values()), "by_logger": {"probe.sdk": sum(Noisy.order.values())}}
    assert by["bm25"]["total"] == 0 and "unattributed_warnings" not in rep
    assert "dropped a memory" not in (d / "health.json").read_text() + (d / "timings.jsonl").read_text()


def test_thread_warnings_rule():
    """A record from a thread that runs no item goes to the one item in flight, else it is unattributed."""
    w = ThreadWarnings()
    log = logging.getLogger("tw.test")
    log.addHandler(w)
    log.propagate = False
    try:
        mine: dict[str, int] = {}
        w.enter(mine)
        th = threading.Thread(target=lambda: log.warning("from a helper thread"))
        th.start()
        th.join()
        assert mine == {"tw.test": 1}

        other: dict[str, int] = {}
        ready, release = threading.Event(), threading.Event()

        def item():
            w.enter(other)
            log.warning("from the other item")
            ready.set()
            release.wait()
            w.leave()

        t2 = threading.Thread(target=item)
        t2.start()
        ready.wait()
        log.warning("from my own thread")
        th = threading.Thread(target=lambda: log.warning("helper, two items in flight"))
        th.start()
        th.join()
        release.set()
        t2.join()
        w.leave()
        assert mine == {"tw.test": 2} and other == {"tw.test": 1} and w.unattributed == {"tw.test": 1}
    finally:
        log.removeHandler(w)
        log.propagate = True


# -- released files -------------------------------------------------------------------------------------------------

def test_views_stay_in_private(tmp_path, cases, upstream):
    m = mrun(tmp_path, "r", cases, upstream, workers=2)
    d = tmp_path / "r"
    assert m["state"] == "complete"
    store = views(d)
    assert len(store) == N
    texts = {u for x in store for u in x["units"] if len(u) >= 25}
    assert texts
    outside = [p for p in d.rglob("*") if p.is_file() and "private" not in p.relative_to(d).parts]
    assert {p.name for p in outside} >= set(m["files"])
    for p in outside:
        body = p.read_text(encoding="utf-8")
        assert not any(t in body for t in texts), p.name
    assert scan_run(d) == []
    assert not any("private" in name for name in m["files"]) and not any("private" in n for n in RELEASED_FILES)


# -- runs without a product adapter -------------------------------------------------------------------------------

# sha256 of results/runs/day1-mock (written by the engine before the view stage): 20 dev cases, mock oracle,
# bm25 and recent, budget 1000, 3 repeats, bootstrap seed 11 and 1000 reps.
DAY1_MOCK = {"metrics.jsonl": "48e48ab74c43e80cabe92f0a2f391a6999161382619a5cc1e87c77f4565a40f5",
             "summary.json": "b79894922400e61a6a95db13073d962b67b8dc5fb30219ef35c8d41de8869aae",
             "spend.json": "3829aa38bc047066c77a2bcce8d7a0e278fa9452431f54e39fbb4588e2311692"}


@pytest.mark.parametrize("workers", [1, 4])
def test_builtin_only_run_is_unchanged(tmp_path, workers, upstream):
    cs = dev_cases()[:20]
    kw = {"memory_side": memory_side(upstream), "proxy_cache": tmp_path / "c.sqlite"} if workers == 4 else {}
    m = run(RunConfig(out_dir=tmp_path / "r", cases=cs, decider=MockOracleDecider(cs), adapters=["bm25", "recent"],
                      budgets=[1000], repeats=3, ingest_workers=workers, **kw))
    d = tmp_path / "r"
    assert m["state"] == "complete"
    for name, sha in DAY1_MOCK.items():
        data = (d / name).read_bytes()
        if name == "summary.json":  # Day 9 adds only the grade key (docs/freeze.md section 4)
            summary = json.loads(data)
            assert summary.pop("grade") == "exploratory"
            data = json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=True).encode() + b"\n"
        assert hashlib.sha256(data).hexdigest() == sha, name
    assert not (d / "private").exists() and not (tmp_path / "c.sqlite").exists()
    assert sorted(p.name for p in d.iterdir()) == sorted(["manifest.json", "metrics.jsonl", "summary.json",
                                                          "spend.json", "health.json", "timings.jsonl"])
    assert "unattributed_warnings" not in json.loads((d / "health.json").read_text())
    assert all("warnings" not in t for t in lines(d / "timings.jsonl"))
