"""bench vendor pack (src/memory_bench/release/packs.py) on small fake runs."""

import hashlib
import json
from pathlib import Path

import pytest

from memory_bench.cli import main
from memory_bench.release.packs import PackError, write_packs

REPO = Path(__file__).resolve().parents[1]
CASES_SHA = "c" * 64


def _ci(v, lo, hi):
    return {"value": v, "ci95": [lo, hi]}


def _cell(system, label, k):
    return {"system": system, "config_label": label, "budget": 1000,
            "headline": _ci(0.05 * k, 0.01 * k, 0.09 * k),
            "abstention_above_floor": _ci(0.3, 0.2, 0.4),
            "right_action": _ci(0.5, 0.4, 0.6),
            "false_closure_rate": _ci(0.15, 0.1, 0.2)}


def _run(base: Path, name: str, decider: str, product: str | None, label="k25", k=1):
    d = base / name
    (d / "private").mkdir(parents=True)
    adapters = [{"name": "full", "config_label": "default", "config_sha256": "x", "product_version": "builtin"}]
    cells = {}
    if product:
        adapters.append({"name": product, "config_label": label, "config_sha256": "y", "product_version": "9.9"})
        cells[f"{product}/{label}@1000"] = _cell(product, label, k)
    man = {"run_id": name, "state": "complete", "grade": "table", "adapters": adapters, "budgets": [1000],
           "repeats": 3, "bootstrap": {"seed": 11, "reps": 1000}, "cases": {"n": 4, "path": None, "sha256": CASES_SHA},
           "deciders": [{"id": decider}], "memory_side": {"id": "grok-4-20-nr-mem"},
           "protocol": {"path": "protocol/v1.json", "protocol_sha256": "p" * 64, "version": "v1"}}
    floor = {"wrong_commit": _ci(0.01, 0.0, 0.02), "abstention": _ci(0.02, 0.0, 0.04), "right_action": _ci(0.97, 0.9, 1.0)}
    summ = {"deciders": {decider: {"cells": cells, "floor": floor, "n_cases": 4, "n_gated": 4,
                                   "validity": {"overall": {"correct": 4, "n": 4}}}}}
    health = {"adapters": [{"name": product, "config_label": label, "errors": 0, "cases_done": 4,
                            "product_warnings": {"total": 3, "by_logger": {"lg.a": 2, "lg.b": 1}}}] if product else []}
    audit = {"systems": {f"{product}/{label}": {"views_checked": 4, "views_with_failed_call": 0, "failed_calls": 7}}
             if product else {}}
    for fn, obj in (("manifest.json", man), ("summary.json", summ), ("health.json", health), ("view-audit.json", audit)):
        (d / fn).write_text(json.dumps(obj))
    lines = []
    for sysname in ([product, "recent"] if product else ["bm25"]):
        lines.append(json.dumps({"case_id": "c1", "system": sysname, "text": f"synthetic text of {sysname}"}))
    (d / "private" / "views.jsonl").write_text("\n".join(lines) + "\n")
    return d


def _verdict(base: Path):
    def pair(h):
        return {"holm_p": h, "direction": "a_lower", "separates": h < 0.05}
    dd = {"from_floor": {"mem0/k25@1000": {"p": 0.002, "separates": True},
                         "langmem/c25@1000": {"p": 0.003, "separates": True}},
          "pairs": {"langmem/c25@1000 | mem0/k25@1000": pair(0.006),
                    "graphiti/e25@1000 | langmem/c25@1000": pair(0.04)}}
    v = {"pool": {"runs": [1, 2], "protocol_sha256": "p" * 64},
         "separation": {"verdict": "leaderboard", "deciders": {"haiku-5-5": dd, "grok-4-20-r": dd}}}
    p = base / "verdict.json"
    p.write_text(json.dumps(v))
    return p


@pytest.fixture()
def runs(tmp_path):
    base = tmp_path / "runs"
    base.mkdir()
    return [_run(base, "r-haiku-prime", "haiku-5-5", None),
            _run(base, "r-haiku-mem0", "haiku-5-5", "mem0", "k25", 1),
            _run(base, "r-grok-mem0", "grok-4-20-r", "mem0", "k25", 2),
            _run(base, "r-haiku-langmem", "haiku-5-5", "langmem", "c25", 3)]


