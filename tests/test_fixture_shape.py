"""The decision fixture is text-free and has the v3 merged shape."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"
FIELDS = {"case_id", "system", "budget", "mechanism", "a_star", "a_H", "a_V", "view_tokens", "view_units"}
ACTION_NAMES = {"EXPLAIN", "REQUEST_INFO", "CHECK_DEVICE", "APPLY_CREDIT", "OPEN_REVIEW"}
SYSTEMS = {"recent", "rag-bm25", "rag-embed", "summary"}
MECHANISMS = {"M0", "M1", "M2", "M3", "U1", "U2"}
BUDGETS = {500, 1000, 2000}


@pytest.fixture(scope="module")
def rows():
    return [json.loads(line) for line in (FIX / "decisions_v3.jsonl").read_text(encoding="utf-8").splitlines()]


def test_row_count_and_unique_keys(rows):
    assert len(rows) == 3600
    assert len({(r["case_id"], r["system"], r["budget"]) for r in rows}) == 3600


def test_twelve_cells_of_300(rows):
    cells = Counter((r["system"], r["budget"]) for r in rows)
    assert len(cells) == 12
    assert set(cells.values()) == {300}
    assert {s for s, _ in cells} == SYSTEMS and {b for _, b in cells} == BUDGETS


def test_mechanism_counts_per_cell(rows):
    cells = {}
    for r in rows:
        cells.setdefault((r["system"], r["budget"]), Counter())[r["mechanism"]] += 1
    for cell, c in cells.items():
        assert c == {"M0": 150, "M1": 30, "M2": 30, "M3": 30, "U1": 30, "U2": 30}, cell


def test_no_free_text(rows):
    for r in rows:
        assert set(r) == FIELDS
        assert isinstance(r["budget"], int) and r["budget"] in BUDGETS
        assert isinstance(r["view_tokens"], int) and isinstance(r["view_units"], int)
        for k in ("a_star", "a_H", "a_V"):
            assert r[k] in ACTION_NAMES, (k, r[k])
        assert r["system"] in SYSTEMS and r["mechanism"] in MECHANISMS
        assert len(r["case_id"]) <= 8 and r["case_id"].split("-")[0] in {"h11", "u3"}


def test_rows_sorted_by_dev_case_order(rows):
    order = {json.loads(line)["case_id"]: i for i, line in
             enumerate((Path(__file__).resolve().parents[1] / "data/dev/cases.jsonl").read_text().splitlines())}
    keys = [(order[r["case_id"]], r["system"], r["budget"]) for r in rows]
    assert keys == sorted(keys)


def test_expected_score_files_present():
    assert json.loads((FIX / "summary_v3.json").read_text(encoding="utf-8"))
    assert (FIX / "tables_v3.txt").read_text(encoding="utf-8").strip()
