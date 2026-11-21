"""Check R3: the scorer reproduces the LENS v3 gated rates and intervals from the stored decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_bench.scoring import legacy_rows_to_metric_rows, summarise
from memory_bench.scoring.metrics import cell_name

FIX = Path(__file__).parent / "fixtures"
DECIDER = "haiku-5-5-lens-v3"
SUM_KEYS = ("wc", "ab", "hpe", "h", "g", "one")
# our rate -> where LENS stored it under harmful_flip
PARTS = {"harmful": (), "wrong_commit": ("wrong_commit",), "abstention": ("abstention",),
         "parse_error": ("parse_error",)}


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads((FIX / "summary_v3.json").read_text())


@pytest.fixture(scope="module")
def got() -> dict:
    legacy = [json.loads(line) for line in (FIX / "decisions_v3.jsonl").read_text().splitlines() if line.strip()]
    rows = legacy_rows_to_metric_rows(legacy)
    assert sum(r["kind"] == "gate" for r in rows) == 300
    assert sum(r["kind"] == "view" for r in rows) == 3600
    s = summarise(rows, seed=11, reps=1000)
    return s["deciders"][DECIDER]


def _cell(got: dict, lens_name: str) -> dict:
    system, budget = lens_name.rsplit("@", 1)
    return got["cells"][cell_name(system, "default", int(budget))]


def test_bootstrap_settings(expected):
    assert expected["bootstrap"]["seed"] == 11 and expected["bootstrap"]["reps"] == 1000


def test_denominators_and_validity(got, expected):
    assert got["n_cases"] == expected["n_cases"] == 300
    assert got["n_gated"] == 290
    assert got["excluded_from_gated"]["count"] == expected["excluded_from_gated"]["count"] == 10
    assert got["excluded_from_gated"]["by_mechanism"] == expected["excluded_from_gated"]["by_mechanism"]
    ev = expected["validity"]
    assert got["validity"]["overall"] == ev["overall"]
    assert got["validity"]["overall"]["correct"] == 290
    assert got["validity"]["per_mechanism"] == ev["per_mechanism"]
    assert got["validity"]["passes"] is ev["gate"]["passes"] is True
    assert got["floor"] is None


def test_all_twelve_cells_present(got, expected):
    assert len(expected["cells"]) == 12
    assert len(got["cells"]) == 12
    for name in expected["cells"]:
        system, budget = name.rsplit("@", 1)
        assert cell_name(system, "default", int(budget)) in got["cells"]


@pytest.mark.parametrize("lens_name", sorted(json.loads((FIX / "summary_v3.json").read_text())["cells"]))
def test_cell_reproduces(got, expected, lens_name):
    exp_cell = expected["cells"][lens_name]
    cell = _cell(got, lens_name)
    hf = exp_cell["harmful_flip"]
    assert hf["n_gated"] == 290 and hf["n_all"] == 300

    # 1. integer sums
    for ours, path in PARTS.items():
        e = hf
        for p in path:
            e = e[p]
        e = e["rate_gated"]
        for k in SUM_KEYS:
            assert cell[ours]["sums"][k] == e["sums"][k], (lens_name, ours, k)
    # 2. values
    for ours, path in PARTS.items():
        e = hf
        for p in path:
            e = e[p]
        assert cell[ours]["value"] == pytest.approx(e["rate_gated"]["value"], abs=1e-9), (lens_name, ours)
    # 3. intervals, exactly as rounded
    for ours, path in PARTS.items():
        e = hf
        for p in path:
            e = e[p]
        assert cell[ours]["ci95"] == e["rate_gated"]["ci95"], (lens_name, ours)

    # gated false closure rate
    ef = exp_cell["fcr_gated"]["all"]
    es = ef["sums"]
    assert cell["false_closure_rate"]["sums"]["com"] == es["c_r"] + es["c_0"]
    assert cell["false_closure_rate"]["sums"]["fc"] == es["f_r"] + es["f_0"]
    assert cell["false_closure_rate"]["value"] == pytest.approx(ef["value"], abs=1e-9)
    assert cell["false_closure_rate"]["ci95"] == ef["ci95"]

    # view size
    assert cell["view"]["mean_tokens"] == pytest.approx(exp_cell["view"]["mean_tokens"], abs=1e-9)
    assert cell["view"]["mean_units"] == pytest.approx(exp_cell["view"]["mean_units"], abs=1e-9)

    # no floor rows: no headline
    assert cell["headline"] is None and cell["abstention_above_floor"] is None
    assert cell["repeats"] == [1]