def test_pack_files_and_results_facts(tmp_path, runs):
    out = tmp_path / "out"
    packs = write_packs(runs, out, verdict_path=_verdict(tmp_path), root=REPO)
    assert set(packs) == {"mem0", "langmem"}
    p = out / "mem0"
    for f in ("RESULTS.md", "RERUN.md", "REBUTTAL.md", "MANIFEST.json", "views/NOTE.md", "config/v1.json",
              "config/mem0-k25.json", "config/grok-4-20-nr-mem.json"):
        assert (p / f).is_file(), f
    text = (p / "RESULTS.md").read_text()
    assert "+5.0 [+1.0, +9.0]" in text and "+10.0 [+2.0, +18.0]" in text  # haiku and grok excess wrong action
    assert "15.0 [10.0, 20.0]" in text      # false_closure_rate
    assert "Views checked: 4. Views with a failed call: 0" in text
    assert "lg.a: 2" in text and "lg.b: 1" in text
    assert "Mem0 vs floor: p 0.0020, separates" in text
    assert "LangMem vs Mem0: Holm p 0.0060, LangMem lower, separates" in text
    assert "Graphiti" not in text            # a pair without this product is left out
    assert "[Mem0](mem0/RESULTS.md)" in (out / "README.md").read_text()


def test_views_only_this_product_and_note(tmp_path, runs):
    out = tmp_path / "out"
    write_packs(runs, out, root=REPO)
    files = sorted((out / "mem0" / "views").glob("r-*.jsonl"))
    assert [f.name for f in files] == ["r-grok-mem0.jsonl", "r-haiku-mem0.jsonl"]
    for f in files:
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        assert [r["system"] for r in rows] == ["mem0"]
    note = (out / "mem0" / "views" / "NOTE.md").read_text()
    assert note.count("\n") == 1 and "vendor only" in note and "never released publicly" in note
    assert "No verdict file" in (out / "mem0" / "RESULTS.md").read_text()


def test_manifest_hashes_match_and_rerun_content(tmp_path, runs):
    out = tmp_path / "out"
    write_packs(runs, out, root=REPO)
    mf = json.loads((out / "langmem" / "MANIFEST.json").read_text())
    names = {f["path"] for f in mf["files"]}
    assert "MANIFEST.json" not in names and "RESULTS.md" in names and "views/NOTE.md" in names
    for f in mf["files"]:
        assert hashlib.sha256((out / "langmem" / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    rr = (out / "langmem" / "RERUN.md").read_text()
    assert "UV_PROJECT_ENVIRONMENT=.venv-langmem uv sync --extra langmem" in rr
    assert "--adapters configs/adapters/langmem-c25.json" in rr
    # exploratory rerun: own keys, no --protocol, compared with bench report (Day 15 final review, item 6)
    assert "configs/adapters/langmem-c25.json" in rr and "--protocol protocol" not in rr and "exploratory" in rr
    assert "your copy of configs/memory-side/grok-4-20-nr-mem.json" in rr and "bench report" in rr
    assert "bench verdict" not in rr
    assert "FalkorDB" not in rr
    assert "--repeats 3" in rr and "--budgets 1000" in rr


def test_graphiti_rerun_names_falkordb(tmp_path):
    base = tmp_path / "runs"
    base.mkdir()
    r = _run(base, "r-haiku-graphiti", "haiku-5-5", "graphiti", "e25")
    write_packs([r], tmp_path / "out", root=REPO)
    rr = (tmp_path / "out" / "graphiti" / "RERUN.md").read_text()
    assert "FalkorDB" in rr and "127.0.0.1:6390" in rr and ".venv-graphiti" in rr


def test_refuses_inside_repo_existing_pack_and_incomplete_run(tmp_path, runs, capsys):
    with pytest.raises(PackError, match="inside the repository"):
        write_packs(runs, REPO / "vendor-packs-x", root=REPO)
    out = tmp_path / "out"
    write_packs(runs, out, root=REPO)
    with pytest.raises(PackError, match="already exists"):
        write_packs(runs, out, root=REPO)
    man = json.loads((runs[1] / "manifest.json").read_text())
    man["state"] = "running"
    (runs[1] / "manifest.json").write_text(json.dumps(man))
    with pytest.raises(PackError, match="not complete"):
        write_packs(runs, tmp_path / "out2", root=REPO)


def test_cli_registered(tmp_path, runs, capsys):
    out = tmp_path / "cli-out"
    assert main(["vendor", "pack", "--runs", *map(str, runs), "--out", str(out), "--verdict",
                 str(_verdict(tmp_path))]) == 0
    assert "mem0:" in capsys.readouterr().out
    assert (out / "langmem" / "RESULTS.md").is_file()
    assert main(["vendor", "pack", "--runs", *map(str, runs), "--out", str(out)]) == 2
