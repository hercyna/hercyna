"""bench submit pack and bench verify (docs/submission.md sections 1 to 3).

Exploratory paths use a mock run in a temp dir. Table-grade paths use a temp git repo with a temp protocol, the
same way tests/test_freeze.py does. Each tamper test asserts the first check that fails.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import write_jsonl
from memory_bench.cli import main
from memory_bench.engine import files as F
from memory_bench.scoring import summarise
from memory_bench.submit.pack import PackError, pack
from memory_bench.submit.verify import verify

N = 4
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
SECRET = "the account holder asked us to wait until Friday"


def run_args(out: Path, cases: Path, repeats: int = 2, reps: int = 50, *extra: str) -> list[str]:
    return ["run", "--mock", "--n", str(N), "--cases", str(cases), "--adapters", "bm25", "--budgets", "1000",
            "--repeats", str(repeats), "--boot-reps", str(reps), "--out", str(out), *extra]


def rehash(sub: Path, name: str) -> None:
    m = F.read_json(sub / F.MANIFEST)
    m["files"][name] = F.sha256_file(sub / name)
    F.write_json(sub / F.MANIFEST, m)


def rows_of(sub: Path) -> list[dict]:
    return F.read_metrics(sub / F.METRICS)


def write_rows(sub: Path, rows: list[dict]) -> None:
    (sub / F.METRICS).write_text("".join(F.row_line(r) + "\n" for r in rows), encoding="utf-8")


def first(res) -> int:
    assert not res.ok
    return res.failed_checks[0]


@pytest.fixture
def mock_sub(tmp_path) -> tuple[Path, Path]:
    """(submission folder, case file) of an untouched exploratory pack of a mock run."""
    cases = tmp_path / "cases.jsonl"
    write_jsonl(cases, dev_cases()[:N])
    run_dir = tmp_path / "run"
    assert main(run_args(run_dir, cases)) == 0
    return pack(run_dir, tmp_path / "submissions", exploratory=True), cases


# -- pack ---------------------------------------------------------------------------------------------------------

def test_pack_layout_and_submission_json(mock_sub, tmp_path):
    sub, _ = mock_sub
    m = F.read_json(sub / F.MANIFEST)
    assert sub.parent.name == "bm25" and sub.name == f"{m['finished'][:10]}-default"
    assert sorted(p.name for p in sub.iterdir()) == sorted(
        ["manifest.json", "metrics.jsonl", "summary.json", "spend.json", "health.json", "timings.jsonl",
         "submission.json"])
    for name in m["files"]:  # copied unchanged
        assert (sub / name).read_bytes() == (tmp_path / "run" / name).read_bytes()
    s = F.read_json(sub / "submission.json")
    assert s == {"schema": "memory_bench.submission/1", "system": "bm25", "config_label": "default",
                 "product_version": "builtin", "submitter": None, "organisation": None, "adapter_source": None,
                 "run_id": m["run_id"], "protocol_sha256": None, "reproduced": None, "grade": "exploratory"}


def test_pack_never_copies_private_and_refuses(tmp_path):
    cases = tmp_path / "cases.jsonl"
    write_jsonl(cases, dev_cases()[:N])
    run_dir = tmp_path / "run"
    assert main(run_args(run_dir, cases)) == 0
    (run_dir / "private").mkdir()
    (run_dir / "private" / "views.jsonl").write_text(json.dumps({"text": SECRET}) + "\n")
    with pytest.raises(PackError, match="table-grade"):
        pack(run_dir, tmp_path / "s")
    with pytest.raises(PackError, match="slug"):
        pack(run_dir, tmp_path / "s", slug="Not A Slug", exploratory=True)
    dest = pack(run_dir, tmp_path / "s", slug="k25-try", exploratory=True)
    assert dest.name.endswith("-k25-try") and not (dest / "private").exists()
    with pytest.raises(PackError, match="not empty"):
        pack(run_dir, tmp_path / "s", slug="k25-try", exploratory=True)
    m = F.read_json(run_dir / F.MANIFEST)
    m["state"] = "failed"
    F.write_json(run_dir / F.MANIFEST, m)
    with pytest.raises(PackError, match="complete"):
        pack(run_dir, tmp_path / "s2", exploratory=True)


def test_pack_cli_exit_codes(tmp_path, capsys):
    cases = tmp_path / "cases.jsonl"
    write_jsonl(cases, dev_cases()[:N])
    run_dir = tmp_path / "run"
    assert main(run_args(run_dir, cases)) == 0
    assert main(["submit", "pack", str(run_dir), "--out", str(tmp_path / "s")]) == 2
    assert "refused" in capsys.readouterr().err
    assert main(["submit", "pack", str(run_dir), "--out", str(tmp_path / "s"), "--exploratory"]) == 0


# -- verify: untouched ----------------------------------------------------------------------------------------------

def test_untouched_mock_pack_passes(mock_sub, capsys):
    sub, cases = mock_sub
    res = verify(sub, cases=cases)
    assert res.ok and res.grade == "exploratory" and res.skipped == [6]
    res = verify(sub)  # no case file for an exploratory run: check 4 is skipped too
    assert res.ok and res.skipped == [4, 6]
    assert main(["verify", str(sub), "--cases", str(cases)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["verify: pass, exploratory (check 6 skipped)"]


# -- verify: tamper tests (docs/submission.md section 3) -----------------------------------------------------------

def test_edited_action_fails_5_and_1(mock_sub, capsys):
    sub, cases = mock_sub
    rows = rows_of(sub)
    i = next(i for i, r in enumerate(rows) if r["kind"] == "view")
    rows[i]["action"] = "ESCALATE" if rows[i]["action"] != "ESCALATE" else "EXPLAIN"
    write_rows(sub, rows)
    res = verify(sub, cases=cases)
    assert res.failed_checks == [1, 5]
    assert main(["verify", str(sub), "--cases", str(cases)]) == 5
    assert capsys.readouterr().out.splitlines()[-1].startswith("verify: FAIL at check(s) 1, 5")
    rehash(sub, F.METRICS)  # with the hash updated it still fails, at check 5
    assert verify(sub, cases=cases).failed_checks == [5]


def test_flipped_a_star_with_hash_updated_fails_4(mock_sub):
    sub, cases = mock_sub
    rows = rows_of(sub)
    rows[0]["a_star"] = "ESCALATE" if rows[0]["a_star"] != "ESCALATE" else "EXPLAIN"
    write_rows(sub, rows)
    rehash(sub, F.METRICS)
    res = verify(sub, cases=cases)
    assert first(res) == 4
    assert any("a_star differs" in f.detail for f in res.failures if f.check == 4)


def test_deleted_row_with_hashes_updated_fails_3(mock_sub):
    sub, cases = mock_sub
    rows = rows_of(sub)
    gone = rows.pop(next(i for i, r in enumerate(rows) if r["kind"] == "view"))
    write_rows(sub, rows)
    rehash(sub, F.METRICS)
    m = F.read_json(sub / F.MANIFEST)
    F.write_json(sub / F.SUMMARY, {**summarise(rows, seed=m["bootstrap"]["seed"], reps=m["bootstrap"]["reps"]),
                                   "grade": m["grade"]})
    rehash(sub, F.SUMMARY)
    res = verify(sub, cases=cases)
    assert res.failed_checks == [3]
    assert [f.detail for f in res.failures][0].startswith(f"missing row (case_id={gone['case_id']}")


def test_view_line_in_metrics_fails_2_and_prints_no_text(mock_sub, capsys):
    sub, cases = mock_sub
    row = rows_of(sub)[0]
    view = {"case_id": row["case_id"], "system": "bm25", "config_label": "default", "text": SECRET,
            "units": [SECRET], "record_ids": []}
    with (sub / F.METRICS).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(view, sort_keys=True) + "\n")
    rehash(sub, F.METRICS)
    res = verify(sub, cases=cases)
    assert first(res) == 2
    assert main(["verify", str(sub), "--cases", str(cases)]) == 5
    out = capsys.readouterr().out
    assert "Friday" not in out and "account holder" not in out


def test_summary_rebuilt_with_another_seed_fails_5(mock_sub):
    sub, cases = mock_sub
    m = F.read_json(sub / F.MANIFEST)
    F.write_json(sub / F.SUMMARY, {**summarise(rows_of(sub), seed=12, reps=m["bootstrap"]["reps"]),
                                   "grade": m["grade"]})
    rehash(sub, F.SUMMARY)
    assert verify(sub, cases=cases).failed_checks == [5]


def test_reproduced_pointing_at_missing_run_fails_7(mock_sub, tmp_path):
    sub, cases = mock_sub
    s = F.read_json(sub / "submission.json")
    s["reproduced"] = {"run_id": "no-such-run", "at": "2026-11-19"}
    F.write_json(sub / "submission.json", s)
    res = verify(sub, cases=cases, runs=tmp_path / "runs")
    assert res.failed_checks == [7] and "no-such-run" in res.failures[0].detail


# -- verify: other failures --------------------------------------------------------------------------------------

def test_extra_file_or_folder_fails_1(mock_sub):
    sub, cases = mock_sub
    (sub / "private").mkdir()
    (sub / "private" / "views.jsonl").write_text(json.dumps({"text": SECRET}) + "\n")
    (sub / "notes.txt").write_text(SECRET)
    res = verify(sub, cases=cases)
    assert res.failed_checks == [1] and len(res.failures) == 2


def test_submission_mismatch_fails_7(mock_sub):
    sub, cases = mock_sub
    s = F.read_json(sub / "submission.json")
    s["product_version"] = "9.9.9"
    F.write_json(sub / "submission.json", s)
    assert verify(sub, cases=cases).failed_checks == [7]


def test_missing_manifest_stops(tmp_path):
    (tmp_path / "s").mkdir()
    res = verify(tmp_path / "s")
    assert res.failed_checks == [1] and res.grade is None


# -- table grade: temp git repo and temp protocol ------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=True).stdout


def commit(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")


@pytest.fixture
def table(tmp_path) -> dict:
    """A temp repo with steps 1 and 2 frozen, a table-grade run under runs/, and its pack."""
    r = tmp_path / "repo"
    (r / "src" / "pkg").mkdir(parents=True)
    (r / "src" / "pkg" / "a.py").write_text("A = 1\n")
    (r / "protocol").mkdir()
    (r / "protocol" / "PREREGISTRATION.md").write_text("hypotheses\n")
    cases = r / "data" / "table" / "cases.jsonl"
    cases.parent.mkdir(parents=True)
    write_jsonl(cases, dev_cases()[:N])
    spec = {"schema": "memory_bench.protocol_spec/1", "version": "v1", "steps": {
        "1": {"title": "metric", "files": ["src/pkg/*.py", "protocol/PREREGISTRATION.md"],
              "values": {"deciders": ["mock-oracle"], "systems": ["full", "bm25", "recent"], "budgets": [1000],
                         "repeats": 3, "boot_seed": 11, "boot_reps": 50}},
        "2": {"title": "table", "files": ["data/table/cases.jsonl"], "values": {"table_seed": 42, "n_table_cases": N}}}}
    (r / "protocol" / "v1.spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    git(r, "init", "-q")
    commit(r)
    proto = r / "protocol" / "v1.json"
    for step in ("1", "2"):
        assert main(["freeze", "--step", step, "--spec", str(r / "protocol/v1.spec.json"), "--out", str(proto)]) == 0
    runs = tmp_path / "runs"
    run_dir = runs / "trun"
    # From the repo root with relative paths, as a maintainer runs: the manifest keeps the paths as given, and a
    # long absolute path is not a token, so the text-free check (2) would flag it.
    cwd = Path.cwd()
    os.chdir(r)
    try:
        assert main(run_args(run_dir, Path("data/table/cases.jsonl"), 3, 50, "--protocol", "protocol/v1.json")) == 0
    finally:
        os.chdir(cwd)
    assert F.read_json(run_dir / F.MANIFEST)["grade"] == "table"
    sub = pack(run_dir, tmp_path / "submissions", slug="t")
    return {"repo": r, "proto": proto, "cases": cases, "runs": runs, "run": run_dir, "sub": sub}


def test_table_pack_passes_with_defaults(table, monkeypatch, capsys):
    s = F.read_json(table["sub"] / "submission.json")
    assert "grade" not in s and s["protocol_sha256"] == F.read_json(table["proto"])["protocol_sha256"]
    with pytest.raises(PackError, match="drop --exploratory"):
        pack(table["run"], table["sub"].parent.parent / "x", exploratory=True)
    res = verify(table["sub"], protocol=table["proto"], cases=table["cases"])
    assert res.ok and res.grade == "table" and res.skipped == []
    monkeypatch.chdir(table["repo"])  # protocol/v1.json and data/table/cases.jsonl by default
    assert verify(table["sub"]).ok
    assert main(["verify", str(table["sub"])]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "verify: pass, grade table"


def test_table_cases_relabelled_file_fails_4(table, tmp_path):
    other = tmp_path / "other.jsonl"
    write_jsonl(other, dev_cases()[N:2 * N])
    res = verify(table["sub"], protocol=table["proto"], cases=other)
    assert first(res) == 4


def test_protocol_hash_changed_fails_6(table):
    sub = table["sub"]
    m = F.read_json(sub / F.MANIFEST)
    m["protocol"]["protocol_sha256"] = "0" * 64
    F.write_json(sub / F.MANIFEST, m)
    res = verify(sub, protocol=table["proto"], cases=table["cases"])
    assert first(res) == 6 and res.failed_checks == [6, 7]


def test_frozen_file_edited_fails_6(table):
    (table["repo"] / "src" / "pkg" / "a.py").write_text("A = 2\n")
    res = verify(table["sub"], protocol=table["proto"], cases=table["cases"])
    assert res.failed_checks == [6] and "src/pkg/a.py" in res.failures[0].detail


def test_frozen_values_differ_fails_6(table):
    sub = table["sub"]
    m = F.read_json(sub / F.MANIFEST)
    m["repeats"] = 2  # also breaks the row plan (check 3): the first failure is 3, and 6 names the value
    F.write_json(sub / F.MANIFEST, m)
    res = verify(sub, protocol=table["proto"], cases=table["cases"])
    assert 6 in res.failed_checks
    assert any("repeats 2 differ from the frozen 3" in f.detail for f in res.failures if f.check == 6)


def test_reproduced_by_a_table_run_passes(table, tmp_path):
    other_sub = pack(table["run"], tmp_path / "subs2", slug="t")
    s = F.read_json(other_sub / "submission.json")
    s["reproduced"] = {"run_id": "trun", "at": "2026-11-19"}
    F.write_json(other_sub / "submission.json", s)
    assert verify(other_sub, protocol=table["proto"], cases=table["cases"], runs=table["runs"]).ok
    s["reproduced"]["run_id"] = "../trun"
    F.write_json(other_sub / "submission.json", s)
    assert verify(other_sub, protocol=table["proto"], cases=table["cases"], runs=table["runs"]).failed_checks == [7]
