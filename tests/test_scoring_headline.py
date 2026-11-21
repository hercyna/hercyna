"""The new headline metric on hand-made metric rows: floor, paired headline, slots, guards, determinism."""

from __future__ import annotations

import json
import random

import pytest

from memory_bench.cases.schema import APPLY_CREDIT, EXPLAIN, OPEN_REVIEW, REQUEST_INFO
from memory_bench.deciders.lifted import PARSE_ERROR
from memory_bench.scoring import summarise
from memory_bench.scoring.metrics import ScoringError, cell_name

D = "mock"
REPS = 200


def row(case, kind, action, *, a_star=EXPLAIN, mech="M0", system=None, budget=None, repeat=None,
        label="default", tokens=0, units=0, decider=D):
    if system is None:
        system = "full" if kind in ("gate", "floor") else "bm25"
    if repeat is None:
        repeat = 0 if kind == "gate" else 1
    if kind == "view" and budget is None:
        budget = 500
    return {"case_id": case, "track": "account_review", "mechanism": mech, "a_star": a_star, "decider": decider,
            "kind": kind, "system": system, "config_label": label, "budget": budget, "repeat": repeat,
            "action": action, "view_tokens": tokens, "view_units": units, "view_sha256": ""}


def summ(rows, reps=REPS):
    return summarise(rows, seed=11, reps=reps)["deciders"][D]


BM = cell_name("bm25", "default", 500)


def test_rates_and_request_info_case():
    rows = [
        # c1: a_star EXPLAIN, view commits wrong
        row("c1", "gate", EXPLAIN), row("c1", "view", OPEN_REVIEW, tokens=10, units=2),
        # c2: a_star REQUEST_INFO; a committed view action is a wrong commit
        row("c2", "gate", REQUEST_INFO, a_star=REQUEST_INFO, mech="U1"),
        row("c2", "view", APPLY_CREDIT, a_star=REQUEST_INFO, mech="U1", tokens=20, units=4),
        # c3: a_star REQUEST_INFO and the view abstains: right, not abstention
        row("c3", "gate", REQUEST_INFO, a_star=REQUEST_INFO, mech="U1"),
        row("c3", "view", REQUEST_INFO, a_star=REQUEST_INFO, mech="U1", tokens=30, units=6),
        # c4: abstention on a committing case
        row("c4", "gate", EXPLAIN), row("c4", "view", REQUEST_INFO, tokens=40, units=8),
        # c5: parse error
        row("c5", "gate", EXPLAIN), row("c5", "view", PARSE_ERROR, tokens=50, units=10),
    ]
    s = summ(rows)
    c = s["cells"][BM]
    assert c["wrong_commit"]["value"] == pytest.approx(2 / 5)
    assert c["wrong_commit"]["sums"]["wc"] == 2
    assert c["abstention"]["value"] == pytest.approx(1 / 5)
    assert c["parse_error"]["value"] == pytest.approx(1 / 5)
    assert c["right_action"]["value"] == pytest.approx(1 / 5)
    assert c["harmful"]["value"] == pytest.approx(4 / 5)
    sums = c["harmful"]["sums"]
    assert sums["h"] == sums["wc"] + sums["ab"] + sums["hpe"]  # PARSE_ERROR in harmful, not in wrong_commit
    assert c["false_closure_rate"]["value"] == pytest.approx(2 / 2)  # committed: c1, c2; both wrong
    assert c["view"] == {"mean_tokens": 30.0, "mean_units": 6.0}
    assert c["headline"] is None and s["floor"] is None
    assert c["wrong_commit"]["ci95"] is not None


def test_gate_failures_excluded_and_counted():
    rows = [row("ok", "gate", EXPLAIN), row("ok", "view", OPEN_REVIEW),
            row("bad", "gate", OPEN_REVIEW, mech="M1"), row("bad", "view", OPEN_REVIEW, mech="M1"),
            row("pe", "gate", PARSE_ERROR, mech="M1"), row("pe", "view", OPEN_REVIEW, mech="M1")]
    s = summ(rows)
    assert s["n_cases"] == 3 and s["n_gated"] == 1
    assert s["excluded_from_gated"] == {"count": 2, "by_mechanism": {"M1": 2}, "gate_parse_errors": 1}
    assert s["validity"]["overall"]["correct"] == 1
    assert s["validity"]["per_mechanism"]["M1"]["accuracy"] == 0.0
    assert s["validity"]["passes"] is False
    c = s["cells"][BM]
    assert c["wrong_commit"]["sums"]["g"] == 1 and c["wrong_commit"]["sums"]["one"] == 3
    assert c["wrong_commit"]["value"] == 1.0


