"""An adapter cannot reach labels: records are plain strings and the baselines never touch a Case."""

from __future__ import annotations

import ast
import dataclasses
import gc
from pathlib import Path

import pytest

from memory_bench.adapters import base as base_mod
from memory_bench.adapters.base import Record, records_of
from memory_bench.cases.render import render_lines
from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import Case, Event

ADAPTER_DIR = Path(base_mod.__file__).parent
BASELINE_FILES = ["full.py", "bm25.py", "recent.py"]


@pytest.fixture(scope="module")
def cases():
    return dev_cases()


def test_record_fields_exact():
    assert [f.name for f in dataclasses.fields(Record)] == ["id", "date", "text"]
    r = Record("e1", "2011-01-01", "e1 2011-01-01 x")
    assert set(vars(r)) == {"id", "date", "text"}


def _assert_plain(recs, case):
    for r in recs:
        assert type(r) is Record
        for name in ("id", "date", "text"):
            assert type(getattr(r, name)) is str
        for ref in gc.get_referents(r) + list(vars(r).values()):
            assert not isinstance(ref, (Case, Event))
            assert not (isinstance(ref, (list, dict)) and ref is case.knobs)
    # the only things in the output are the rendered lines
    assert [r.text for r in recs] == render_lines(case, [e.id for e in case.events])
    assert [r.id for r in recs] == [e.id for e in case.events]
    assert [r.date for r in recs] == [e.date for e in case.events]


def test_records_are_plain_strings(cases):
    for c in cases[:50]:
        _assert_plain(records_of(c), c)


def test_no_label_attributes_or_text(cases):
    for c in cases[:50]:
        for r in records_of(c):
            for label in ("mechanism", "cls", "knobs", "a_star", "d_star", "deleted_ids"):
                assert not hasattr(r, label)
            assert "Case(" not in repr(r) and "Event(" not in repr(r)
            assert str(c.knobs) not in r.text and (not c.knobs or repr(c.knobs) not in repr(r))
            assert f"mechanism" not in r.text and c.case_id not in r.text


def test_leaky_bridge_is_caught(cases):
    c = cases[0]

    @dataclasses.dataclass(frozen=True)
    class LeakyRecord:
        id: str
        date: str
        text: str
        event: object

    def leaky_records_of(case):
        return [LeakyRecord(e.id, e.date, "x", e) for e in case.events]

    with pytest.raises(AssertionError):
        _assert_plain(leaky_records_of(c), c)

    class StrLeak(str):
        pass

    def leaky_text(case):
        return [Record(e.id, e.date, f"{e.id} {e.date} mechanism={case.mechanism}") for e in case.events]

    with pytest.raises(AssertionError):
        _assert_plain(leaky_text(c), c)


def _case_uses(src: str, allowed_func: str | None) -> list[str]:
    """Places where `Case` is imported or named outside `allowed_func`."""
    tree = ast.parse(src)
    allowed: set[int] = set()
    if allowed_func:
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == allowed_func:
                allowed |= {id(x) for x in ast.walk(n)}
    bad: list[str] = []
    for n in ast.walk(tree):
        if id(n) in allowed:
            continue
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names]
            mod = getattr(n, "module", "") or ""
            if "Case" in names or "schema" in mod or "schema" in names or "cases" in mod.split("."):
                if allowed_func and isinstance(n, ast.ImportFrom) and names == ["Case"]:
                    continue  # the one import that records_of needs
                bad.append(ast.unparse(n))
        elif isinstance(n, ast.Name) and n.id == "Case":
            bad.append(f"name Case line {n.lineno}")
        elif isinstance(n, ast.Attribute) and n.attr in ("Case", "schema"):
            bad.append(f"attr {n.attr} line {n.lineno}")
    return bad


@pytest.mark.parametrize("fname", BASELINE_FILES)
def test_baselines_do_not_touch_case(fname):
    assert _case_uses((ADAPTER_DIR / fname).read_text(), None) == []


def test_base_uses_case_only_in_records_of():
    assert _case_uses((ADAPTER_DIR / "base.py").read_text(), "records_of") == []


def test_checker_catches_a_leaky_adapter():
    leaky = (
        "from ..cases.schema import Case\n"
        "class Leaky:\n"
        "    def ingest(self, case: Case):\n"
        "        self.a = case.a_star\n"
    )
    assert _case_uses(leaky, None)
    sneaky = "import memory_bench.cases.schema as s\nx = s.Case\n"
    assert _case_uses(sneaky, None)
    leaky_base = (
        "from ..cases.schema import Case\n"
        "def records_of(case: Case): return []\n"
        "def enforce_budget(r, b, case: Case): return r\n"
    )
    assert _case_uses(leaky_base, "records_of")
    with pytest.raises(AssertionError):
        assert _case_uses(leaky, None) == []
