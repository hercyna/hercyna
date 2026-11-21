"""bench stability and bench power (docs/freeze.md sections 5 and 6) on hand-made rows with known answers."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from memory_bench.cli import main
from memory_bench.engine import files as F
from memory_bench.scoring.power import mde, power_note
from memory_bench.scoring.stability import repeat_stability
from memory_bench.submit.textfree import scan_run

ROOT = Path(__file__).resolve().parent.parent
D = "d"
A_STAR = {"c1": "EXPLAIN", "c2": "OPEN_REVIEW", "c3": "EXPLAIN"}
GATE = {"c1": "EXPLAIN", "c2": "OPEN_REVIEW", "c3": "APPLY_CREDIT"}  # c3 is not gated
REPEATS = {
    ("floor", "full"): {"c1": ["EXPLAIN"] * 3, "c2": ["OPEN_REVIEW", "APPLY_CREDIT", "OPEN_REVIEW"],
                        "c3": ["EXPLAIN", "APPLY_CREDIT", "CHECK_DEVICE"]},
    ("view", "bm25"): {"c1": ["APPLY_CREDIT"] * 3, "c2": ["REQUEST_INFO", "OPEN_REVIEW", "PARSE_ERROR"],
                       "c3": ["EXPLAIN", "APPLY_CREDIT", "CHECK_DEVICE"]},
    ("view", "recent"): {"c1": ["EXPLAIN", "APPLY_CREDIT", "REQUEST_INFO"], "c2": ["CHECK_DEVICE"] * 3,
                         "c3": ["EXPLAIN"] * 3},
}
Z_SUM = 1.959964 + 0.841621        # z(0.975) + z(0.8)
Z_SUM_HOLM = 2.393980 + 0.841621   # z(1 - 0.05/6) + z(0.8)


def rows() -> list[dict]:
    out = []
    for cid in A_STAR:
        base = {"case_id": cid, "decider": D, "a_star": A_STAR[cid], "mechanism": "M0", "config_label": "default"}
        out.append({**base, "kind": "gate", "system": "full", "budget": None, "repeat": 0, "action": GATE[cid]})
        for (kind, system), acts in REPEATS.items():
            for r, a in enumerate(acts[cid], 1):
                out.append({**base, "kind": kind, "system": system, "budget": None if kind == "floor" else 1000,
                            "repeat": r, "action": a})
    return out


def test_repeat_stability_known_answers():
    res = repeat_stability(rows())
    d = res["deciders"][D]
    assert (d["n_cases"], d["n_gated"]) == (3, 2)
    s = d["systems"]
    assert list(s) == ["full", "bm25", "recent"]
    assert (s["full"]["gated_cases"], s["full"]["identical_cases"], s["full"]["identical_share"]) == (2, 1, 0.5)
    assert (s["full"]["later_repeats"], s["full"]["later_differ"], s["full"]["later_differ_share"]) == (4, 1, 0.25)
    for name in ("bm25", "recent"):
        assert (s[name]["identical_cases"], s[name]["later_differ"], s[name]["later_differ_share"]) == (1, 2, 0.5)
    assert s["bm25"]["budget"] == 1000 and s["full"]["budget"] is None and s["full"]["repeats"] == [1, 2, 3]


def test_stability_keys_by_budget_and_label():
    rs = rows()
    extra = [{**r, "budget": 500} for r in rs if r["system"] == "bm25"]
    extra += [{**r, "system": "mem0", "config_label": "k25"} for r in rs if r["system"] == "recent"]
    s = repeat_stability(rs + extra)["deciders"][D]["systems"]
    assert set(s) == {"full", "bm25@1000", "bm25@500", "recent", "mem0/k25"}


def test_power_known_answers():
    res = power_note(rows())
    assert res["method"] == "normal_approximation" and res["pairs_k"] == 3 and res["power"] == 0.8
    d = res["deciders"][D]
    # bm25 per-case headline: c1 1 - 0 = 1, c2 0 - 1/3; population sd 2/3
    b = d["systems"]["bm25"]
    se_b = (2 / 3) / math.sqrt(2)
    assert b["n"] == 2 and b["mean"] == pytest.approx(1 / 3, abs=1e-6) and b["se"] == pytest.approx(se_b, abs=1e-6)
    # recent: c1 1/3, c2 2/3; population sd 1/6
    assert d["systems"]["recent"]["se"] == pytest.approx((1 / 6) / math.sqrt(2), abs=1e-6)
    # pair bm25 - recent: c1 2/3, c2 -1; population sd 5/6
    p = d["pairs"]["bm25:recent"]
    se_p = (5 / 6) / math.sqrt(2)
    assert (p["a"], p["b"], p["n"]) == ("bm25", "recent", 2)
    assert p["mean"] == pytest.approx(-1 / 6, abs=1e-6) and p["se"] == pytest.approx(se_p, abs=1e-6)
    assert p["mde"] == pytest.approx(Z_SUM * se_p, abs=1e-5)
    assert p["mde_holm"] == pytest.approx(Z_SUM_HOLM * se_p, abs=1e-5)


def test_power_options_and_no_floor():
    assert mde(1.0, 0.05, 0.8) == pytest.approx(Z_SUM, abs=1e-5)
    res = power_note(rows(), pairs=1, power=0.9)
    assert res["deciders"][D]["pairs"]["bm25:recent"]["mde"] == res["deciders"][D]["pairs"]["bm25:recent"]["mde_holm"]
    no_floor = [r for r in rows() if r["kind"] != "floor"]
    d = power_note(no_floor)["deciders"][D]
    assert d["systems"] == {"bm25": None, "recent": None} and d["pairs"]["bm25:recent"]["se"] is not None
    with pytest.raises(ValueError):
        power_note(rows(), pairs=0)


def test_cli_stability_and_power_write_text_free_files(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["run", "--mock", "--noisy", "0.2", "--n", "20", "--adapters", "bm25,recent", "--budgets", "1000",
                 "--repeats", "3", "--boot-reps", "50", "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["stability", str(out)]) == 0
    assert "identical" in capsys.readouterr().out
    assert main(["power", str(out), "--pairs", "2"]) == 0
    text = capsys.readouterr().out
    assert "normal approximation" in text and "bm25 - recent" in text
    st, pw = F.read_json(out / "stability.json"), F.read_json(out / "power.json")
    assert set(st["deciders"]["mock-noisy"]["systems"]) == {"full", "bm25", "recent"}
    assert pw["pairs_k"] == 2
    assert scan_run(out) == []
    assert main(["power", str(out), "--pairs", "0"]) == 2
    assert main(["stability", str(tmp_path / "nothing")]) == 2


@pytest.mark.parametrize("run_id,expect", [
    ("day4-baselines-haiku", {"full": 273, "bm25": 175, "recent": 261, "n": 292, "se": 2.41, "mde": 6.7, "holm": 7.8}),
    ("day4-baselines-grok-4-20-r", {"full": 289, "bm25": 214, "recent": 229, "n": 296, "se": 1.91, "mde": 5.3,
                                    "holm": 6.2}),
])
def test_day4_cross_check(run_id, expect):
    p = ROOT / "results" / "runs" / run_id / "metrics.jsonl"
    if not p.exists():
        pytest.skip("day 4 run folder not present")
    rs = F.read_metrics(p)
    (did, st), = repeat_stability(rs)["deciders"].items()
    for name in ("full", "bm25", "recent"):
        assert (st["systems"][name]["identical_cases"], st["systems"][name]["gated_cases"]) == (expect[name], expect["n"])
    pair = power_note(rs)["deciders"][did]["pairs"]["bm25:recent"]
    assert round(100 * pair["se"], 2) == expect["se"]
    assert round(100 * pair["mde"], 1) == expect["mde"] and round(100 * pair["mde_holm"], 1) == expect["holm"]
