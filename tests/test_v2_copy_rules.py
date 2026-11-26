"""v2 tests: no em dashes, gated count split under every row and row labels."""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from memory_bench.engine.freeze import check_protocol
from memory_bench.scoring.metrics import summarise
from memory_bench.site.build import build as build_site

REPO_ROOT = Path(__file__).resolve().parent.parent

SPLIT_RE = re.compile(
    r"(\d+)\s+cases:\s+(\d+)\s+validity\s+fails?\s*\+\s*(\d+)\s+ingest\s+errors?\s*\u2212\s*(\d+)\s+cases?\s+in\s+both\s*\u2192\s*(\d+)\s+gated"
)


def _strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _make_synthetic_repo(tmp_path: Path) -> Path:
    """Create a deterministic 2-decider x 3-system v2 repo with overlapping validity fails & ingest errors."""
    root = tmp_path / "syn-repo"
    (root / "protocol").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "protocol" / "v1.json", root / "protocol" / "v1.json")
    shutil.copy(REPO_ROOT / "protocol" / "v2.json", root / "protocol" / "v2.json")
    if (REPO_ROOT / "site" / "copy").is_dir():
        shutil.copytree(REPO_ROOT / "site" / "copy", root / "site" / "copy")

    n_cases = 260
    proto_sha = "6267bed29d33f962ac41bb5b20148f6531b3b067a527aa94dd31e47c8f98a6b7"
    cases_sha = "48ab661a44a8edeb9b93f38b73d429d97a68433133af99f8c0cff7ea844cd1c0"

    gate_fails_by_dec = {
        "haiku-5-5": {0, 5, 259},                 # 3 validity fails (2 overlap graphiti 0..12, 0 overlap mem0 13..14)
        "opencode-deepseek-v4.1-flash": {14, 258}, # 2 validity fails (0 overlap graphiti 0..12, 1 overlaps mem0 13..14)
    }

    for did, fail_set in gate_fails_by_dec.items():
        base_rows = []
        for idx in range(n_cases):
            cid = f"csyn-{idx:04d}"
            g_act = "EXECUTE_OR_ANSWER" if idx in fail_set else "USE_UPDATED_PREF"
            base_rows.append({
                "case_id": cid, "track": "chat_agent", "mode": "blind_adapter", "eval_mode": "mode_a",
                "decider": did, "kind": "gate", "system": "full", "config_label": "default",
                "budget": None, "ingest_pass": 1, "repeat": 0, "a_star": "USE_UPDATED_PREF",
                "mechanism": "H1", "action": g_act, "view_tokens": 5000, "view_units": 80,
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
            ("mem0", "k25", {13, 14}, lambda i: "EXECUTE_OR_ANSWER" if i < 190 else "USE_UPDATED_PREF"),
            ("bm25", "default", set(), lambda i: "EXECUTE_OR_ANSWER" if i < 120 else "USE_UPDATED_PREF"),
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
            inc = (
                [{"cell": f"{sys_name}/{cfg}@1000", "missing_cases": [f"csyn-{k:04d}" for k in sorted(missing_idxs)]}]
                if missing_idxs
                else []
            )
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
                "incomplete_cells": inc,
            }
            (rdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return root


def _assert_no_em_dash_in_dir(site_dir: Path) -> None:
    pages = sorted(site_dir.glob("*.html"))
    names = {p.name for p in pages}
    assert {"index.html", "coding.html", "chat.html", "method.html", "submit.html", "why.html", "about.html"} <= names
    for page in pages:
        txt = page.read_text(encoding="utf-8")
        assert "\u2014" not in txt, f"Em dash found in {page.name}"
        assert "&mdash;" not in txt, f"&mdash; entity found in {page.name}"
        assert "&#8212;" not in txt and "&#x2014;" not in txt.lower(), f"Numeric em dash entity found in {page.name}"


def _verify_ranked_row_splits(chat_html: str) -> list[tuple[str, int, int, int, int, int]]:
    """Verify every ranked row in chat.html is immediately followed by a v2-row-split whose 4 numbers add up."""
    checked: list[tuple[str, int, int, int, int, int]] = []
    tables = re.findall(r'<table class="v2-full-tbl"[^>]*>[\s\S]*?</table>', chat_html)
    assert tables, "Expected at least one v2-full-tbl in chat.html"

    for tbl in tables:
        floor_m = re.search(r"<b>Floor\s*·\s*([^<]+)</b>\s*\(full history,\s*(\d+)\s+gated of\s+(\d+)\s+cases\)", tbl)
        assert floor_m, f"Missing floor header in table: {tbl[:220]}"
        did = html.unescape(floor_m.group(1)).strip()
        floor_gated = int(floor_m.group(2))
        floor_total = int(floor_m.group(3))

        tbody = tbl.split("<tbody", 1)[1]
        trs = re.findall(r"<tr\b[^>]*>[\s\S]*?</tr>", tbody)
        idx = 0
        while idx < len(trs):
            tr = trs[idx]
            is_ranked = ("Lower wrong action" in tr) or ("Higher wrong action" in tr)
            if is_ranked:
                assert idx + 1 < len(trs), f"Ranked row on {did} has no split row beneath it"
                split_tr = trs[idx + 1]
                assert 'class="v2-row-split"' in split_tr, f"Expected v2-row-split beneath ranked row on {did}"
                visible = _strip_tags(split_tr)
                m = SPLIT_RE.search(visible)
                assert m, f"Unparseable gated split text {visible!r} on {did}"
                n_cases, vf, ie, both, gated = (int(g) for g in m.groups())

                # Cross-check against table header and row ingest-error cell
                ing_cell_m = re.search(r'data-k="Ingest-error cases left out"[^>]*>\s*<div[^>]*><b>(\d+)</b>', tr)
                assert ing_cell_m, f"Missing ingest-error cell in ranked row on {did}"
                assert ie == int(ing_cell_m.group(1))
                assert n_cases == floor_total
                assert vf == floor_total - floor_gated
                assert 0 <= both <= min(vf, ie)
                assert n_cases - (vf + ie - both) == gated, (
                    f"Gated split math failed on {did}: {n_cases} - ({vf} + {ie} - {both}) != {gated}"
                )
                sys_m = re.search(r'<span class="sys">([^<]+)</span>', tr)
                label = f"{did}:{sys_m.group(1) if sys_m else '?'}"
                checked.append((label, n_cases, vf, ie, both, gated))
                idx += 2
            else:
                idx += 1
    return checked


def test_r8_a1_no_em_dash_in_any_built_page(tmp_path):
    """A1 (P3-1): no built HTML page contains '\\u2014' or '&mdash;', and chat.html uses spaced en dashes."""
    # 1. Empty / clean-repo build (without untracked table runs)
    empty_root = tmp_path / "empty-repo"
    (empty_root / "protocol").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "protocol" / "v1.json", empty_root / "protocol" / "v1.json")
    shutil.copy(REPO_ROOT / "protocol" / "v2.json", empty_root / "protocol" / "v2.json")
    if (REPO_ROOT / "site" / "copy").is_dir():
        shutil.copytree(REPO_ROOT / "site" / "copy", empty_root / "site" / "copy")
    build_site(empty_root, tmp_path / "out-empty", built_at="2026-11-24T12:00:00+00:00")
    _assert_no_em_dash_in_dir(tmp_path / "out-empty")

    # 2. Populated synthetic v2 build
    syn_root = _make_synthetic_repo(tmp_path)
    build_site(syn_root, tmp_path / "out-syn", built_at="2026-11-24T12:00:00+00:00")
    _assert_no_em_dash_in_dir(tmp_path / "out-syn")
    syn_chat = (tmp_path / "out-syn" / "chat.html").read_text(encoding="utf-8")
    assert " \u2013 " in syn_chat  # spaced en dash in title & subtitle

    # 3. Full workspace build (both default and include_exploratory=True)
    build_site(REPO_ROOT, tmp_path / "out-repo", built_at="2026-11-24T12:00:00+00:00")
    _assert_no_em_dash_in_dir(tmp_path / "out-repo")
    build_site(REPO_ROOT, tmp_path / "out-repo-exp", include_exploratory=True, built_at="2026-11-24T12:00:00+00:00")
    _assert_no_em_dash_in_dir(tmp_path / "out-repo-exp")


def test_r8_a2_gated_count_split_adds_up_for_every_ranked_row(tmp_path):
    """A2 (P3-2): under each ranked row, validity fails, ingest errors, cases in both, and gated total add up."""
    syn_root = _make_synthetic_repo(tmp_path)
    build_site(syn_root, tmp_path / "out-syn", built_at="2026-11-24T12:00:00+00:00")
    syn_chat = (tmp_path / "out-syn" / "chat.html").read_text(encoding="utf-8")
    syn_checked = _verify_ranked_row_splits(syn_chat)
    # 2 deciders x 2 ranked rows in #chat-ranked-pairs + 2 deciders x 2 ranked rows in #chat-board = 8
    assert len(syn_checked) == 8
    assert ("haiku-5-5:Graphiti", 260, 3, 13, 2, 246) in syn_checked
    assert ("haiku-5-5:Mem0", 260, 3, 2, 0, 255) in syn_checked
    assert ("opencode-deepseek-v4.1-flash:Graphiti", 260, 2, 13, 0, 245) in syn_checked
    assert ("opencode-deepseek-v4.1-flash:Mem0", 260, 2, 2, 1, 257) in syn_checked

    if (REPO_ROOT / "results" / "v2" / "table" / "chat_agent" / "haiku-5-5").is_dir():
        build_site(REPO_ROOT, tmp_path / "out-real", built_at="2026-11-24T12:00:00+00:00")
        real_chat = (tmp_path / "out-real" / "chat.html").read_text(encoding="utf-8")
        real_checked = _verify_ranked_row_splits(real_chat)
        # 5 deciders x 2 ranked rows in #chat-ranked-pairs + 5 deciders x 2 ranked rows in #chat-board = 20
        assert len(real_checked) == 20
        assert ("haiku-5-5:Graphiti", 300, 11, 13, 1, 277) in real_checked
        assert ("haiku-5-5:Mem0", 300, 11, 2, 0, 287) in real_checked


def test_r8_a3_lower_wrong_action_badges_and_no_rank_numbers(tmp_path):
    """A3 (D-2): badges show 'Lower wrong action' and 'Higher wrong action'; 'Rank #' never appears; freeze intact."""
    assert check_protocol(REPO_ROOT / "protocol" / "v2.json") == []

    syn_root = _make_synthetic_repo(tmp_path)
    build_site(syn_root, tmp_path / "out-syn", built_at="2026-11-24T12:00:00+00:00")
    for page in sorted((tmp_path / "out-syn").glob("*.html")):
        txt = page.read_text(encoding="utf-8")
        assert "Rank #" not in txt and not re.search(r"rank\s*#\s*\d", txt, re.I), f"Rank # in {page.name}"
        assert not re.search(r"\brank\s+[12]\b", txt, re.I), f"Rank number in {page.name}"

    syn_chat = (tmp_path / "out-syn" / "chat.html").read_text(encoding="utf-8")
    assert '<span class="rank-tag">Lower wrong action</span>' in syn_chat
    assert '<span class="rank-tag r2">Higher wrong action</span>' in syn_chat

    if (REPO_ROOT / "results" / "v2" / "table" / "chat_agent" / "haiku-5-5").is_dir():
        build_site(REPO_ROOT, tmp_path / "out-real", built_at="2026-11-24T12:00:00+00:00")
        for page in sorted((tmp_path / "out-real").glob("*.html")):
            txt = page.read_text(encoding="utf-8")
            assert "Rank #" not in txt and not re.search(r"rank\s*#\s*\d", txt, re.I), f"Rank # in {page.name}"
            assert not re.search(r"\brank\s+[12]\b", txt, re.I), f"Rank number in {page.name}"
        real_chat = (tmp_path / "out-real" / "chat.html").read_text(encoding="utf-8")
        assert real_chat.count('<span class="rank-tag">Lower wrong action</span>') == 10
        assert real_chat.count('<span class="rank-tag r2">Higher wrong action</span>') == 10
