"""`bench run --mock` then `bench report`, through cli.main."""

from __future__ import annotations

from memory_bench.cli import main


def test_run_then_report(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["run", "--mock", "--n", "5", "--adapters", "bm25,recent", "--budgets", "1000",
                 "--repeats", "2", "--boot-reps", "100", "--out", str(out)]) == 0
    assert (out / "metrics.jsonl").exists()
    capsys.readouterr()
    assert main(["report", str(out)]) == 0
    text = capsys.readouterr().out
    assert "decider mock-oracle" in text
    assert "n_cases 5  n_gated 5" in text
    assert "validity" in text
    assert "wrong commit" in text and "headline" in text and "right action" in text
    assert "bm25/default@1000" in text and "recent/default@1000" in text


def test_run_noisy_and_cases_file(tmp_path, capsys):
    cases = tmp_path / "cases.jsonl"
    assert main(["cases", "dev", "--out", str(cases)]) == 0
    out = tmp_path / "noisy"
    assert main(["run", "--mock", "--noisy", "0.2", "--n", "4", "--cases", str(cases), "--repeats", "1",
                 "--boot-reps", "50", "--out", str(out)]) == 0
    assert main(["report", str(out)]) == 0
    assert "decider mock-noisy" in capsys.readouterr().out


def test_run_without_mock_is_refused(tmp_path):
    assert main(["run", "--n", "1", "--out", str(tmp_path / "x")]) == 2
