"""v2 tests: Step 8 deduplicated ledger, isolated environments and runner metadata."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from memory_bench.engine import ledger_v2
from memory_bench.engine.freeze import check_protocol, load_protocol
from memory_bench.submit.textfree import scan_file

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_r6_a1_ledger_counts_run_and_pack_copy_once(tmp_path):
    """A1: one run plus its _packs/ copy (and any submission.json or same-run_id copy) sums once."""
    run_dir = tmp_path / "table" / "chat_agent" / "opencode-deepseek-v4.1-flash" / "bm25"
    pack_dir = tmp_path / "table" / "chat_agent" / "_packs" / "opencode-deepseek-v4.1-flash" / "bm25" / "2026-11-24-default"
    extra_sub_dir = tmp_path / "custom_export" / "bm25"
    dup_run_dir = tmp_path / "backup_copy" / "bm25"

    for d in (run_dir, pack_dir, extra_sub_dir, dup_run_dir):
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"run_id": "v2-step8-table-opencode-deepseek-v4.1-flash-bm25"}),
            encoding="utf-8",
        )
        (d / "spend.json").write_text(
            json.dumps({"ledger": "main", "total_usd": 1.170164}),
            encoding="utf-8",
        )
    (pack_dir / "submission.json").write_text(json.dumps({"schema": "memory_bench.submission/1"}), encoding="utf-8")
    (extra_sub_dir / "submission.json").write_text(json.dumps({"schema": "memory_bench.submission/1"}), encoding="utf-8")

    # R1-R4 file with NO ledger key counts on main
    r4_probe = tmp_path / "extractor_probe" / "chat_agent" / "opencode-deepseek-v4.1-flash"
    r4_probe.mkdir(parents=True)
    (r4_probe / "spend.json").write_text(json.dumps({"total_usd": 0.049213}), encoding="utf-8")

    # R4 Qwen file with metering_note applies effective_usd
    r4_qwen = tmp_path / "validity" / "chat_agent" / "opencode-qwen3.8-flash"
    r4_qwen.mkdir(parents=True)
    (r4_qwen / "spend.json").write_text(
        json.dumps({
            "ledger": "main",
            "total_usd": 0.023658,
            "metering_note": {"corrected_total_usd": 0.062155},
        }),
        encoding="utf-8",
    )

    # Sonnet ledger stays strictly separate
    sonnet_gate = tmp_path / "validity" / "chat_agent" / "sonnet-5-5"
    sonnet_gate.mkdir(parents=True)
    (sonnet_gate / "spend.json").write_text(
        json.dumps({"ledger": "sonnet", "total_usd": 0.809292}),
        encoding="utf-8",
    )

    main_sum = ledger_v2.sum_ledger_spend(tmp_path, "main")
    assert main_sum["recorded_usd"] == pytest.approx(1.170164 + 0.049213 + 0.023658, abs=1e-6)
    assert main_sum["effective_usd"] == pytest.approx(1.170164 + 0.049213 + 0.062155, abs=1e-6)
    assert main_sum["n_files"] == 3
    assert main_sum["pack_copies_skipped"] == 2
    assert main_sum["deduped_skipped"] == 1
    assert main_sum["remaining_to_stop_usd"] == pytest.approx(55.0 - main_sum["effective_usd"], abs=1e-6)

    sonnet_sum = ledger_v2.sum_ledger_spend(tmp_path, "sonnet")
    assert sonnet_sum["recorded_usd"] == pytest.approx(0.809292, abs=1e-6)
    assert sonnet_sum["effective_usd"] == pytest.approx(0.809292, abs=1e-6)
    assert sonnet_sum["n_files"] == 1

    # Headroom excludes run_dir's own spend.json
    hr = ledger_v2.run_headroom_usd(tmp_path, "main", run_dir=run_dir)
    assert hr == pytest.approx(55.0 - (0.049213 + 0.062155), abs=1e-6)


def test_r6_a2_a3_table_runner_isolated_venvs_and_textfree_environment(tmp_path):
    """A2/A3: table runner uses only v2 worktree venvs, uses ledger_v2, and emits textfree env metadata."""
    runner_path = REPO_ROOT / "scripts" / "run_phase1_table.py"
    if not runner_path.is_file():
        pytest.skip("scripts/run_phase1_table.py omitted from public export (docs/freeze.md)")
    src_text = runner_path.read_text(encoding="utf-8")
    assert "MAIN_REPO_ROOT" not in src_text
    assert "/06_Products/memory-bench/" not in src_text
    assert "ledger_v2" in src_text

    spec = importlib.util.spec_from_file_location("run_phase1_table_r6", runner_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for slug, info in mod.SYSTEM_SPECS.items():
        py_path = Path(info["python"])
        assert REPO_ROOT in py_path.parents, f"{slug} python {py_path} escapes v2 worktree {REPO_ROOT}"

    env_block = mod._environment_block("mem0")
    assert env_block["python_version"]
    assert env_block["venv_name"] == "venv-mem0"
    assert set(env_block["packages"]) == {"mem0ai", "langmem", "graphiti-core"}
    assert len(env_block["table_runner_sha256"]) == 64
    assert env_block["protocol_step"] == 8

    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps({"run_id": "test-run", "environment": env_block}), encoding="utf-8")
    assert scan_file(mf) == []


def test_r6_step8_protocol_freeze_matches():
    """Step 8 freezes ledger_v2.py and scripts/run_phase1_table.py; all Steps 1-8 match."""
    proto = load_protocol(REPO_ROOT / "protocol" / "v2.json")
    assert "8" in proto["steps"]
    step8_files = proto["steps"]["8"]["files"]
    assert "src/memory_bench/engine/ledger_v2.py" in step8_files
    assert "scripts/run_phase1_table.py" in step8_files
    assert check_protocol(REPO_ROOT / "protocol" / "v2.json") == []
