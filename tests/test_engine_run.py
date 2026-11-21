"""Run engine: run files, determinism, resume, refusal, rebuild, floors and adapter errors."""

from __future__ import annotations

import json
import math
import re

import pytest

from memory_bench.adapters import get_adapter
from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import ACTIONS
from memory_bench.deciders.base import PARSE_ERROR, Decider, DecisionInput
from memory_bench.deciders.mock import MockNoisyDecider, MockOracleDecider
from memory_bench.engine import ResumeMismatch, RunConfig, rebuild_summary, run
from memory_bench.scoring.metrics import ROW_KEY

N, R = 20, 3
ADAPTERS = ["bm25", "recent"]
ROW_FIELDS = {"case_id": str, "track": str, "mechanism": str, "a_star": str, "decider": str, "kind": str,
              "system": str, "config_label": str, "budget": (int, type(None)), "repeat": int, "action": str,
              "view_tokens": int, "view_units": int, "view_sha256": str}
MANIFEST_KEYS = {"run_id", "created", "state", "grade", "protocol", "cases", "deciders", "adapters", "budgets",
                 "repeats", "code", "files"}
SPEND_KEYS = {"cap_usd", "total_usd", "calls", "input_tokens", "output_tokens", "by_stage", "by_decider",
              "by_adapter"}
HEALTH_KEYS = {"name", "product_version", "ok", "cases_done", "errors", "last_error", "ingest_seconds_mean",
               "retrieve_seconds_mean", "last_ok_at"}


@pytest.fixture(scope="module")
def cases():
    return dev_cases()[:N]


def cfg(out, cases, decider=None, **kw):
    kw.setdefault("adapters", list(ADAPTERS))
    kw.setdefault("budgets", [1000])
    kw.setdefault("repeats", R)
    kw.setdefault("boot_reps", 200)
    return RunConfig(out_dir=out, cases=cases, decider=decider or MockOracleDecider(cases), **kw)


def rows_of(d):
    return [json.loads(x) for x in (d / "metrics.jsonl").read_text().splitlines()]


@pytest.fixture(scope="module")
def oracle_run(tmp_path_factory, cases):
    d = tmp_path_factory.mktemp("oracle")
    manifest = run(cfg(d, cases))
    return d, manifest


def test_run_files_and_schemas(oracle_run, cases):
    d, manifest = oracle_run
    for name in ("metrics.jsonl", "summary.json", "spend.json", "health.json", "timings.jsonl", "manifest.json"):
        assert (d / name).exists(), name
    m = json.loads((d / "manifest.json").read_text())
    assert m == manifest
    assert MANIFEST_KEYS <= set(m)
    assert m["state"] == "complete" and m["grade"] == "exploratory" and m["incomplete_cells"] == []
    assert m["cases"]["n"] == N and re.fullmatch(r"[0-9a-f]{64}", m["cases"]["sha256"])
    assert m["deciders"] == [{"id": "mock-oracle", "model": "mock", "params": {}}]
    assert [a["name"] for a in m["adapters"]] == ["full", *ADAPTERS]
    assert all(set(a) == {"name", "product_version", "config_label", "config_sha256"} for a in m["adapters"])
    assert m["budgets"] == [1000] and m["repeats"] == R
    assert set(m["code"]) == {"version", "git_commit"}
    import hashlib
    assert set(m["files"]) == {"metrics.jsonl", "summary.json", "spend.json", "health.json", "timings.jsonl"}
    for name, sha in m["files"].items():
        assert hashlib.sha256((d / name).read_bytes()).hexdigest() == sha

    spend = json.loads((d / "spend.json").read_text())
    assert set(spend) == SPEND_KEYS and spend["cap_usd"] == 0.0
    assert spend["total_usd"] == 0 and spend["calls"] == 0 and spend["input_tokens"] == 0

    health = json.loads((d / "health.json").read_text())
    assert [h["name"] for h in health["adapters"]] == ["full", *ADAPTERS]
    for h in health["adapters"]:
        assert HEALTH_KEYS <= set(h) and h["ok"] and h["errors"] == 0 and h["cases_done"] == N

    timings = [json.loads(x) for x in (d / "timings.jsonl").read_text().splitlines()]
    assert timings and all(isinstance(t["seconds"], float) and t["call"] in ("reset", "ingest", "retrieve")
                           for t in timings)

    summary = json.loads((d / "summary.json").read_text())
    assert set(summary["deciders"]) == {"mock-oracle"}


