"""Check R2: the adapter path reproduces the legacy views and the old run's view stats."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_bench.adapters import get_adapter
from memory_bench.adapters.base import count_tokens, enforce_budget, records_of
from memory_bench.adapters.legacy import get_system
from memory_bench.cases.sample import dev_cases

FIX = Path(__file__).parent / "fixtures"
BUDGETS = [500, 1000, 2000]
PAIRS = [("recent", "recent"), ("bm25", "rag-bm25")]  # (adapter, legacy/fixture system)


@pytest.fixture(scope="module")
def cases():
    cs = dev_cases()
    assert len(cs) == 300
    return cs


@pytest.fixture(scope="module")
def fixture_rows():
    rows = {}
    for line in (FIX / "decisions_v3.jsonl").read_text().splitlines():
        d = json.loads(line)
        rows[(d["system"], d["budget"], d["case_id"])] = (d["view_tokens"], d["view_units"])
    return rows


@pytest.fixture(scope="module")
def summary():
    return json.loads((FIX / "summary_v3.json").read_text())


@pytest.mark.parametrize("budget", BUDGETS)
@pytest.mark.parametrize("adapter_name,legacy_name", PAIRS)
def test_r2(cases, fixture_rows, summary, adapter_name, legacy_name, budget):
    adapter = get_adapter(adapter_name)
    legacy = get_system(legacy_name)
    toks: list[int] = []
    units: list[int] = []
    for c in cases:
        adapter.reset(c.case_id)
        adapter.ingest(records_of(c))
        got = enforce_budget(adapter.retrieve(c.query_text, budget), budget, reference=adapter.reference)
        old = legacy.view(c, budget)
        # (a) same text and ids as the legacy view
        assert got.text == old.text, f"text differs at {c.case_id}"
        assert got.record_ids == old.event_ids, f"ids differ at {c.case_id}"
        # (b) per-case fixture rows
        vt, vu = count_tokens(got.units), len(got.units)
        assert (vt, vu) == fixture_rows[(legacy_name, budget, c.case_id)], f"fixture row differs at {c.case_id}"
        toks.append(vt)
        units.append(vu)
    adapter.close()
    # (c) cell means
    cell = summary["cells"][f"{legacy_name}@{budget}"]["view"]
    assert sum(toks) / len(toks) == pytest.approx(cell["mean_tokens"], abs=1e-9, rel=0)
    assert sum(units) / len(units) == pytest.approx(cell["mean_units"], abs=1e-9, rel=0)
