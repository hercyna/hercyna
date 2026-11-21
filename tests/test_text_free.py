"""Text-free scanner: released run files hold no view, note or prompt text (charter section 5 rule 8)."""

from __future__ import annotations

import json
import shutil

from memory_bench.adapters.base import records_of
from memory_bench.cases.sample import dev_cases
from memory_bench.cli import main
from memory_bench.deciders import lifted
from memory_bench.submit.textfree import RELEASED_FILES, iter_strings, scan_run

N = 20


def mock_run(out) -> None:
    assert main(["run", "--mock", "--n", str(N), "--adapters", "bm25,recent", "--budgets", "1000",
                 "--repeats", "3", "--boot-reps", "100", "--out", str(out)]) == 0


def source_lines() -> set[str]:
    texts: list[str] = []
    for case in dev_cases()[:N]:
        texts.append(case.query_text)
        texts.extend(r.text for r in records_of(case))
    for const in (lifted.SYSTEM, lifted.CONTEXT, lifted.POLICY, lifted.REPLY, lifted.REMINDER):
        texts.extend(const.values() if isinstance(const, dict) else [const])
    return {ln.strip() for t in texts for ln in t.splitlines() if len(ln.strip()) >= 25}


def test_no_source_text_in_released_files(tmp_path):
    out = tmp_path / "run"
    mock_run(out)
    lines = source_lines()
    assert len(lines) >= 100
    raw = {n: (out / n).read_text(encoding="utf-8") for n in RELEASED_FILES if (out / n).exists()}
    assert raw
    for name, text in raw.items():
        for ln in lines:
            assert ln not in text, f"{name} holds source text of {len(ln)} characters"


def test_scan_flags_planted_text(tmp_path):
    out = tmp_path / "run"
    mock_run(out)
    case = dev_cases()[0]
    secret = records_of(case)[0].text
    p = out / "metrics.jsonl"
    rows = p.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["note"] = secret
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    found = scan_run(out)
    assert len(found) == 1
    v = found[0]
    assert v.file == "metrics.jsonl" and v.line == len(rows) + 1 and v.path == ".note"
    assert v.length == len(secret)
    assert secret not in repr(v)


def test_clean_run_has_no_violations(tmp_path):
    out = tmp_path / "run"
    mock_run(out)
    assert scan_run(out) == []


def test_iter_strings_paths():
    obj = {"a": {"b": [{"c": "x"}, "y"]}, "k": "v", "n": 3}
    assert list(iter_strings(obj)) == [
        (".a", "a"), (".a.b", "b"), (".a.b[].c", "c"), (".a.b[].c", "x"), (".a.b[]", "y"),
        (".k", "k"), (".k", "v"), (".n", "n"),
    ]


def test_day4_run_is_text_free():
    # The real run holds honest prose the rule must allow: a price source in manifest.json, a unit note in summary.json.
    import pytest
    from pathlib import Path

    day4 = Path(__file__).resolve().parent.parent / "results" / "runs" / "day4-baselines-haiku"
    if not (day4 / "metrics.jsonl").exists():
        pytest.skip("day 4 run folder not present")
    assert scan_run(day4) == []


def test_rule_cases():
    from memory_bench.submit.textfree import is_free_text

    for path, ok in ((".action", "REQUEST_INFO"), (".deciders.haiku-5-5.cells", "bm25/default@1000"),
                     (".created", "2026-11-19T08:46:17+00:00"), (".view_sha256", "a" * 64), (".x", ""),
                     (".deciders[].price.source", "live price taken as twice the batch price recorded 2026-10-16")):
        assert not is_free_text(path, ok), ok
    line = "A payment of 40.00 was received on the account."
    for path, bad in ((".note", line), (".deciders[].price.source", line * 5),
                      (".deciders[].price.source", "one line\nand another"), (".view_sha256", "a" * 65),
                      (".error", "adapter failed: " + line)):
        assert is_free_text(path, bad), path
