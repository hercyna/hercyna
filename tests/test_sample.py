"""Sampling functions lifted from the LENS headline engine (the parts that rebuild the case sample)."""

from __future__ import annotations

from collections import Counter

import pytest

from memory_bench.cases.sample import make_headline_sample, sample_mix, u2_pair_for


def test_sample_mix_lengths_and_determinism():
    cs = make_headline_sample(60, 3)
    assert Counter(c.mechanism for c in cs) == {"M0": 30, "M1": 6, "M2": 6, "M3": 6, "U1": 6, "U2": 6}
    for m in ("M0", "M1", "U2"):
        assert Counter(c.knobs["length"] for c in cs if c.mechanism == m) in ({100: 15, 400: 15}, {100: 3, 400: 3})
    assert len({c.knobs["distance"] for c in cs}) == 3
    again = make_headline_sample(60, 3)
    assert [c.case_id for c in cs] == [c.case_id for c in again]
    assert [c.seed for c in cs] == [c.seed for c in again]


def test_v2_and_v3_differ_in_u2_cases_only():
    v2 = make_headline_sample(300, 11, generator_version=2)
    v3 = make_headline_sample(300, 11)
    changed = [a.mechanism for a, b in zip(v2, v3) if a.to_dict() != b.to_dict()]
    assert changed and set(changed) == {"U2"}
    assert [c.case_id for c in v2] == [c.case_id for c in v3]


def test_u2_pairs_split_half_and_half_and_cross_length():
    pairs = [u2_pair_for(i) for i in range(30)]
    assert Counter(pairs) == {p: 15 for p in set(pairs)} and len(set(pairs)) == 2
    for p in set(pairs):
        assert Counter(i % 2 for i, q in enumerate(pairs) if q == p) == {0: 8, 1: 7} or \
            Counter(i % 2 for i, q in enumerate(pairs) if q == p) == {0: 7, 1: 8}


def test_subset_sample_uses_prefix_and_only_chosen_mechanisms():
    cs = make_headline_sample(30, 12, mechanisms=("U2",), prefix="u3")
    assert {c.mechanism for c in cs} == {"U2"}
    assert [c.case_id for c in cs][:2] == ["u3-0000", "u3-0001"]


def test_sample_mix_rejects_unknown_or_repeated_mechanisms():
    with pytest.raises(ValueError):
        sample_mix(("X9",))
    with pytest.raises(ValueError):
        sample_mix(("U2", "U2"))
    assert set(sample_mix(("U2",))) == {"U2"}


def test_full_sample_is_unchanged_by_the_new_arguments():
    a = make_headline_sample(60, 3)
    b = make_headline_sample(60, 3, mechanisms=None, prefix=None, generator_version=3)
    assert [c.to_dict() for c in a] == [c.to_dict() for c in b]
    assert all(c.case_id.startswith("h3-") for c in a)


def test_v2_mode_regenerates_headline_300_byte_for_byte(tmp_path):
    import hashlib

    from memory_bench.cases.schema import write_jsonl

    out = tmp_path / "cases.jsonl"
    write_jsonl(out, make_headline_sample(300, 11, generator_version=2))
    # the hash frozen in LENS protocol v2 for results/headline-300/cases.jsonl
    assert hashlib.sha256(out.read_bytes()).hexdigest() == \
        "74a1e3a421da9e658cf930f5017a92bfa92d775af6dfb0f14b2cdcb7c0731374"
