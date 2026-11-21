"""The separation rule (CHARTER section 4.6) on synthetic metric rows built here."""

from __future__ import annotations

import json
import random

import pytest

from memory_bench.cases.schema import EXPLAIN, OPEN_REVIEW
from memory_bench.cli import main
from memory_bench.scoring import separation, summarise
from memory_bench.scoring.separation import bootstrap_p, holm

REPS = 400
A, B = ("prodA", "default", 1000), ("prodB", "default", 1000)
NA, NB = "prodA/default@1000", "prodB/default@1000"
PAIR = f"{NA} | {NB}"


def row(cid, decider, kind, system, budget, repeat, action):
    return {"case_id": cid, "track": "account_review", "mechanism": "M0", "a_star": EXPLAIN, "decider": decider,
            "kind": kind, "system": system, "config_label": "default", "budget": budget, "repeat": repeat,
            "action": action, "view_tokens": 0, "view_units": 0, "view_sha256": ""}


def make_rows(decider, rates, *, n=200, seed=0, floor_rate=0.05, same_outcomes=False):
    """Gate right on every case; one floor row and one row per product per case. rates: system -> wrong-commit
    probability. same_outcomes: every product reads the same per-case draw (a synthetic null)."""
    rng = random.Random(f"{decider}|{seed}")
    out = []
    for i in range(n):
        cid = f"c{i:04d}"
        out.append(row(cid, decider, "gate", "full", None, 0, EXPLAIN))
        out.append(row(cid, decider, "floor", "full", None, 1, OPEN_REVIEW if rng.random() < floor_rate else EXPLAIN))
        shared = rng.random()
        for system, p in rates.items():
            u = shared if same_outcomes else rng.random()
            out.append(row(cid, decider, "view", system, 1000, 1, OPEN_REVIEW if u < p else EXPLAIN))
    return out


def sep(rows, products=(A, B)):
    return separation(rows, list(products), seed=11, reps=REPS)


def test_planted_difference_is_found_on_two_deciders():
    rows = make_rows("d1", {"prodA": 0.10, "prodB": 0.30}) + make_rows("d2", {"prodA": 0.12, "prodB": 0.32})
    res = sep(rows)
    for d in ("d1", "d2"):
        p = res["deciders"][d]["pairs"][PAIR]
        assert p["separates"] and p["direction"] == "a_lower" and p["holm_p"] < 0.05
        assert p["value"] == pytest.approx(-0.2, abs=0.08) and p["ci95"][1] < 0
        assert res["deciders"][d]["from_floor"][NB]["separates"]
    assert res["leaderboard"] is True and res["verdict"] == "leaderboard"
    assert res["separating_pairs"] == [{"pair": PAIR, "direction": "a_lower"}]
    json.dumps(res)  # serialisable, string keys


def test_synthetic_null_is_not_a_leaderboard():
    same = make_rows("d1", {"prodA": 0.2, "prodB": 0.2}, same_outcomes=True) + \
        make_rows("d2", {"prodA": 0.2, "prodB": 0.2}, same_outcomes=True)
    res = sep(same)
    for d in ("d1", "d2"):
        p = res["deciders"][d]["pairs"][PAIR]
        assert p["value"] == 0 and p["p"] == 1.0 and not p["separates"] and p["direction"] is None
    assert res["leaderboard"] is False and res["verdict"] == "null"

    noisy = make_rows("d1", {"prodA": 0.2, "prodB": 0.2}, seed=3) + make_rows("d2", {"prodA": 0.2, "prodB": 0.2},
                                                                               seed=3)
    assert sep(noisy)["leaderboard"] is False


def test_opposite_directions_are_not_a_leaderboard():
    rows = make_rows("d1", {"prodA": 0.10, "prodB": 0.30}) + make_rows("d2", {"prodA": 0.30, "prodB": 0.10})
    res = sep(rows)
    assert res["deciders"]["d1"]["pairs"][PAIR]["direction"] == "a_lower"
    assert res["deciders"]["d2"]["pairs"][PAIR]["direction"] == "b_lower"
    assert all(res["deciders"][d]["pairs"][PAIR]["separates"] for d in ("d1", "d2"))
    assert res["leaderboard"] is False and res["verdict"] == "null" and res["separating_pairs"] == []


