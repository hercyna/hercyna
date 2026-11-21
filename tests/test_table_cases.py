"""bench cases table (D7, docs/freeze.md step 2)."""

from collections import Counter

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import read_jsonl
from memory_bench.cli import main, table_cases


def test_table_cases_are_deterministic_and_balanced():
    a = table_cases(424242, 60)
    b = table_cases(424242, 60)
    assert [c.case_id for c in a] == [c.case_id for c in b]
    assert [c.a_star for c in a] == [c.a_star for c in b]
    counts = Counter(c.mechanism for c in table_cases(424242, 300))
    assert counts == {"M0": 150, "M1": 30, "M2": 30, "M3": 30, "U1": 30, "U2": 30}
    assert all(c.case_id.startswith("t424242-") for c in a)


def test_table_cases_share_nothing_with_dev():
    dev = {c.case_id for c in dev_cases()}
    assert not dev & {c.case_id for c in table_cases(424242, 300)}


def test_cli_refuses_dev_seeds(tmp_path, capsys):
    assert main(["cases", "table", "--seed", "11", "--out", str(tmp_path / "c.jsonl")]) == 2
    assert main(["cases", "table", "--seed", "7", "--n", "12", "--out", str(tmp_path / "c.jsonl")]) == 0
    assert len(list(read_jsonl(tmp_path / "c.jsonl"))) == 12
