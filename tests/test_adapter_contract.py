"""One suite any adapter must pass. Run here over the three baselines."""

from __future__ import annotations

import pytest

from memory_bench.adapters import ADAPTERS, get_adapter
from memory_bench.adapters.base import Record, Retrieved, count_tokens, enforce_budget, records_of
from memory_bench.cases.sample import dev_cases

NAMES = ["full", "bm25", "recent"]


@pytest.fixture(scope="module")
def case():
    return dev_cases()[0]


@pytest.fixture(scope="module")
def other_case():
    return dev_cases()[1]


@pytest.fixture(params=NAMES)
def adapter(request):
    a = get_adapter(request.param)
    yield a
    a.close()


def test_registry():
    products = sorted(n for n, cls in ADAPTERS.items() if getattr(cls, "needs_proxy", False))
    assert products == ["graphiti", "langmem", "mem0"]  # product adapters need the proxy and are not built by a bare name
    assert sorted(ADAPTERS) == sorted([*NAMES, "empty", *products])  # empty: the exploratory baseline (Day 14)
    for n in [*NAMES, "empty"]:
        a = get_adapter(n)
        assert a.name == n and a.product_version == "builtin" and a.config_label == "default"
        assert a.reference is (n == "full")
    with pytest.raises(ValueError):
        get_adapter("nope")


def test_reset_isolates_namespaces(adapter, case, other_case):
    adapter.reset("A")
    adapter.ingest(records_of(case))
    assert adapter.retrieve(case.query_text, 2000).text
    adapter.reset("B")
    got = adapter.retrieve(case.query_text, 2000)
    assert got.text == "" and got.record_ids == [] and got.units == []
    adapter.ingest(records_of(other_case))
    ids_b = {r.id for r in records_of(other_case)}
    got = adapter.retrieve(other_case.query_text, 2000)
    assert set(got.record_ids) <= ids_b
    assert all(u in {r.text for r in records_of(other_case)} for u in got.units)


@pytest.mark.parametrize("budget", [50, 500, 1000, 2000])
def test_budget_after_enforce(adapter, case, budget):
    adapter.reset(case.case_id)
    adapter.ingest(records_of(case))
    out = enforce_budget(adapter.retrieve(case.query_text, budget), budget, reference=adapter.reference)
    if adapter.reference:
        assert out.text == "\n".join(r.text for r in records_of(case))
    else:
        assert count_tokens(out.text.split("\n") if out.text else []) <= budget
        assert count_tokens(out.units) <= budget


def test_enforce_budget_cuts_oversize_text():
    r = Retrieved(text="x " * 500, record_ids=[], units=["x " * 500])
    out = enforce_budget(r, 10)
    assert count_tokens([out.text]) <= 10
    assert enforce_budget(r, 10, reference=True) is r


def test_ids_and_units_verbatim(adapter, case):
    recs = records_of(case)
    adapter.reset(case.case_id)
    adapter.ingest(recs)
    got = adapter.retrieve(case.query_text, 1000)
    assert set(got.record_ids) <= {r.id for r in recs}
    assert len(got.record_ids) == len(got.units) == len(set(got.record_ids))
    assert "\n".join(got.units) == got.text
    by_id = {r.id: r.text for r in recs}
    assert got.units == [by_id[i] for i in got.record_ids]
    order = {r.id: i for i, r in enumerate(recs)}
    assert got.record_ids == sorted(got.record_ids, key=order.__getitem__)


def test_retrieve_before_ingest_is_empty(adapter):
    got = adapter.retrieve("anything", 500)
    assert got.text == "" and got.record_ids == [] and got.units == []
    adapter.reset("fresh")
    assert adapter.retrieve("anything", 500).text == ""


def test_determinism(case):
    for n in NAMES:
        outs = []
        for _ in range(2):
            a = get_adapter(n)
            a.reset(case.case_id)
            a.ingest(records_of(case))
            r = a.retrieve(case.query_text, 500)
            outs.append((r.text, r.record_ids, r.units))
            a.close()
        assert outs[0] == outs[1]


def test_close_idempotent(adapter, case):
    adapter.reset("x")
    adapter.ingest(records_of(case))
    adapter.close()
    adapter.close()
    assert adapter.retrieve("q", 500).text == ""


def test_record_is_frozen():
    r = Record(id="e1", date="2011-01-01", text="e1 2011-01-01 hi")
    with pytest.raises(Exception):
        r.id = "z"  # type: ignore[misc]
