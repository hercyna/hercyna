"""build_prompt equals lifted.build_messages byte for byte on real dev-case views."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from memory_bench.adapters import get_adapter
from memory_bench.adapters.base import enforce_budget, records_of
from memory_bench.cases.sample import dev_cases
from memory_bench.deciders import lifted
from memory_bench.deciders.base import DecisionInput
from memory_bench.deciders.prompts import build_prompt, prompt_hashes, with_reminder


def _canon(msgs) -> bytes:
    return json.dumps(msgs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _views(case):
    out = []
    for name, budget in (("full", None), ("bm25", 300), ("recent", 120)):
        a = get_adapter(name)
        a.reset(case.case_id)
        a.ingest(records_of(case))
        got = enforce_budget(a.retrieve(case.query_text, budget or 0), budget or 0, reference=a.reference)
        out.append(got.text)
    return out + [""]  # an empty view gives "(no records)" on both sides


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_build_prompt_equals_lifted(version):
    cases = dev_cases()
    picked = cases[:12] + cases[-6:]  # every mechanism, base and U2 cases
    n = 0
    for case in picked:
        for text in _views(case):
            inp = DecisionInput(case_id=case.case_id, query_text=case.query_text, records_text=text)
            view = SimpleNamespace(text=text)
            stand_in = SimpleNamespace(query_text=case.query_text)
            assert _canon(build_prompt(inp, version)) == _canon(lifted.build_messages(view, [], stand_in, version))
            n += 1
    assert n == len(picked) * 4


def test_reminder_matches_lifted_retry():
    inp = DecisionInput(case_id="c", query_text="q?", records_text="r1")
    msgs = build_prompt(inp)
    again = with_reminder(msgs)
    assert again[-1]["content"] == msgs[-1]["content"] + "\n\n" + lifted.REMINDER
    assert msgs[-1]["content"].endswith(lifted.REPLY["v1"])  # the original is not touched


def test_prompt_hashes_stable_and_versioned():
    h1, h2 = prompt_hashes("v1"), prompt_hashes("v2")
    assert set(h1) == {"system", "context", "policy", "reply", "reminder", "reply_format", "action_schema"}
    assert all(len(v) == 64 for v in h1.values())
    assert h1 == prompt_hashes("v1")
    assert h1["system"] != h2["system"] and h1["context"] == h2["context"]
    with pytest.raises(ValueError):
        build_prompt(DecisionInput(case_id="c", query_text="q", records_text="r"), "v9")
