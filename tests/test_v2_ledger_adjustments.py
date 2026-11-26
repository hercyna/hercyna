"""v2 tests: Step 9 ledger adjustments, call-log reconciliation and the v2 site build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_bench.engine import ledger_v2, ledger_v3
from memory_bench.engine.freeze import check_protocol, load_protocol
from memory_bench.submit.textfree import scan_file

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_r7_a1_adjustments_file_schema_and_values():
    """A1: configs/v2/ledger_adjustments.jsonl holds one append-only row per uncounted R5 run."""
    adj_path = REPO_ROOT / "configs" / "v2" / "ledger_adjustments.jsonl"
    assert adj_path.is_file()
    assert scan_file(adj_path) == []

    rows = ledger_v3.load_adjustments(adj_path)
    assert len(rows) == 2
    by_path = {r["run_path"]: r for r in rows}
    qwen = by_path["results/v2/table/chat_agent_r5_step7/opencode-qwen3.8-flash/mem0"]
    haiku = by_path["results/v2/table/chat_agent_r5_step7/haiku-5-5/langmem"]

    assert qwen == {
        "run_path": "results/v2/table/chat_agent_r5_step7/opencode-qwen3.8-flash/mem0",
        "ledger": "main",
        "usd": 0.352621,
        "call_log": "calls.jsonl",
        "paid_rows": 378,
    }
    assert haiku == {
        "run_path": "results/v2/table/chat_agent_r5_step7/haiku-5-5/langmem",
        "ledger": "main",
        "usd": 0.06353,
        "call_log": "calls.jsonl",
        "paid_rows": 104,
    }
    assert sum(r["usd"] for r in rows) == pytest.approx(0.416151, abs=1e-6)


def test_r7_a1_ledger_v3_adds_adjustments_without_touching_ledger_v2(tmp_path):
    """A1: ledger_v3 wraps frozen ledger_v2, adds adjustments once per ledger, and enforces headroom."""
    r5_qwen = tmp_path / "table" / "chat_agent_r5_step7" / "opencode-qwen3.8-flash" / "mem0"
    r5_haiku = tmp_path / "table" / "chat_agent_r5_step7" / "haiku-5-5" / "langmem"
    sonnet_gate = tmp_path / "validity" / "chat_agent" / "sonnet-5-5"
    for d in (r5_qwen, r5_haiku, sonnet_gate):
        d.mkdir(parents=True)

    (r5_qwen / "spend.json").write_text(json.dumps({"ledger": "main", "total_usd": 3.746676}), encoding="utf-8")
    (r5_qwen / "memory_calls.jsonl").write_text(
        json.dumps({"cached": False, "usd": 3.746676}) + "\n",
        encoding="utf-8",
    )
    (r5_qwen / "calls.jsonl").write_text(
        "".join(json.dumps({"cached": False, "usd": 0.352621 / 378}) + "\n" for _ in range(378)),
        encoding="utf-8",
    )

    (r5_haiku / "spend.json").write_text(json.dumps({"ledger": "main", "total_usd": 3.155302}), encoding="utf-8")
    (r5_haiku / "memory_calls.jsonl").write_text(
        json.dumps({"cached": False, "usd": 3.155302}) + "\n",
        encoding="utf-8",
    )
    (r5_haiku / "calls.jsonl").write_text(
        "".join(json.dumps({"cached": False, "usd": 0.063530 / 104}) + "\n" for _ in range(104)),
        encoding="utf-8",
    )

    (sonnet_gate / "spend.json").write_text(json.dumps({"ledger": "sonnet", "total_usd": 0.809292}), encoding="utf-8")
    (sonnet_gate / "calls.jsonl").write_text(
        json.dumps({"cached": False, "usd": 0.809292}) + "\n",
        encoding="utf-8",
    )

    # Without adjustments, audit_call_logs_vs_spend catches both uncounted calls.jsonl discrepancies (> $0.005)
    unadjusted_audit = ledger_v3.audit_call_logs_vs_spend(tmp_path, adjustments_path=False, tolerance_usd=0.005)
    assert not unadjusted_audit["ok"]
    assert len(unadjusted_audit["violations"]) == 2

    # With canonical adjustments file, audit passes with zero violations > $0.005
    adj_file = REPO_ROOT / "configs" / "v2" / "ledger_adjustments.jsonl"
    adjusted_audit = ledger_v3.audit_call_logs_vs_spend(tmp_path, adjustments_path=adj_file, tolerance_usd=0.005)
    assert adjusted_audit["ok"], adjusted_audit

    before_main = ledger_v2.sum_ledger_spend(tmp_path, "main")
    after_main = ledger_v3.sum_ledger_spend(tmp_path, "main", adjustments_path=adj_file)
    assert after_main["ledger_step"] == 9
    assert after_main["adjustments_usd"] == pytest.approx(0.416151, abs=1e-6)
    assert after_main["recorded_usd"] == pytest.approx(before_main["recorded_usd"] + 0.416151, abs=1e-6)
    assert after_main["effective_usd"] == pytest.approx(before_main["effective_usd"] + 0.416151, abs=1e-6)
    assert after_main["remaining_to_stop_usd"] == pytest.approx(55.0 - after_main["effective_usd"], abs=1e-6)

    after_sonnet = ledger_v3.sum_ledger_spend(tmp_path, "sonnet", adjustments_path=adj_file)
    assert after_sonnet["adjustments_usd"] == 0.0
    assert after_sonnet["recorded_usd"] == pytest.approx(0.809292, abs=1e-6)


def test_r7_a1_every_v2_call_log_reconciles_within_half_cent():
    """A1: sum paid, uncached rows in every call log under results/v2 and fail if any folder
    exceeds its spend file (plus recorded Step 9 adjustments) by more than $0.005."""
    results_root = REPO_ROOT / "results" / "v2"
    if not (results_root / "validity").is_dir():
        pytest.skip("private results/v2 validity runs omitted from public export")
    audit = ledger_v3.audit_call_logs_vs_spend(results_root, tolerance_usd=0.005)
    assert audit["dirs_checked"] >= 18
    assert audit["adjustment_mismatches"] == [], f"Adjustment log mismatch: {audit['adjustment_mismatches']}"
    assert audit["violations"] == [], (
        "Paid uncached call-log rows exceed spend.json + adjustments by > $0.005:\n"
        + "\n".join(json.dumps(v, sort_keys=True) for v in audit["violations"])
    )

    # When the full untracked table tree is present (live workspace), verify exact R7 before/after figures
    if (results_root / "table" / "chat_agent_r5_step7").is_dir():
        before = ledger_v2.sum_ledger_spend(results_root, "main")
        after = ledger_v3.sum_ledger_spend(results_root, "main")
        assert before["recorded_usd"] == pytest.approx(44.150407, abs=1e-5)
        assert after["recorded_usd"] == pytest.approx(44.566558, abs=1e-5)
        assert after["effective_usd"] == pytest.approx(44.605055, abs=1e-5)


def test_r7_step9_protocol_freeze_matches():
    """Step 9 freezes ledger_v3.py and configs/v2/ledger_adjustments.jsonl; Steps 1-9 match."""
    proto = load_protocol(REPO_ROOT / "protocol" / "v2.json")
    assert "9" in proto["steps"]
    step9_files = proto["steps"]["9"]["files"]
    assert "src/memory_bench/engine/ledger_v3.py" in step9_files
    assert "configs/v2/ledger_adjustments.jsonl" in step9_files
    assert check_protocol(REPO_ROOT / "protocol" / "v2.json") == []


def _srgb_lin(c: int) -> float:
    x = c / 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _contrast(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    lf = 0.2126 * _srgb_lin(fg[0]) + 0.7152 * _srgb_lin(fg[1]) + 0.0722 * _srgb_lin(fg[2])
    lb = 0.2126 * _srgb_lin(bg[0]) + 0.7152 * _srgb_lin(bg[1]) + 0.0722 * _srgb_lin(bg[2])
    return (max(lf, lb) + 0.05) / (min(lf, lb) + 0.05)


def test_r7_a4_palette_contrast_at_least_4_5_in_light_and_dark():
    """A4: every text/badge/link colour token meets >= 4.5:1 contrast in both light and dark themes."""
    from memory_bench.site.style import DARK, LIGHT

    light_checks = [
        ("ink", "bg"), ("ink-2", "bg"), ("ink-3", "bg"), ("ink-3", "paper"), ("ink-3", "shape"),
        ("teal", "bg"), ("teal", "paper"), ("teal", "shape"), ("teal", "teal-soft"),
        ("on-teal", "teal"), ("red", "bg"), ("verdict", "verdict-soft"),
        ("rust", "bg"), ("rust", "hover"), ("rust", "rust-soft"),
        ("amber", "bg"), ("amber", "amber-soft"),
    ]
    for fg_k, bg_k in light_checks:
        ratio = _contrast(_hex_rgb(LIGHT[fg_k]), _hex_rgb(LIGHT[bg_k]))
        assert ratio >= 4.5, f"LIGHT {fg_k} on {bg_k} contrast {ratio:.2f} < 4.5"

    dark_hex_checks = [
        ("ink", "bg"), ("ink-2", "bg"), ("ink-3", "bg"), ("ink-3", "paper"), ("ink-3", "surface"), ("ink-3", "shape"),
        ("teal", "bg"), ("teal", "paper"), ("teal", "shape"),
        ("on-teal", "teal"), ("red", "bg"), ("rust", "bg"), ("rust", "surface"), ("amber", "bg"),
    ]
    for fg_k, bg_k in dark_hex_checks:
        ratio = _contrast(_hex_rgb(DARK[fg_k]), _hex_rgb(DARK[bg_k]))
        assert ratio >= 4.5, f"DARK {fg_k} on {bg_k} contrast {ratio:.2f} < 4.5"


def test_r7_a2_a4_rank_with_full_row_and_exploratory_headers(tmp_path):
    """A2 & A4: any ranked pair shows wrong action, withheld action, right action, and ingest-error
    cases left out on the same row; every table shows decider floor in thead, exploratory badge,
    uniform XX.X% [XX.X, XX.X] intervals, th[scope], and chart text alternatives."""
    import re
    import shutil
    from memory_bench.scoring.metrics import summarise
    from memory_bench.site.build import build as build_site

    root = tmp_path / "repo"
    (root / "protocol").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "protocol" / "v1.json", root / "protocol" / "v1.json")
    shutil.copy(REPO_ROOT / "protocol" / "v2.json", root / "protocol" / "v2.json")

    # Build a deterministic 2-decider x 2-product synthetic v2 chat_agent table where graphiti < mem0 on wrong action
    # Keep 13 / 260 = 5.0% <= MAX_EXCUSED_SHARE so graphiti remains eligible for separation display
    n_cases = 260
    proto_sha = "d43cb44fd25628424d2d836bc8a7d6939d991737f83b73368d5a90a359fb1552"
    cases_sha = "48ab661a44a8edeb9b93f38b73d429d97a68433133af99f8c0cff7ea844cd1c0"

    for did in ("haiku-5-5", "opencode-deepseek-v4.1-flash"):
        base_rows = []
        for idx in range(n_cases):
            cid = f"csyn-{idx:04d}"
            gate_act = "EXECUTE_OR_ANSWER" if idx in (0, 259) else "USE_UPDATED_PREF"  # 2 validity fails
            base_rows.append({
                "case_id": cid, "track": "chat_agent", "mode": "blind_adapter", "eval_mode": "mode_a",
                "decider": did, "kind": "gate", "system": "full", "config_label": "default",
                "budget": None, "ingest_pass": 1, "repeat": 0, "a_star": "USE_UPDATED_PREF",
                "mechanism": "H1", "action": gate_act, "view_tokens": 5000, "view_units": 80,
            })
            for rep in (1, 2, 3):
                base_rows.append({
                    "case_id": cid, "track": "chat_agent", "mode": "blind_adapter", "eval_mode": "mode_a",
                    "decider": did, "kind": "floor", "system": "full", "config_label": "default",
                    "budget": None, "ingest_pass": 1, "repeat": rep, "a_star": "USE_UPDATED_PREF",
                    "mechanism": "H1", "action": "USE_UPDATED_PREF", "view_tokens": 5000, "view_units": 80,
                })

        for sys_name, cfg, missing_idxs, acts in (
            ("graphiti", "e25", set(range(13)), lambda i: "REQUEST_INFO" if i % 2 == 0 else "USE_UPDATED_PREF"),
            ("mem0", "k25", {13, 14}, lambda i: "EXECUTE_OR_ANSWER" if i < 180 else "USE_UPDATED_PREF"),
        ):
            rdir = root / "results" / "v2" / "table" / "chat_agent" / did / sys_name
            rdir.mkdir(parents=True)
            rows = list(base_rows)
            for idx in range(n_cases):
                if idx in missing_idxs:
                    continue
                cid = f"csyn-{idx:04d}"
                act = acts(idx)
                for rep in (1, 2, 3):
                    rows.append({
                        "case_id": cid, "track": "chat_agent", "mode": "blind_adapter", "eval_mode": "mode_a",
                        "decider": did, "kind": "view", "system": sys_name, "config_label": cfg,
                        "budget": 1000, "ingest_pass": 1, "repeat": rep,
                        "a_star": "USE_UPDATED_PREF", "mechanism": "H1", "action": act,
                        "view_tokens": 420, "view_units": 10,
                        "view_sha256": f"sha-{sys_name}-{idx}",
                    })
            (rdir / "metrics.jsonl").write_text(
                "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                encoding="utf-8",
            )
            summ = {**summarise(rows, seed=11, reps=80), "grade": "table"}
            (rdir / "summary.json").write_text(json.dumps(summ), encoding="utf-8")
            man = {
                "run_id": f"syn-{did}-{sys_name}",
                "state": "complete",
                "grade": "table",
                "repeats": 3,
                "bootstrap": {"seed": 11, "reps": 80},
                "cases": {"n": n_cases, "sha256": cases_sha},
                "protocol": {"protocol_sha256": proto_sha},
                "deciders": [{"id": did, "model": did}],
                "adapters": [{"name": "full", "config_label": "default"}, {"name": sys_name, "config_label": cfg}],
                "incomplete_cells": [
                    {
                        "cell": f"{sys_name}/{cfg}@1000",
                        "missing_cases": [f"csyn-{k:04d}" for k in sorted(missing_idxs)],
                    }
                ],
            }
            (rdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")

    out = tmp_path / "site-out"
    build_site(root, out, built_at="2026-11-24T12:00:00+00:00")
    chat_html = (out / "chat.html").read_text(encoding="utf-8")

    # Ranked pairs section shows Lower/Higher wrong action on the SAME <tr> with all 4 required fields
    assert 'id="chat-ranked-pairs"' in chat_html
    pair_trs = re.findall(r'<tr[^>]*>[\s\S]*?</tr>', chat_html.split('id="chat-ranked-pairs"')[1].split("</section>")[0])
    lo_trs = [tr for tr in pair_trs if "Lower wrong action" in tr]
    hi_trs = [tr for tr in pair_trs if "Higher wrong action" in tr]
    assert len(lo_trs) == 2 and len(hi_trs) == 2

    for tr in lo_trs:
        assert "Graphiti" in tr
        assert 'data-k="Wrong action"' in tr
        assert 'data-k="Withheld action"' in tr
        assert 'data-k="Right action"' in tr
        assert 'data-k="Ingest-error cases left out"' in tr
        assert "<b>13</b>" in tr
        assert 'class="lab x">exploratory<' in tr

    for tr in hi_trs:
        assert "Mem0" in tr
        assert 'data-k="Wrong action"' in tr
        assert 'data-k="Withheld action"' in tr
        assert 'data-k="Right action"' in tr
        assert 'data-k="Ingest-error cases left out"' in tr
        assert "<b>2</b>" in tr

    # Decider floor in thead + uniform XX.X% [XX.X, XX.X] format + th[scope] + zero forbidden terms
    assert "Floor · haiku-5-5" in chat_html
    assert "Floor · opencode-deepseek-v4.1-flash" in chat_html
    assert re.search(r"\d+\.\d+%\s*(?:<small>)?\[\d+\.\d+,\s*\d+\.\d+\]", chat_html)
    for th_tag in re.findall(r"<th\b[^>]*>", chat_html):
        assert "scope=" in th_tag, f"th missing scope attribute: {th_tag}"
    assert not re.search(r"style\s*=", chat_html, re.I)
    for bad in (r"\bcommit\b", r"wrong_commit", r"\babstention\b", r"Rule\s+L", r"ctab2026112302", r"Rank\s*#"):
        assert not re.search(bad, chat_html, re.I), f"forbidden string {bad} in chat.html"

    # Real 40-cell verification when untracked table runs exist locally
    if (REPO_ROOT / "results" / "v2" / "table" / "chat_agent" / "haiku-5-5").is_dir():
        real_out = tmp_path / "real-site"
        build_site(REPO_ROOT, real_out, built_at="2026-11-24T12:00:00+00:00")
        real_chat = (real_out / "chat.html").read_text(encoding="utf-8")
        assert real_chat.count("Lower wrong action") >= 10  # 5 deciders in ranked-pairs + 5 deciders in 40-cell board
        assert "Rank #" not in real_chat
        assert "<b>13</b>" in real_chat and "<b>2</b>" in real_chat
        assert "ctab2026112302" not in real_chat