def test_rows_count_keys_and_fields(oracle_run, cases):
    d, _ = oracle_run
    raw = (d / "metrics.jsonl").read_text()
    rows = rows_of(d)
    assert len(rows) == N * (1 + R + len(ADAPTERS) * 1 * R)
    keys = [tuple(r[k] for k in ROW_KEY) for r in rows]
    assert len(set(keys)) == len(keys)
    for line, r in zip(raw.splitlines(), rows):
        assert line == json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        assert set(r) == set(ROW_FIELDS)
        for k, t in ROW_FIELDS.items():
            assert isinstance(r[k], t), (k, r[k])
            if isinstance(r[k], str):
                assert not re.search(r"\s", r[k]), (k, r[k])
        assert r["action"] in (*ACTIONS, PARSE_ERROR)
        assert re.fullmatch(r"[0-9a-f]{64}", r["view_sha256"])
        if r["kind"] == "gate":
            assert r["repeat"] == 0 and r["system"] == "full" and r["budget"] is None
        elif r["kind"] == "floor":
            assert 1 <= r["repeat"] <= R and r["system"] == "full" and r["budget"] is None
        else:
            assert 1 <= r["repeat"] <= R and r["system"] in ADAPTERS and r["budget"] == 1000
            assert r["view_tokens"] <= 1000
    # canonical order: case order, gate, floor, adapters in config order, repeats ascending
    order = [c.case_id for c in cases]
    assert [r["case_id"] for r in rows[:: 1 + R + 2 * R]] == order
    first = rows[: 1 + R + 2 * R]
    assert [(r["kind"], r["system"], r["repeat"]) for r in first] == (
        [("gate", "full", 0)] + [("floor", "full", i) for i in (1, 2, 3)]
        + [("view", "bm25", i) for i in (1, 2, 3)] + [("view", "recent", i) for i in (1, 2, 3)])


