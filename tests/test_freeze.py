"""bench freeze and the run grade (docs/freeze.md sections 3, 4 and 7), in a temp git repo."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import write_jsonl
from memory_bench.cli import main
from memory_bench.engine import ProtocolError, RunConfig, run
from memory_bench.engine import files as F
from memory_bench.engine.freeze import (check_protocol, check_run, freeze_step, load_spec, protocol_sha256,
                                        step_sha256)
from memory_bench.engine.run import needs_proxy

ROOT = Path(__file__).resolve().parent.parent
N = 6
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         env={**os.environ, **GIT_ENV}, check=True)
    return out.stdout


def commit(repo: Path, msg: str = "c") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def spec(step2_values: dict | None = None) -> dict:
    return {"schema": "memory_bench.protocol_spec/1", "version": "v1", "steps": {
        "1": {"title": "metric", "files": ["src/pkg/*.py", "configs/adapters/mem0-k25.json",
                                           "configs/memory-side/m.json", "protocol/PREREGISTRATION.md"],
              "values": {"deciders": ["mock-oracle"], "systems": ["full", "bm25", "recent", "mem0/k25"],
                         "budgets": [1000], "repeats": 3, "boot_seed": 11, "boot_reps": 50,
                         "product_pins": {"mem0ai": "2.2.1"}}},
        "2": {"title": "table", "files": ["data/table/cases.jsonl"],
              "values": step2_values if step2_values is not None else {"table_seed": None, "n_table_cases": None}},
    }}


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    (r / "src" / "pkg").mkdir(parents=True)
    (r / "src" / "pkg" / "a.py").write_text("A = 1\n")
    (r / "src" / "pkg" / "b.py").write_text("B = 2\n")
    (r / "configs" / "adapters").mkdir(parents=True)
    (r / "configs" / "adapters" / "mem0-k25.json").write_bytes((ROOT / "configs/adapters/mem0-k25.json").read_bytes())
    (r / "configs" / "memory-side").mkdir(parents=True)
    (r / "configs" / "memory-side" / "m.json").write_text('{"id": "m"}\n')
    (r / "protocol").mkdir()
    (r / "protocol" / "PREREGISTRATION.md").write_text("hypotheses\n")
    (r / "protocol" / "v1.spec.json").write_text(json.dumps(spec(), indent=2) + "\n")
    git(r, "init", "-q")
    commit(r, "init")
    return r


def freeze(repo: Path, step: int) -> int:
    return main(["freeze", "--step", str(step), "--spec", str(repo / "protocol/v1.spec.json"),
                 "--out", str(repo / "protocol/v1.json")])


def check(repo: Path) -> int:
    return main(["freeze", "--check", "--protocol", str(repo / "protocol/v1.json")])


def add_step2(repo: Path, n: int = N) -> Path:
    cases = repo / "data" / "table" / "cases.jsonl"
    cases.parent.mkdir(parents=True)
    write_jsonl(cases, dev_cases()[:n])
    (repo / "protocol" / "v1.spec.json").write_text(json.dumps(spec({"table_seed": 42, "n_table_cases": n})) + "\n")
    commit(repo, "step 2")
    return cases


# -- freeze ------------------------------------------------------------------------------------------------------

def test_shipped_spec_is_the_design_spec():
    import re

    design = (ROOT / "docs" / "freeze.md").read_text(encoding="utf-8")
    block = re.search(r"## 2\. The spec\n\n```json\n(.*?)```", design, re.S).group(1)
    shipped = load_spec(ROOT / "protocol" / "v1.spec.json")
    assert shipped == json.loads(block)
    frozen = json.loads((ROOT / "protocol" / "v1.json").read_text(encoding="utf-8"))
    assert shipped["steps"]["2"]["values"] == frozen["steps"]["2"]["values"]  # filled on Day 10, then frozen


def test_freeze_then_check_passes(repo, capsys):
    assert freeze(repo, 1) == 0
    p = json.loads((repo / "protocol" / "v1.json").read_text())
    st = p["steps"]["1"]
    assert list(st["files"]) == sorted(["src/pkg/a.py", "src/pkg/b.py", "configs/adapters/mem0-k25.json",
                                        "configs/memory-side/m.json", "protocol/PREREGISTRATION.md"])
    assert st["git_commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert st["values"] == spec()["steps"]["1"]["values"] and st["frozen_at"].endswith("+00:00")
    assert st["step_sha256"] == step_sha256(st["files"], st["values"])
    assert p["protocol_sha256"] == protocol_sha256(p["steps"]) and p["spec"] == "protocol/v1.spec.json"
    assert "2" not in p["steps"]
    capsys.readouterr()
    assert check(repo) == 0
    assert "all hashes match" in capsys.readouterr().out


def test_one_byte_edit_fails_check_naming_the_file(repo, capsys):
    assert freeze(repo, 1) == 0
    (repo / "src" / "pkg" / "b.py").write_text("B = 3\n")
    capsys.readouterr()
    assert check(repo) == 4
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("src/pkg/b.py  expected ")
    assert "B = " not in out  # hashes only, never contents
    bad = check_protocol(repo / "protocol" / "v1.json")
    assert [m.path for m in bad] == ["src/pkg/b.py"] and len(bad[0].actual) == 64


def test_edited_protocol_file_fails_check(repo):
    assert freeze(repo, 1) == 0
    pf = repo / "protocol" / "v1.json"
    p = json.loads(pf.read_text())
    p["steps"]["1"]["values"]["repeats"] = 5
    pf.write_text(json.dumps(p))
    assert [m.path for m in check_protocol(pf)] == ["step 1 step_sha256", "protocol_sha256"]
    assert check(repo) == 4


def test_dirty_tree_refuses(repo, capsys):
    (repo / "src" / "pkg" / "a.py").write_text("A = 9\n")
    assert freeze(repo, 1) == 2
    assert "uncommitted changes in matched files: src/pkg/a.py" in capsys.readouterr().err
    assert not (repo / "protocol" / "v1.json").exists()
    commit(repo)
    (repo / "src" / "pkg" / "c.py").write_text("C = 1\n")  # untracked, matched by the glob
    assert freeze(repo, 1) == 2
    assert "src/pkg/c.py" in capsys.readouterr().err


def test_refreeze_refuses(repo, capsys):
    assert freeze(repo, 1) == 0
    before = (repo / "protocol" / "v1.json").read_bytes()
    assert freeze(repo, 1) == 2
    assert "already frozen" in capsys.readouterr().err
    assert (repo / "protocol" / "v1.json").read_bytes() == before


def test_step2_refusals_then_step2(repo, capsys):
    assert freeze(repo, 2) == 2  # step 1 first
    assert "step 1 is not frozen" in capsys.readouterr().err
    assert freeze(repo, 1) == 0
    p1 = json.loads((repo / "protocol" / "v1.json").read_text())
    assert freeze(repo, 2) == 2  # null values
    assert "null values: values.table_seed, values.n_table_cases" in capsys.readouterr().err
    (repo / "protocol" / "v1.spec.json").write_text(json.dumps(spec({"table_seed": 42, "n_table_cases": N})) + "\n")
    commit(repo)
    assert freeze(repo, 2) == 2  # the cases file is missing
    assert "data/table/cases.jsonl" in capsys.readouterr().err
    add_step2(repo)
    assert freeze(repo, 2) == 0
    p = json.loads((repo / "protocol" / "v1.json").read_text())
    assert p["steps"]["1"] == p1["steps"]["1"] and p["protocol_sha256"] != p1["protocol_sha256"]
    assert set(p["steps"]) == {"1", "2"} and check(repo) == 0


def test_glob_matching_nothing_refuses(repo, capsys):
    s = spec()
    s["steps"]["1"]["files"].append("src/none/*.py")
    (repo / "protocol" / "v1.spec.json").write_text(json.dumps(s))
    commit(repo)
    assert freeze(repo, 1) == 2
    assert "glob 'src/none/*.py' matches no file" in capsys.readouterr().err


# -- run grade ---------------------------------------------------------------------------------------------------

def run_args(out: Path, *extra: str) -> list[str]:
    return ["run", "--mock", "--n", str(N), "--adapters", "bm25,recent", "--budgets", "1000", "--repeats", "3",
            "--boot-reps", "50", "--out", str(out), *extra]


def test_exploratory_without_the_flag(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(run_args(out)) == 0
    m = F.read_json(out / "manifest.json")
    assert m["grade"] == "exploratory" and m["protocol"] is None
    assert F.read_json(out / "summary.json")["grade"] == "exploratory"
    capsys.readouterr()
    assert main(["report", str(out)]) == 0
    assert capsys.readouterr().out.startswith("grade exploratory\n")


def test_wrong_budget_refuses(repo, tmp_path, capsys):
    assert freeze(repo, 1) == 0
    out = tmp_path / "run"
    args = run_args(out, "--protocol", str(repo / "protocol/v1.json"))
    args[args.index("--budgets") + 1] = "500"
    assert main(args) == 2
    err = capsys.readouterr().err
    assert "check 2" in err and "budgets [500]" in err
    assert not out.exists()  # stopped before any file or call


def test_decider_not_in_the_list_refuses(repo, tmp_path, capsys):
    assert freeze(repo, 1) == 0
    out = tmp_path / "run"
    assert main(run_args(out, "--noisy", "0.1", "--protocol", str(repo / "protocol/v1.json"))) == 2
    assert "decider 'mock-noisy' is not in the frozen deciders" in capsys.readouterr().err
    assert not out.exists()


def test_check1_failure_refuses(repo, tmp_path, capsys):
    assert freeze(repo, 1) == 0
    (repo / "src" / "pkg" / "a.py").write_text("A = 2\n")
    assert main(run_args(tmp_path / "run", "--protocol", str(repo / "protocol/v1.json"))) == 2
    err = capsys.readouterr().err
    assert "check 1" in err and "src/pkg/a.py" in err


def test_step1_only_stays_exploratory_with_reason(repo, tmp_path, capsys):
    assert freeze(repo, 1) == 0
    out = tmp_path / "run"
    assert main(run_args(out, "--protocol", str(repo / "protocol/v1.json"))) == 0
    m = F.read_json(out / "manifest.json")
    assert m["grade"] == "exploratory"
    assert m["protocol"]["steps"] == ["1"] and m["protocol"]["protocol_sha256"] is not None
    assert "step 2" in m["protocol"]["exploratory_reason"]
    assert "exploratory: step 2" in capsys.readouterr().out


def test_table_grade_and_resume_rules(repo, tmp_path, capsys):
    assert freeze(repo, 1) == 0
    cases = add_step2(repo)
    assert freeze(repo, 2) == 0
    proto = str(repo / "protocol/v1.json")
    out = tmp_path / "run"
    assert main(run_args(out, "--cases", str(cases), "--protocol", proto)) == 0
    m = F.read_json(out / "manifest.json")
    assert m["grade"] == "table" and m["protocol"]["steps"] == ["1", "2"]
    assert F.read_json(out / "summary.json")["grade"] == "table"
    capsys.readouterr()
    assert main(["report", str(out)]) == 0
    assert capsys.readouterr().out.startswith("grade table\n")
    # a resume passes the same checks again; the same command resumes cleanly
    assert main(run_args(out, "--cases", str(cases), "--protocol", proto)) == 0
    assert F.read_json(out / "manifest.json")["attempts"] == 2
    # resuming without the flag would mix grades: refused
    with pytest.raises(Exception, match="grade"):
        main(run_args(out, "--cases", str(cases)))
    # other cases than the frozen table file: check 4
    other = run_args(tmp_path / "other", "--cases", str(cases), "--protocol", proto)
    other[other.index("--n") + 1] = str(N - 1)  # a cut of the frozen file is not the frozen file
    assert main(other) == 2
    assert "check 4" in capsys.readouterr().err


def test_grade_table_without_protocol_refuses(tmp_path):
    cs = dev_cases()[:2]
    from memory_bench.deciders.mock import MockOracleDecider
    from memory_bench.engine import RunError

    with pytest.raises(RunError, match="needs a protocol"):
        run(RunConfig(out_dir=tmp_path / "r", cases=cs, decider=MockOracleDecider(cs), grade="table"))


# -- check 3 (product adapters), without starting a product -------------------------------------------------------

def product_check(repo: Path, **kw):
    a = json.loads((repo / "configs/adapters/mem0-k25.json").read_text())
    args = {"decider_id": "mock-oracle", "adapters": ["bm25", a],
            "adapter_files": {"mem0/k25": str(repo / "configs/adapters/mem0-k25.json")}, "budgets": [1000],
            "repeats": 3, "boot_seed": 11, "boot_reps": 50, "memory_side": str(repo / "configs/memory-side/m.json"),
            "cases_sha256": "x", "is_product": needs_proxy}
    args.update(kw)
    return check_run(repo / "protocol/v1.json", **args)


def test_product_checks(repo, monkeypatch):
    from importlib import metadata

    import memory_bench.engine.freeze as fz

    assert freeze(repo, 1) == 0
    monkeypatch.setattr(fz.metadata, "version", lambda pkg: "2.2.1")
    assert product_check(repo)["grade"] == "exploratory"
    monkeypatch.setattr(fz.metadata, "version", lambda pkg: "2.3.0")
    with pytest.raises(ProtocolError, match="check 3.*mem0ai 2.3.0 is installed, the frozen pin is 2.2.1"):
        product_check(repo)

    def missing(pkg):
        raise metadata.PackageNotFoundError(pkg)

    monkeypatch.setattr(fz.metadata, "version", missing)
    with pytest.raises(ProtocolError, match="check 3.*mem0ai is not installed in this environment"):
        product_check(repo)
    monkeypatch.setattr(fz.metadata, "version", lambda pkg: "2.2.1")
    with pytest.raises(ProtocolError, match="check 3.*give its adapter config file"):
        product_check(repo, adapter_files={})
    other = repo / "k25-copy.json"
    other.write_bytes((repo / "configs/adapters/mem0-k25.json").read_bytes())
    with pytest.raises(ProtocolError, match="check 3.*is not a frozen file"):
        product_check(repo, adapter_files={"mem0/k25": str(other)})
    with pytest.raises(ProtocolError, match="check 3.*memory-side config as a file"):
        product_check(repo, memory_side={"id": "m"})
    with pytest.raises(ProtocolError, match="check 3.*is not a frozen file"):
        product_check(repo, memory_side=str(other))
