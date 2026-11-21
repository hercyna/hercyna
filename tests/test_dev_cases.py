"""R1: the 300 development cases regenerate byte for byte."""

from __future__ import annotations

import hashlib
from collections import Counter

from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import write_jsonl

R1_SHA256 = "432e55ca579b74766e9097428fe513bd5a8c96496892dec1b023365ba1a4ed3c"


def test_dev_cases_sha256_equals_the_released_file(tmp_path):
    out = tmp_path / "cases.jsonl"
    assert write_jsonl(out, dev_cases()) == 300
    assert hashlib.sha256(out.read_bytes()).hexdigest() == R1_SHA256


def test_dev_cases_composition():
    cs = dev_cases()
    assert Counter(c.mechanism for c in cs) == {"M0": 150, "M1": 30, "M2": 30, "M3": 30, "U1": 30, "U2": 30}
    assert [c.case_id[:3] for c in cs[:270]].count("u3-") == 0
    assert all(c.case_id.startswith("u3-") for c in cs[270:])
    assert all(c.mechanism == "U2" for c in cs[270:])