def test_two_fresh_runs_are_byte_identical(oracle_run, cases, tmp_path):
    d, _ = oracle_run
    run(cfg(tmp_path / "b", cases))
    for name in ("metrics.jsonl", "summary.json"):
        assert (d / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


class Boom(RuntimeError):
    pass


class DiesOnCase:
    """Delegates to a decider and raises on the first decide call of the k-th distinct case."""

    def __init__(self, inner: Decider, k: int):
        self.inner, self.k = inner, k
        self.id, self.model, self.params = inner.id, inner.model, inner.params
        self.seen: list[str] = []

    def decide(self, inp: DecisionInput) -> str:
        if inp.case_id not in self.seen:
            self.seen.append(inp.case_id)
        if len(self.seen) >= self.k:
            raise Boom(f"killed at case {inp.case_id}")
        return self.inner.decide(inp)


def test_interrupted_run_resumes_byte_identical(oracle_run, cases, tmp_path):
    d, _ = oracle_run
    out = tmp_path / "resume"
    with pytest.raises(Boom):
        run(cfg(out, cases, decider=DiesOnCase(MockOracleDecider(cases), 8)))
    m = json.loads((out / "manifest.json").read_text())
    assert m["state"] == "failed" and "killed at case" in m["error"]
    assert len(rows_of(out)) == 7 * (1 + R + 2 * R)
    assert not (out / "summary.json").exists()

    calls = []

    class Counting(MockOracleDecider):
        def decide(self, inp):
            calls.append(inp.case_id)
            return super().decide(inp)

    m2 = run(cfg(out, cases, decider=Counting(cases)))
    assert m2["state"] == "complete" and m2["attempts"] == 2
    assert set(calls) == {c.case_id for c in cases[7:]}
    for name in ("metrics.jsonl", "summary.json"):
        assert (out / name).read_bytes() == (d / name).read_bytes(), name


def test_resume_out_of_order_is_rewritten_canonically(oracle_run, cases, tmp_path):
    d, _ = oracle_run
    out = tmp_path / "shuffled"
    run(cfg(out, cases))
    lines = (out / "metrics.jsonl").read_text().splitlines(keepends=True)
    keep = [x for i, x in enumerate(lines) if i % 5 != 0]  # drop some rows across many cases
    (out / "metrics.jsonl").write_text("".join(reversed(keep)))
    run(cfg(out, cases))
    assert (out / "metrics.jsonl").read_bytes() == (d / "metrics.jsonl").read_bytes()
    assert (out / "summary.json").read_bytes() == (d / "summary.json").read_bytes()


def test_resume_with_different_config_is_refused(oracle_run, cases, tmp_path):
    out = tmp_path / "refuse"
    run(cfg(out, cases[:3]))
    before = (out / "metrics.jsonl").read_bytes()
    with pytest.raises(ResumeMismatch, match="budgets"):
        run(cfg(out, cases[:3], budgets=[500, 1000]))
    with pytest.raises(ResumeMismatch, match="repeats"):
        run(cfg(out, cases[:3], repeats=2))
    with pytest.raises(ResumeMismatch, match="cases sha256"):
        run(cfg(out, cases[:4]))
    with pytest.raises(ResumeMismatch, match="decider id"):
        run(cfg(out, cases[:3], decider=MockNoisyDecider(cases, 0.1)))
    with pytest.raises(ResumeMismatch, match="adapters"):
        run(cfg(out, cases[:3], adapters=["recent", "bm25"]))
    assert (out / "metrics.jsonl").read_bytes() == before
    assert json.loads((out / "manifest.json").read_text())["state"] == "complete"


def test_rebuild_summary_restores_bytes(oracle_run):
    d, _ = oracle_run
    before = (d / "summary.json").read_bytes()
    (d / "summary.json").unlink()
    rebuild_summary(d)
    assert (d / "summary.json").read_bytes() == before


def test_oracle_floor_zero_gate_one(oracle_run):
    d, _ = oracle_run
    s = json.loads((d / "summary.json").read_text())["deciders"]["mock-oracle"]
    assert s["validity"]["overall"]["accuracy"] == 1.0 and s["n_gated"] == N
    assert s["floor"]["wrong_commit"]["value"] == 0.0
    assert set(s["cells"]) == {"bm25/default@1000", "recent/default@1000"}


def test_noisy_floor_above_zero(cases, tmp_path):
    run(cfg(tmp_path, cases, decider=MockNoisyDecider(cases, flip_rate=0.2, seed=0)))
    s = json.loads((tmp_path / "summary.json").read_text())["deciders"]["mock-noisy"]
    assert s["floor"]["wrong_commit"]["value"] > 0
    h = s["cells"]["bm25/default@1000"]["headline"]
    assert math.isfinite(h["value"])
    lo, hi = h["ci95"]
    assert math.isfinite(lo) and math.isfinite(hi) and lo <= h["value"] <= hi


def test_noisy_is_deterministic(cases):
    a, b = MockNoisyDecider(cases, 0.5, seed=3), MockNoisyDecider(cases, 0.5, seed=3)
    inp = DecisionInput(cases[0].case_id, cases[0].query_text, "x", [], 1)
    assert a.decide(inp) == b.decide(inp)


class FlakyAdapter:
    """bm25 that raises on one case."""

    name = "flaky"
    product_version = "test"
    config_label = "default"
    reference = False

    def __init__(self, bad_case: str):
        self.inner = get_adapter("bm25")
        self.bad = bad_case
        self.ns = None

    def reset(self, namespace):
        self.ns = namespace
        self.inner.reset(namespace)

    def ingest(self, records):
        self.inner.ingest(records)

    def retrieve(self, query, budget_tokens):
        if self.ns == self.bad:
            raise ConnectionError("product timed out")
        return self.inner.retrieve(query, budget_tokens)

    def close(self):
        self.inner.close()


def test_adapter_error_is_recorded_and_run_continues(cases, tmp_path):
    bad = cases[4].case_id
    m = run(cfg(tmp_path, cases, adapters=[FlakyAdapter(bad), "recent"]))
    assert m["state"] == "complete"
    assert m["incomplete_cells"] == [{"cell": "flaky/default@1000", "missing_cases": [bad]}]
    health = {h["name"]: h for h in json.loads((tmp_path / "health.json").read_text())["adapters"]}
    assert health["flaky"]["errors"] == 1 and not health["flaky"]["ok"]
    assert health["flaky"]["last_error"]["case_id"] == bad
    assert "product timed out" in health["flaky"]["last_error"]["error"]
    assert health["flaky"]["cases_done"] == N - 1 and health["recent"]["cases_done"] == N
    rows = rows_of(tmp_path)
    assert len(rows) == N * (1 + R + 2 * R) - R
    assert not [r for r in rows if r["case_id"] == bad and r["system"] == "flaky"]
    assert len([r for r in rows if r["case_id"] == bad and r["system"] == "recent"]) == R


def test_decider_exception_is_not_swallowed(cases, tmp_path):
    class Bad(MockOracleDecider):
        def decide(self, inp):
            return "MAYBE"

    with pytest.raises(RuntimeError, match="MAYBE"):
        run(cfg(tmp_path, cases[:2], decider=Bad(cases)))
    assert json.loads((tmp_path / "manifest.json").read_text())["state"] == "failed"


def test_full_in_adapters_is_refused(cases, tmp_path):
    with pytest.raises(RuntimeError, match="full"):
        run(cfg(tmp_path, cases[:2], adapters=["full", "bm25"]))
