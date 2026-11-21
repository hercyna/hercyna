"""`bench report` rebuilds summary.json byte for byte (story S4.2)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from memory_bench.cli import main
from memory_bench.engine import rebuild_summary

ROOT = Path(__file__).resolve().parent.parent
DAY4 = ROOT / "results" / "runs" / "day4-baselines-haiku"


def test_report_rebuilds_same_bytes(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["run", "--mock", "--noisy", "0.2", "--n", "20", "--adapters", "bm25,recent", "--budgets", "1000",
                 "--repeats", "3", "--boot-reps", "100", "--out", str(out)]) == 0
    summary_bytes = (out / "summary.json").read_bytes()
    manifest_bytes = (out / "manifest.json").read_bytes()
    (out / "summary.json").unlink()
    capsys.readouterr()
    assert main(["report", str(out)]) == 0
    assert (out / "summary.json").read_bytes() == summary_bytes
    manifest = json.loads(manifest_bytes)
    assert hashlib.sha256(summary_bytes).hexdigest() == manifest["files"]["summary.json"]
    assert (out / "manifest.json").read_bytes() == manifest_bytes


def test_day4_run_rebuilds_to_recorded_hash(tmp_path):
    if not (DAY4 / "metrics.jsonl").exists():
        pytest.skip("day 4 run folder not present")
    for name in ("metrics.jsonl", "manifest.json"):
        shutil.copy(DAY4 / name, tmp_path / name)
    rebuild_summary(tmp_path)
    data = (tmp_path / "summary.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == "cfb38871a2590a37aec7f112de0d9450fb6a31da73757a71bd85497549612b93"
    # Day 9 adds only the grade key (docs/freeze.md section 4); without it the recorded Day 4 bytes come back.
    summary = json.loads(data)
    assert summary.pop("grade") == "exploratory"
    old = json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    assert hashlib.sha256(old.encode()).hexdigest() == "c0718ae12b0afb8980a3740c6db5a08860db830574d260f002a8bbb6b56a6459"
