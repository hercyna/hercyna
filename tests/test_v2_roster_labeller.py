"""v2 tests: Step 3 paid decider roster, Step 4 labeller record and frozen paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_bench.engine import files as F
from memory_bench.engine.freeze import ProtocolError, check_protocol, check_run, frozen_deciders, load_protocol
from memory_bench.cases.schema import read_jsonl

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_r4_protocol_v2_steps_1_to_4_frozen_and_valid():
    v2_path = REPO_ROOT / "protocol" / "v2.json"
    assert check_protocol(v2_path) == []
    proto = load_protocol(v2_path)
    steps = proto["steps"]
    assert {"1", "2", "3", "4"}.issubset(set(steps))

    # Step 3: 3 paid open-weight OpenCode models + frozen transports.py
    s3 = steps["3"]
    assert len(s3["files"]) == 3
    s3v = s3["values"]
    assert s3v["deciders"] == [
        "opencode-deepseek-v4.1-flash",
        "opencode-glm-5.3-flash",
        "opencode-qwen3.8-flash",
    ]
    assert s3v["transport_path"] == "frozen_transports_py_tools_off"
    for entry in s3v["paid_phase1_deciders"]:
        assert entry["weights_status"] == "open-weight"
        assert entry["weights_url"].startswith("https://huggingface.co/")
        assert "served build not verified" in entry["weights_label"]
        assert entry["price"]["as_of"] == "2026-11-23"

    # Step 4: single human labeller (FNU Ratnavarma), presence rule vs human labels, inter-human not measured
    s4v = steps["4"]["values"]
    assert s4v["labeller_1"] == "FNU Ratnavarma"
    assert s4v["labeller_2"] == "FNU Ratnavarma (same person as labeller 1)"
    assert s4v["kappa_basis"] == "presence rule vs human labels"
    assert s4v["inter_human_agreement"] == "not measured"

    # frozen_deciders merges Step 1 and Step 3
    all_decs = frozen_deciders(steps)
    assert "haiku-5-5" in all_decs
    assert "opencode-deepseek-v4.1-flash" in all_decs
    assert "opencode-glm-5.3-flash" in all_decs
    assert "opencode-qwen3.8-flash" in all_decs


def test_r4_check_run_accepts_step3_decider_and_reports_unrun_budget():
    v2_path = REPO_ROOT / "protocol" / "v2.json"
    cases = list(read_jsonl(REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl"))
    res = check_run(
        v2_path,
        decider_id="opencode-deepseek-v4.1-flash",
        adapters=["bm25", "recent", "empty"],
        adapter_files={},
        budgets=[1000],
        repeats=3,
        boot_seed=11,
        boot_reps=1000,
        memory_side=None,
        cases_sha256=F.cases_sha256(cases),
        is_product=lambda a: False,
    )
    assert res["grade"] == "table"
    assert res["protocol"]["budgets_registered_not_run"] == [4000]

    with pytest.raises(ProtocolError, match="subset of frozen"):
        check_run(
            v2_path,
            decider_id="opencode-deepseek-v4.1-flash",
            adapters=["bm25"],
            adapter_files={},
            budgets=[2000],
            repeats=3,
            boot_seed=11,
            boot_reps=1000,
            memory_side=None,
            cases_sha256=F.cases_sha256(cases),
            is_product=lambda a: False,
        )


def test_r4_b1_beam_exploratory_and_counted_scripts_never_use_opencode_runner():
    gates_src = (REPO_ROOT / "scripts" / "run_phase1_gates.py").read_text(encoding="utf-8")
    assert "opencode_zen_stdin_runner" not in gates_src
    beam_path = REPO_ROOT / "results" / "v2" / "validity" / "chat_agent" / "opencode-beam-free" / "validity.json"
    if not beam_path.is_file():
        pytest.skip("private results/v2/validity omitted from public export")
    beam = json.loads(beam_path.read_text())
    assert beam.get("exploratory") is True
    ext_src = (REPO_ROOT / "scripts" / "run_a4_extractor_probe.py").read_text(encoding="utf-8")
    assert "opencode_zen_stdin_runner" not in ext_src
