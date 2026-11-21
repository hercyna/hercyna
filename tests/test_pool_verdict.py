"""bench verdict: pooling the table's runs (src/memory_bench/engine/pool.py)."""

import pytest

from memory_bench.cli import main
from memory_bench.engine.pool import PoolError, pool, verdict


def _run(out, adapters, *extra):
    assert main(["run", "--mock", "--n", "6", "--adapters", adapters, "--budgets", "1000", "--repeats", "3",
                 "--boot-reps", "50", "--out", str(out), *extra]) == 0
    return out


def test_two_runs_pool_into_one_row_set(tmp_path):
    a = _run(tmp_path / "a", "bm25")
    b = _run(tmp_path / "b", "recent")
    rows, facts = pool([a, b], require_table=False)
    systems = {r["system"] for r in rows if r["kind"] == "view"}
    assert systems == {"bm25", "recent"}
    assert sum(1 for r in rows if r["kind"] == "gate") == 6  # shared gate rows counted once
    assert [x["new_rows"] > 0 for x in facts["runs"]] == [True, True]


def test_exploratory_runs_refused_for_the_table(tmp_path):
    a = _run(tmp_path / "a", "bm25")
    with pytest.raises(PoolError, match="not table"):
        pool([a])


def test_runs_that_did_not_share_calls_are_refused(tmp_path):
    a = _run(tmp_path / "a", "bm25", "--noisy", "0.5", "--noisy-seed", "1")
    b = _run(tmp_path / "b", "recent", "--noisy", "0.5", "--noisy-seed", "2")
    with pytest.raises(PoolError, match="did not share"):
        pool([a, b], require_table=False)


def test_verdict_cli_refuses_and_reports(tmp_path, capsys):
    a = _run(tmp_path / "a", "bm25")
    assert main(["verdict", str(a)]) == 2
    assert "refused" in capsys.readouterr().err
    assert main(["verdict", str(a), "--exploratory", "--boot-reps", "50", "--out", str(tmp_path / "v.json")]) == 0
    assert "verdict:" in capsys.readouterr().out
    assert verdict([a], reps=50, require_table=False)["separation"]["verdict"] == "needs two deciders"


# ---------------------------------------------------------------- Day 14: shared views, empty, sensitivity

def _row(case, did, system, sha, repeat=1, label="k25", budget=1000):
    return {"case_id": case, "decider": did, "kind": "view", "system": system, "config_label": label,
            "budget": budget, "repeat": repeat, "view_sha256": sha}


def test_shared_views_counts_cells_across_deciders():
    from memory_bench.engine.pool import shared_views

    rows = [_row("c1", "d1", "mem0", "a"), _row("c1", "d2", "mem0", "a"),
            _row("c2", "d1", "mem0", "b"), _row("c2", "d2", "mem0", "b"),
            _row("c3", "d1", "mem0", "z"),                                   # one decider only: not counted
            _row("c1", "d1", "bm25", "x"), _row("c1", "d2", "bm25", "y")]   # a baseline: never checked
    assert shared_views(rows) == {"shared_views": True, "shared_view_cells": 2, "differing_view_cells": 0}
    rows.append(_row("c2", "d3", "mem0", "other"))  # a separate ingest for one decider: legal, recorded
    assert shared_views(rows) == {"shared_views": False, "shared_view_cells": 2, "differing_view_cells": 1}
    assert shared_views([])["shared_views"] is None


def test_empty_is_a_baseline_never_a_product(tmp_path):
    from memory_bench.engine import pool as P
    from memory_bench.site import build as S

    assert P.BASELINES == ("full", "bm25", "recent", "empty") == S.BASELINES
    a = _run(tmp_path / "a", "empty,bm25")
    rows, facts = pool([a], require_table=False)
    assert {r["system"] for r in rows if r["kind"] == "view"} == {"empty", "bm25"}
    assert facts["products"] == [] and facts["shared_views"] is None


def test_pool_records_shared_views_for_products(tmp_path, monkeypatch):
    """bm25 treated as a product: one mock-oracle run and one mock-noisy run read the same views."""
    import memory_bench.engine.pool as P

    monkeypatch.setattr(P, "BASELINES", ("full",))
    a = _run(tmp_path / "a", "bm25")
    b = _run(tmp_path / "b", "bm25", "--noisy", "0.2", "--noisy-seed", "1")
    rows, facts = pool([a, b], require_table=False)
    assert facts["deciders"] == ["mock-noisy", "mock-oracle"]
    assert facts["shared_views"] is True and facts["shared_view_cells"] == 6 * 3 and facts["differing_view_cells"] == 0


def test_sensitivity_reruns_the_rule_per_seed(tmp_path, monkeypatch, capsys):
    import json

    import memory_bench.engine.pool as P
    from memory_bench.engine.sensitivity import sensitivity, table

    monkeypatch.setattr(P, "BASELINES", ("full",))
    a = _run(tmp_path / "a", "bm25,recent")
    b = _run(tmp_path / "b", "bm25,recent", "--noisy", "0.2", "--noisy-seed", "1")
    res = sensitivity([a, b], [11, 12, 13], reps=60, require_table=False)
    assert res["grade"] == "exploratory" and res["seeds"] == [11, 12, 13] and set(res["by_seed"]) == {"11", "12", "13"}
    for s in res["by_seed"].values():
        assert set(s["pairs"]) == {"mock-noisy", "mock-oracle"}
        assert all(p["n_resamples"] <= 60 for d in s["pairs"].values() for p in d.values())
    # seed 11 at the same reps is the verdict the frozen rule gives
    assert res["by_seed"]["11"]["verdict"] == verdict([a, b], reps=60, require_table=False)["separation"]["verdict"]
    lines = table(res)
    assert lines[0].startswith("EXPLORATORY") and any("bm25/default@1000 | recent/default@1000" in x for x in lines)
    out = tmp_path / "s.json"
    assert main(["sensitivity", str(a), str(b), "--seeds", "11,12", "--boot-reps", "60", "--exploratory",
                 "--out", str(out)]) == 0
    assert "EXPLORATORY" in capsys.readouterr().out and json.loads(out.read_text())["seeds"] == [11, 12]
    assert main(["sensitivity", str(a)]) == 2  # exploratory runs refused without the flag
