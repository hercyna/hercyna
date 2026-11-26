"""v2 regression and red-team tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from memory_bench.engine.freeze import protocol_sha256
from memory_bench.engine.health import sanitize_health_errors
from memory_bench.engine.run import rebuild_summary
from memory_bench.site.build import build as build_site
from memory_bench.submit.pack import pack
from memory_bench.submit.textfree import is_free_text
from memory_bench.submit.verify import EXCUSED_CONFIG, MAX_EXCUSED_SHARE, verify

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl"
PROTOCOL = ROOT / "protocol" / "v2.json"
REF_PACK = ROOT / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5" / "graphiti"
CLEAN_PACK = ROOT / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5" / "bm25"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _rehash_manifest(pack_dir: Path) -> None:
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in list(manifest["files"]):
        fp = pack_dir / name
        if fp.exists():
            manifest["files"][name] = _sha256(fp)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _stage_copy(tmp_path: Path, src: Path, name: str = "pack") -> Path:
    dst = tmp_path / name
    shutil.copytree(src, dst)
    return dst


def test_a1_reference_cell_r10_drop_attack_fails(tmp_path: Path) -> None:
    """Dropping an extra scored case and listing it in health/incomplete_cells must fail check 3."""
    pack_dir = _stage_copy(tmp_path, REF_PACK, "haiku-5-5/graphiti")
    victim_cid = "ctab2026112302-0001"
    rows = [json.loads(ln) for ln in (pack_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    kept = [r for r in rows if not (r["system"] == "graphiti" and r["case_id"] == victim_cid)]
    assert len(kept) == len(rows) - 3
    (pack_dir / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in kept) + "\n", encoding="utf-8")

    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["incomplete_cells"][0]["missing_cases"] = sorted(
        [*manifest["incomplete_cells"][0]["missing_cases"], victim_cid]
    )
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    health = json.loads((pack_dir / "health.json").read_text(encoding="utf-8"))
    health["adapters"][1]["errors"] += 1
    health["adapters"][1]["cases_done"] -= 1
    health["adapters"][1]["last_error"]["case_id"] = victim_cid
    (pack_dir / "health.json").write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
    rebuild_summary(pack_dir)
    _rehash_manifest(pack_dir)

    res = verify(pack_dir, protocol=PROTOCOL, cases=CASES)
    assert not res.ok
    assert any("frozen excused list" in str(f) for f in res.failures), res.failures


def test_a1_reference_cell_missing_frozen_excuse_fails(tmp_path: Path) -> None:
    """Adding back a frozen excused case (changing set from frozen config) must fail check 3."""
    pack_dir = _stage_copy(tmp_path, REF_PACK, "haiku-5-5/graphiti")
    excused_cfg = json.loads((ROOT / EXCUSED_CONFIG).read_text(encoding="utf-8"))
    restored_cid = excused_cfg["systems"]["graphiti/e25"][0]

    rows = [json.loads(ln) for ln in (pack_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    template = next(r for r in rows if r["system"] == "graphiti" and r["case_id"] == "ctab2026112302-0001")
    for rep in range(3):
        sample = dict(template)
        sample["case_id"] = restored_cid
        sample["repeat"] = rep
        rows.append(sample)
    (pack_dir / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["incomplete_cells"][0]["missing_cases"] = [
        cid for cid in manifest["incomplete_cells"][0]["missing_cases"] if cid != restored_cid
    ]
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rebuild_summary(pack_dir)
    _rehash_manifest(pack_dir)

    res = verify(pack_dir, protocol=PROTOCOL, cases=CASES)
    assert not res.ok
    assert any("frozen excused list" in str(f) for f in res.failures), res.failures


def test_a1_outside_pack_r10_drop_without_failed_ingest_fails(tmp_path: Path) -> None:
    """Outside pack cannot excuse a missing row unless memory_calls.jsonl shows a failed ingest call."""
    pack_dir = _stage_copy(tmp_path, REF_PACK, "outside-customgraph")
    victim_cid = "ctab2026112302-0001"

    rows = [json.loads(ln) for ln in (pack_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    kept = []
    for r in rows:
        if r["system"] == "graphiti":
            if r["case_id"] == victim_cid:
                continue
            r["system"] = "customgraph"
        kept.append(json.dumps(r))
    (pack_dir / "metrics.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")

    call_lines = []
    for ln in (pack_dir / "memory_calls.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(ln)
        c["adapter"] = "customgraph"
        c["status"] = 200
        call_lines.append(json.dumps(c))
    (pack_dir / "memory_calls.jsonl").write_text("\n".join(call_lines) + "\n", encoding="utf-8")

    health = json.loads((pack_dir / "health.json").read_text(encoding="utf-8"))
    health["adapters"][1]["name"] = "customgraph"
    health["adapters"][1]["errors"] = 1
    health["adapters"][1]["cases_done"] = 299
    health["adapters"][1]["last_error"] = {"case_id": victim_cid, "error": "RuntimeError"}
    (pack_dir / "health.json").write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    sub = json.loads((pack_dir / "submission.json").read_text(encoding="utf-8"))
    sub["system"] = "customgraph"
    sub["protocol_sha256"] = None
    (pack_dir / "submission.json").write_text(json.dumps(sub, indent=2) + "\n", encoding="utf-8")

    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["grade"] = "exploratory"
    manifest.pop("protocol", None)
    manifest["adapters"][1]["name"] = "customgraph"
    manifest["incomplete_cells"] = [{"cell": "customgraph/e25@1000", "missing_cases": [victim_cid]}]
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rebuild_summary(pack_dir)
    _rehash_manifest(pack_dir)

    res = verify(pack_dir, protocol=PROTOCOL, cases=CASES)
    assert not res.ok
    assert any("no failed ingest call in memory_calls.jsonl" in str(f) for f in res.failures), res.failures


def _build_synthetic_outside_pack(tmp_path: Path, n_cases: int, n_excused: int) -> tuple[Path, Path]:
    pack_dir = _stage_copy(tmp_path, REF_PACK, f"customgraph-{n_excused}-of-{n_cases}")
    # Select n_cases real clean case rows (non-excused in REF_PACK) from table_cases.jsonl.
    ref_manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    ref_missing = set(ref_manifest["incomplete_cells"][0]["missing_cases"])
    all_case_lines = [ln for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    picked_lines = [ln for ln in all_case_lines if json.loads(ln)["case_id"] not in ref_missing][:n_cases]
    assert len(picked_lines) == n_cases
    case_ids = [json.loads(ln)["case_id"] for ln in picked_lines]
    excused_ids = case_ids[:n_excused]
    kept_id_set = set(case_ids) - set(excused_ids)
    all_id_set = set(case_ids)

    cases_file = tmp_path / f"cases-{n_cases}-{n_excused}.jsonl"
    cases_file.write_text("\n".join(picked_lines) + "\n", encoding="utf-8")

    def _keep_row(row: dict) -> dict | None:
        if row["case_id"] not in all_id_set:
            return None
        if row["system"] != "full":
            if row["case_id"] in excused_ids:
                return None
            row = {**row, "system": "customgraph"}
        return row

    new_metrics = []
    for ln in (pack_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines():
        kept = _keep_row(json.loads(ln))
        if kept is not None:
            new_metrics.append(json.dumps(kept))
    (pack_dir / "metrics.jsonl").write_text("\n".join(new_metrics) + "\n", encoding="utf-8")

    new_calls = []
    for ln in (pack_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        kept = _keep_row(json.loads(ln))
        if kept is not None:
            new_calls.append(json.dumps(kept))
    (pack_dir / "calls.jsonl").write_text("\n".join(new_calls) + "\n", encoding="utf-8")

    new_timings = []
    for ln in (pack_dir / "timings.jsonl").read_text(encoding="utf-8").splitlines():
        kept = _keep_row(json.loads(ln))
        if kept is not None:
            new_timings.append(json.dumps(kept))
    (pack_dir / "timings.jsonl").write_text("\n".join(new_timings) + "\n", encoding="utf-8")

    base_mem_calls = [json.loads(ln) for ln in (pack_dir / "memory_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    sample_ingest = dict(next(c for c in base_mem_calls if c.get("phase") == "ingest"))
    new_mem_calls = []
    for cid in case_ids:
        status = 500 if cid in excused_ids else 200
        new_mem_calls.append(json.dumps({**sample_ingest, "case_id": cid, "adapter": "customgraph", "status": status}))
    (pack_dir / "memory_calls.jsonl").write_text("\n".join(new_mem_calls) + "\n", encoding="utf-8")

    health = json.loads((pack_dir / "health.json").read_text(encoding="utf-8"))
    health["adapters"][0]["cases_done"] = len(all_id_set)
    health["adapters"][1]["name"] = "customgraph"
    health["adapters"][1]["errors"] = n_excused
    health["adapters"][1]["cases_done"] = len(kept_id_set)
    health["adapters"][1]["last_error"] = {"case_id": excused_ids[-1], "error": "RuntimeError"}
    (pack_dir / "health.json").write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")

    sub = json.loads((pack_dir / "submission.json").read_text(encoding="utf-8"))
    sub["system"] = "customgraph"
    sub["protocol_sha256"] = None
    (pack_dir / "submission.json").write_text(json.dumps(sub, indent=2) + "\n", encoding="utf-8")

    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["grade"] = "exploratory"
    manifest.pop("protocol", None)
    manifest["cases"]["n"] = n_cases
    manifest["cases"]["sha256"] = _sha256(cases_file)
    manifest["adapters"][1]["name"] = "customgraph"
    manifest["incomplete_cells"] = [{"cell": "customgraph/e25@1000", "missing_cases": excused_ids}]
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    rebuild_summary(pack_dir)
    _rehash_manifest(pack_dir)
    return pack_dir, cases_file


def test_a1_outside_pack_genuine_ingest_error_under_and_over_5pct(tmp_path: Path) -> None:
    """Genuine failed ingest call passes <= 5% and exits 5 (excused_over_limit) when > 5%."""
    ok_pack, ok_cases = _build_synthetic_outside_pack(tmp_path, n_cases=20, n_excused=1)
    res_ok = verify(ok_pack, protocol=PROTOCOL, cases=ok_cases)
    assert res_ok.ok, res_ok.failures
    assert res_ok.verdict() == "verify: pass, exploratory (check 6 skipped); ingest error, documented (1 case)"

    over_pack, over_cases = _build_synthetic_outside_pack(tmp_path, n_cases=20, n_excused=2)
    assert MAX_EXCUSED_SHARE == 0.05
    res_over = verify(over_pack, protocol=PROTOCOL, cases=over_cases)
    assert not res_over.ok
    assert res_over.excused_over_limit
    verdict = res_over.verdict()
    assert verdict.startswith("verify: excused_over_limit ")
    assert "pass" not in verdict


def test_a1_site_build_unranks_over_5pct_excused_cell(tmp_path: Path) -> None:
    """site/build.py must mark cells with > 5% excused cases as unranked and exclude from pairs."""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    for link_name in ("configs", "protocol", "site"):
        (fake_root / link_name).symlink_to(ROOT / link_name)
    v2_root = fake_root / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5"
    v2_root.mkdir(parents=True)
    shutil.copytree(CLEAN_PACK, v2_root / "bm25")
    shutil.copytree(ROOT / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5" / "mem0", v2_root / "mem0")
    shutil.copytree(REF_PACK, v2_root / "graphiti")
    # Remove 20 scored cases for graphiti (> 5% of 300) in metrics.jsonl.
    g_rows = [json.loads(ln) for ln in (v2_root / "graphiti" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    extra_drop = {r["case_id"] for r in g_rows if r["system"] == "graphiti"}
    drop_20 = sorted(extra_drop)[:20]
    drop_set = set(drop_20)
    kept_rows = [r for r in g_rows if not (r["system"] == "graphiti" and r["case_id"] in drop_set)]
    (v2_root / "graphiti" / "metrics.jsonl").write_text(
        "\n".join(json.dumps(r) for r in kept_rows) + "\n", encoding="utf-8"
    )
    g_manifest = json.loads((v2_root / "graphiti" / "manifest.json").read_text(encoding="utf-8"))
    g_manifest["incomplete_cells"][0]["missing_cases"] = sorted(
        set(g_manifest["incomplete_cells"][0]["missing_cases"]) | drop_set
    )
    (v2_root / "graphiti" / "manifest.json").write_text(json.dumps(g_manifest, indent=2) + "\n", encoding="utf-8")

    out_dir = tmp_path / "site_out"
    manifest = build_site(
        fake_root,
        out_dir,
        built_at="2026-11-25T12:00:00+00:00",
    )
    assert "chat.html" in manifest["bytes"]
    chat_html = (out_dir / "chat.html").read_text(encoding="utf-8")
    assert "unranked (&gt;5% excused)" in chat_html
    row_match = re.search(r"<tr\b[^>]*>(?:(?!</tr>).)*?Graphiti(?:(?!</tr>).)*?</tr>", chat_html, re.S)
    assert row_match is not None
    assert "unranked (&gt;5% excused)" in row_match.group(0)
    assert "Lower wrong action" not in row_match.group(0)
    assert "Higher wrong action" not in row_match.group(0)
    assert "graphiti/e25@1000" not in (chat_html.split('<div class="v2-dpanel"')[0])


def test_a2_positioning_citations_verified() -> None:
    """Verify 2410.11029 is gone and live-fetched URLs are present in docs/positioning.md."""
    pos = (ROOT / "docs" / "positioning.md").read_text(encoding="utf-8")
    assert "2410.11029" not in pos
    assert "https://www.goodai.com/introducing-goodai-ltm-benchmark/" in pos
    assert "https://github.com/GoodAI/goodai-ltm-benchmark" in pos
    assert "https://arxiv.org/abs/2506.21605" in pos


def test_a3_health_error_sanitization_and_strict_textfree(tmp_path: Path) -> None:
    """health.json allows class-name error only, rejects messages, and all 40 shipped packs pass."""
    assert is_free_text(".adapters[].last_error.error", "JSONDecodeError") is False
    assert is_free_text(".adapters[].last_error.error", "AttributeError") is False
    assert is_free_text(".adapters[].last_error.error", "KeyError: 'User prefers PDF only'") is True
    assert is_free_text(".adapters[].last_error.error", "bad failure") is True

    raw = {
        "adapters": [
            {"system": "mem0", "last_error": {"case_id": "c1", "error": "ValueError: secret user text"}},
        ]
    }
    clean, changed = sanitize_health_errors(raw)
    assert changed is True
    err_obj = clean["adapters"][0]["last_error"]
    assert err_obj["error"] == "ValueError"
    assert err_obj["error_sha256"] == hashlib.sha256(b"ValueError: secret user text").hexdigest()

    table_root = ROOT / "results" / "table" / "v2" / "chat_agent"
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    for decider_dir in sorted(table_root.iterdir()):
        for prod, expected_cls in (("graphiti", "JSONDecodeError"), ("mem0", "AttributeError")):
            h = json.loads((decider_dir / prod / "health.json").read_text(encoding="utf-8"))
            err_adapters = [a for a in h["adapters"] if a.get("last_error") is not None]
            assert len(err_adapters) == 1
            le = err_adapters[0]["last_error"]
            assert le["error"] == expected_cls
            assert hex64.match(le["error_sha256"])

    run_copy = _stage_copy(tmp_path, REF_PACK, "raw_run")
    m = json.loads((run_copy / "manifest.json").read_text(encoding="utf-8"))
    m["grade"] = "exploratory"
    (run_copy / "manifest.json").write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    h = json.loads((run_copy / "health.json").read_text(encoding="utf-8"))
    h["adapters"][1]["last_error"]["error"] = "ValueError: leaky note"
    (run_copy / "health.json").write_text(json.dumps(h, indent=2) + "\n", encoding="utf-8")
    packed_dir = pack(run_copy, tmp_path / "repacked", exploratory=True)
    packed_health = json.loads((packed_dir / "health.json").read_text(encoding="utf-8"))
    assert packed_health["adapters"][1]["last_error"]["error"] == "ValueError"
    assert "error_sha256" in packed_health["adapters"][1]["last_error"]


def test_a4_red_team_protocol_prefix_and_submission_multiline(tmp_path: Path) -> None:
    """Protocol prefix cannot authorize later deciders; multiline submitter is rejected."""
    step3_src = ROOT / "results" / "table" / "v2" / "chat_agent" / "opencode-deepseek-v4.1-flash" / "bm25"
    p_proto = _stage_copy(tmp_path, step3_src, "proto-spoof")
    proto = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    step1_hash = protocol_sha256({"1": proto["steps"]["1"]})
    manifest = json.loads((p_proto / "manifest.json").read_text(encoding="utf-8"))
    manifest["protocol"]["protocol_sha256"] = step1_hash
    (p_proto / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    res_proto = verify(p_proto, protocol=PROTOCOL, cases=CASES)
    assert not res_proto.ok
    assert any("is not a frozen decider" in str(f) for f in res_proto.failures), res_proto.failures

    p_sub = _stage_copy(tmp_path, CLEAN_PACK, "sub-multiline")
    sub = json.loads((p_sub / "submission.json").read_text(encoding="utf-8"))
    sub["submitter"] = "line1\nCASE TEXT LEAK HERE"
    (p_sub / "submission.json").write_text(json.dumps(sub, indent=2) + "\n", encoding="utf-8")
    m2 = json.loads((p_sub / "manifest.json").read_text(encoding="utf-8"))
    m2["files"]["submission.json"] = _sha256(p_sub / "submission.json")
    (p_sub / "manifest.json").write_text(json.dumps(m2, indent=2) + "\n", encoding="utf-8")
    res_sub = verify(p_sub, protocol=PROTOCOL, cases=CASES)
    assert not res_sub.ok
    assert any("single-line string" in str(f) for f in res_sub.failures), res_sub.failures


def test_a1_verify_cli_exit_code_5_on_over_limit(tmp_path: Path) -> None:
    """CLI exits 5 and omits 'pass' when > 5% of cases are excused."""
    over_pack, over_cases = _build_synthetic_outside_pack(tmp_path, n_cases=20, n_excused=2)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "memory_bench.cli",
            "verify",
            str(over_pack),
            "--cases",
            str(over_cases),
            "--protocol",
            str(PROTOCOL),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 5
    assert "verify: excused_over_limit" in proc.stdout
    assert "pass" not in proc.stdout