def _repeat_rows(n_cases=12, R=3, seed=5):
    rng = random.Random(seed)
    rows = []
    for i in range(n_cases):
        cid = f"c{i:02d}"
        rows.append(row(cid, "gate", EXPLAIN if i != 0 else OPEN_REVIEW))  # c00 fails the gate
        for r in range(1, R + 1):
            rows.append(row(cid, "floor", rng.choice([EXPLAIN, EXPLAIN, EXPLAIN, OPEN_REVIEW, REQUEST_INFO]),
                            repeat=r))
            rows.append(row(cid, "view", rng.choice([EXPLAIN, OPEN_REVIEW, REQUEST_INFO, PARSE_ERROR]),
                            repeat=r, tokens=100 + r, units=r))
            rows.append(row(cid, "view", rng.choice([EXPLAIN, OPEN_REVIEW]), system="recent", repeat=r))
    return rows


def test_three_repeats_give_three_slots_per_gated_case():
    rows = _repeat_rows()
    s = summ(rows)
    c = s["cells"][BM]
    assert c["repeats"] == [1, 2, 3]
    assert c["wrong_commit"]["sums"]["g"] == 3 * 11
    assert c["wrong_commit"]["sums"]["one"] == 3 * 12
    gated_view = [r for r in rows if r["kind"] == "view" and r["system"] == "bm25" and r["case_id"] != "c00"]
    wc = sum(r["action"] in (OPEN_REVIEW,) for r in gated_view)
    assert c["wrong_commit"]["value"] == pytest.approx(wc / 33)


def test_floor_through_view_code_path_and_paired_headline():
    rows = _repeat_rows()
    s = summ(rows)
    # The floor equals the same rows relabelled as a view cell.
    as_view = [({**r, "kind": "view", "system": "floorcopy", "budget": 1} if r["kind"] == "floor" else r)
               for r in rows]
    s2 = summ(as_view)
    copy = s2["cells"][cell_name("floorcopy", "default", 1)]
    for k in ("wrong_commit", "abstention", "parse_error", "harmful", "right_action", "false_closure_rate"):
        assert s["floor"][k] == copy[k]
    assert s2["floor"] is None

    c = s["cells"][BM]
    h = c["headline"]
    assert h["value"] == pytest.approx(c["wrong_commit"]["value"] - s["floor"]["wrong_commit"]["value"])
    assert h["ci95"] is not None and h["ci95"][0] <= h["value"] <= h["ci95"][1]
    a = c["abstention_above_floor"]
    assert a["value"] == pytest.approx(c["abstention"]["value"] - s["floor"]["abstention"]["value"])


def test_system_identical_to_floor_has_zero_headline():
    rows = _repeat_rows()
    twin = [{**r, "kind": "view", "system": "twin", "budget": 500} for r in rows if r["kind"] == "floor"]
    s = summ(rows + twin)
    h = s["cells"][cell_name("twin", "default", 500)]["headline"]
    assert h == {"value": 0.0, "ci95": [0.0, 0.0]}
    assert s["cells"][cell_name("twin", "default", 500)]["abstention_above_floor"] == {"value": 0.0,
                                                                                        "ci95": [0.0, 0.0]}


def test_duplicate_row_key_raises():
    rows = [row("c1", "gate", EXPLAIN), row("c1", "view", EXPLAIN), row("c1", "view", OPEN_REVIEW)]
    with pytest.raises(ScoringError, match="duplicate row key"):
        summarise(rows)


def test_missing_gate_row_raises():
    rows = [row("c1", "gate", EXPLAIN), row("c1", "view", EXPLAIN), row("c2", "view", EXPLAIN)]
    with pytest.raises(ScoringError, match="no gate row"):
        summarise(rows)


def test_deterministic_under_reordering_within_case():
    rows = _repeat_rows()
    by_case: dict[str, list] = {}
    for r in rows:
        by_case.setdefault(r["case_id"], []).append(r)
    rng = random.Random(1)
    shuffled = []
    for rs in by_case.values():
        rs = list(rs)
        rng.shuffle(rs)
        shuffled += rs
    a = json.dumps(summarise(rows, seed=11, reps=REPS), sort_keys=True)
    b = json.dumps(summarise(shuffled, seed=11, reps=REPS), sort_keys=True)
    c = json.dumps(summarise(rows, seed=11, reps=REPS), sort_keys=True)
    assert a == b == c
    top = summarise(rows, seed=11, reps=REPS)
    assert top["schema"] == "memory_bench.summary/1"
    assert top["bootstrap"]["seed"] == 11 and top["bootstrap"]["reps"] == REPS