def test_holm_hand_example_and_monotone():
    # sorted 0.01, 0.03, 0.04 -> 3*0.01 = 0.03; 2*0.03 = 0.06; max(0.06, 1*0.04) = 0.06
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    rng = random.Random(5)
    for _ in range(50):
        ps = [rng.random() ** 3 for _ in range(rng.randint(1, 8))]
        adj = holm(ps)
        order = sorted(range(len(ps)), key=lambda i: ps[i])
        assert all(adj[order[k]] <= adj[order[k + 1]] for k in range(len(ps) - 1))
        assert all(p <= a <= 1.0 for p, a in zip(ps, adj))


def test_holm_with_none_counts_only_defined():
    # m = 2: 0.5 -> 1.0, 0.9 -> max(1.0, 0.9) = 1.0
    assert holm([0.5, None, 0.9]) == [1.0, None, 1.0]
    assert holm([0.02, None, 0.04]) == pytest.approx([0.04, None, 0.04])


def test_bootstrap_p_formula():
    assert bootstrap_p([]) is None
    assert bootstrap_p([0.0] * 9) == 1.0
    assert bootstrap_p([1.0] * 99) == pytest.approx(2 * 1 / 100)
    assert bootstrap_p([-1.0] * 50 + [1.0] * 49) == pytest.approx(min(1, 2 * 50 / 100))


def test_product_identical_to_floor_does_not_separate():
    rows = make_rows("d1", {"prodA": 0.3}, floor_rate=0.0)
    for r in rows:  # make prodA's actions equal the floor's, case by case
        if r["kind"] == "view":
            r["action"] = next(x["action"] for x in rows if x["case_id"] == r["case_id"] and x["kind"] == "floor")
    res = sep(rows, [A])
    f = res["deciders"]["d1"]["from_floor"][NA]
    assert f["value"] == 0 and f["ci95"] == [0.0, 0.0] and f["p"] == 1.0 and not f["separates"]


def test_from_floor_uses_the_summary_resamples():
    rows = make_rows("d1", {"prodA": 0.15, "prodB": 0.25})
    s = summarise(rows, seed=11, reps=REPS)["deciders"]["d1"]["cells"]
    res = sep(rows)["deciders"]["d1"]["from_floor"]
    for name in (NA, NB):
        assert res[name]["value"] == s[name]["headline"]["value"]
        assert res[name]["ci95"] == s[name]["headline"]["ci95"]
        assert res[name]["n_resamples"] == REPS


def test_single_decider_and_no_floor():
    rows = make_rows("d1", {"prodA": 0.10, "prodB": 0.30})
    res = sep(rows)
    assert res["leaderboard"] is None and res["verdict"] == "needs two deciders"
    no_floor = [r for r in rows if r["kind"] != "floor"]
    res2 = sep(no_floor)
    assert res2["deciders"]["d1"]["from_floor"] == {NA: None, NB: None}
    assert res2["deciders"]["d1"]["pairs"][PAIR]["separates"]


def test_rule_fields_and_determinism():
    rows = make_rows("d1", {"prodA": 0.10, "prodB": 0.20, "prodC": 0.25}) + \
        make_rows("d2", {"prodA": 0.10, "prodB": 0.20, "prodC": 0.25})
    prods = [A, B, ("prodC", "default", 1000)]
    r1, r2 = sep(rows, prods), sep(rows, prods)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    rule = r1["rule"]
    assert rule["alpha"] == 0.05 and rule["correction"] == "holm" and rule["seed"] == 11 and rule["reps"] == REPS
    assert len(r1["deciders"]["d1"]["pairs"]) == 3
    assert all(" | " in k for k in r1["deciders"]["d1"]["pairs"])
    with pytest.raises(ValueError):
        separation(rows, [A, A])


def test_report_products_option(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["run", "--mock", "--noisy", "0.2", "--n", "6", "--adapters", "bm25,recent", "--repeats", "2",
                 "--boot-reps", "50", "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["report", str(out)]) == 0
    plain = capsys.readouterr().out
    assert "separation" not in plain
    assert main(["report", str(out), "--products", "bm25,recent"]) == 0
    text = capsys.readouterr().out
    assert text.startswith(plain)
    assert "separation (CHARTER section 4.6)" in text and "bm25/default@1000 | recent/default@1000" in text
    assert "verdict: needs two deciders" in text and "correction holm" in text
