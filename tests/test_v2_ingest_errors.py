"""v2 tests: ingest-error verification, by-rule causes, replay fidelity and step prefixes."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from memory_bench.adapters import ADAPTERS, get_adapter
from memory_bench.site.build import _CAUSE_SENTENCES
from memory_bench.cases.schema import Case, Event, REQUEST_INFO
from memory_bench.diagnostics.cause_split import (
    CAUSE_KEYS,
    classify_case_cause,
    compute_cause_split,
    format_cause_split_markdown,
)
from memory_bench.engine import files as F
from memory_bench.scoring import summarise
from memory_bench.site.build import build as build_site
from memory_bench.submit.verify import verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mk_case(cid: str, mech: str, a_star: str, events: list[Event]) -> Case:
    return Case(
        case_id=cid,
        track="chat_agent",
        mechanism=mech,
        a_star=a_star,
        d_star=[],
        events=events,
        query_text="Run the export.",
        metadata={"slot": "dataset_export"},
    )


def test_r10_a1_all_40_shipped_cells_and_a4_matched_step(capsys):
    """A1 & A4: all 40 shipped cells end in pass or 'ingest error, documented' and name matched protocol v2 Steps."""
    troot = REPO_ROOT / "results" / "table" / "v2" / "chat_agent"
    cells = sorted(p for dec in troot.iterdir() if dec.is_dir() for p in dec.iterdir() if p.is_dir())
    assert len(cells) == 40
    for cdir in cells:
        res = verify(cdir, protocol=REPO_ROOT / "protocol" / "v2.json", cases=REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl")
        assert res.ok, f"{cdir}: {[f.line() for f in res.failures]}"
        verdict_line = res.verdict()
        assert "matched protocol v2 Steps 1 to 8" in verdict_line
        if cdir.name == "mem0":
            assert "ingest error, documented (2 cases)" in verdict_line
        elif cdir.name == "graphiti":
            assert "ingest error, documented (13 cases)" in verdict_line
        else:
            assert "ingest error, documented" not in verdict_line

    # CLI check prints the exact matched step and documented ingest error state
    proc = subprocess.run(
        [sys.executable, "-m", "memory_bench.cli", "verify", str(troot / "haiku-5-5" / "graphiti")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "matched protocol v2 Steps 1 to 8" in proc.stdout
    assert "ingest error, documented (13 cases)" in proc.stdout

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Mem0 has 2 ingest-error cases and Graphiti has 13" in readme


def test_r10_a1_undocumented_missing_row_still_fails_check3_and_textfree_guards_health(tmp_path):
    """A1: removing any row for a case NOT documented in health.json still fails check 3; health free text fails check 2."""
    src = REPO_ROOT / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5" / "mem0"
    copy_dir = tmp_path / "mem0_tampered"
    shutil.copytree(src, copy_dir)

    rows = F.read_metrics(copy_dir / F.METRICS)
    man = F.read_json(copy_dir / F.MANIFEST)
    health = F.read_json(copy_dir / F.HEALTH)
    documented_ids = set(man["incomplete_cells"][0]["missing_cases"])
    # Drop all view rows for one non-error case not in health.json / incomplete_cells
    target_cid = next(
        r["case_id"]
        for r in rows
        if r["kind"] == "view" and r["case_id"] not in documented_ids
    )
    trimmed = [r for r in rows if not (r["kind"] == "view" and r["case_id"] == target_cid)]
    assert len(trimmed) < len(rows)
    (copy_dir / F.METRICS).write_text("".join(F.row_line(r) + "\n" for r in trimmed), encoding="utf-8")
    man["files"][F.METRICS] = F.sha256_file(copy_dir / F.METRICS)
    new_sum = {**summarise(trimmed, seed=man["bootstrap"]["seed"], reps=man["bootstrap"]["reps"]), "grade": man["grade"]}
    F.write_json(copy_dir / F.SUMMARY, new_sum)
    man["files"][F.SUMMARY] = F.sha256_file(copy_dir / F.SUMMARY)
    F.write_json(copy_dir / F.MANIFEST, man)

    res = verify(
        copy_dir,
        protocol=REPO_ROOT / "protocol" / "v2.json",
        cases=REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl",
    )
    assert not res.ok
    assert 3 in res.failed_checks
    assert any(target_cid in f.detail and "missing row" in f.detail for f in res.failures if f.check == 3)

    # Arbitrary free text placed in health.json last_error.error still fails check 2
    health["adapters"][1]["last_error"]["error"] = "The user asked us to wait until Friday before confirming"
    F.write_json(copy_dir / F.HEALTH, health)
    man["files"][F.HEALTH] = F.sha256_file(copy_dir / F.HEALTH)
    F.write_json(copy_dir / F.MANIFEST, man)
    res2 = verify(
        copy_dir,
        protocol=REPO_ROOT / "protocol" / "v2.json",
        cases=REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl",
    )
    assert 2 in res2.failed_checks


def test_r10_a2_a3_by_rule_column_and_replay_fidelity():
    """A2 & A3: by_rule separates rule-set cases; shipped cause-split JSONs include by_rule & replay_fidelity."""
    assert "by_rule" in CAUSE_KEYS
    c_hu1 = _mk_case("hu1", "HU1", REQUEST_INFO, [])
    c_h0_no_pref = _mk_case("h0np", "H0", "EXECUTE_OR_ANSWER", [])
    assert classify_case_cause(c_hu1, "", "") == "by_rule"
    assert classify_case_cause(c_h0_no_pref, "", "") == "by_rule"

    res = compute_cause_split(
        cases=[c_hu1],
        store_records=[{"case_id": "hu1", "store_text": "some stored text"}],
        view_records=[{"case_id": "hu1", "text": "missing unit", "units": ["missing unit"], "record_ids": []}],
        metric_rows=[
            {"case_id": "hu1", "decider": "d1", "kind": "gate", "system": "full", "config_label": "default", "budget": None, "repeat": 0, "a_star": REQUEST_INFO, "action": REQUEST_INFO},
            {"case_id": "hu1", "decider": "d1", "kind": "view", "system": "m", "config_label": "c", "budget": 1000, "repeat": 1, "a_star": REQUEST_INFO, "action": "EXECUTE_OR_ANSWER"},
        ],
        reps=20,
    )
    assert res["deciders"]["d1"]["wrong"]["causes"]["by_rule"]["count"] == 1
    assert res["replay_fidelity"]["low_fidelity"] is True
    md = format_cause_split_markdown({**res, "product": "m"})
    assert "By rule" in md and "low-fidelity" in md

    cdir = REPO_ROOT / "results" / "v2" / "diagnostics" / "cause_split"
    for prod, want_low in (("graphiti", False), ("mem0", True), ("langmem", True)):
        data = json.loads((cdir / f"{prod}_cause_split.json").read_text(encoding="utf-8"))
        assert "replay_fidelity" in data
        rf = data["replay_fidelity"]
        assert isinstance(rf["view_unit_match_rate"], float)
        assert rf["low_fidelity"] is want_low
        for did, dd in data["deciders"].items():
            for kind in ("wrong", "withheld", "combined"):
                causes = dd[kind]["causes"]
                assert tuple(causes.keys()) == tuple(sorted(CAUSE_KEYS)) or set(causes) == set(CAUSE_KEYS)
                assert sum(causes[k]["count"] for k in CAUSE_KEYS) == dd[kind]["total"]


def test_r10_c1_external_adapter_zero_edits_outside_folder(tmp_path, monkeypatch):
    """C1: external adapter in adapters/<name>/adapter.py runs, packs, and verifies with zero repo edits."""
    adir = tmp_path / "adapters" / "toy_dict"
    adir.mkdir(parents=True)
    (adir / "adapter.py").write_text(
        """from memory_bench.adapters.base import MemoryAdapter, Retrieved

class ToyDictAdapter(MemoryAdapter):
    name = "toy_dict"
    product_version = "0.1.0"
    config_label = "default"
    reference = False
    needs_proxy = False

    def __init__(self):
        self._order = []
        self._docs = {}

    def reset(self, namespace: str) -> None:
        self._order = []
        self._docs = {}

    def ingest(self, records):
        for r in records:
            self._order.append(r.id)
            self._docs[r.id] = r.text

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        terms = set(query.lower().split())
        rank = {rid: i for i, rid in enumerate(self._order)}
        scored = [(sum(1 for t in terms if t in self._docs[rid].lower()), rank[rid], rid) for rid in self._order]
        hits = [x for x in scored if x[0] > 0]
        hits.sort(key=lambda x: (-x[0], x[1]))
        chosen = sorted([rid for _, _, rid in hits[:15]], key=rank.__getitem__)
        units = [self._docs[rid] for rid in chosen]
        return Retrieved(text="\\n".join(units), record_ids=chosen, units=units)

    def close(self) -> None:
        self._order = []
        self._docs = {}
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert "bm25" in ADAPTERS and "toy_dict" in ADAPTERS
    inst = get_adapter("toy_dict")
    assert inst.name == "toy_dict" and inst.product_version == "0.1.0"

    run_out = tmp_path / "run_toy"
    proc_run = subprocess.run(
        [sys.executable, "-m", "memory_bench.cli", "run", "--mock", "--n", "6", "--cases",
         str(REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl"),
         "--adapters", "toy_dict", "--budgets", "1000", "--repeats", "2", "--out", str(run_out)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc_run.returncode == 0, proc_run.stdout + proc_run.stderr

    sub_out = tmp_path / "submissions"
    proc_pack = subprocess.run(
        [sys.executable, "-m", "memory_bench.cli", "submit", "pack", str(run_out), "--out", str(sub_out), "--exploratory"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc_pack.returncode == 0, proc_pack.stdout + proc_pack.stderr
    packed = next(sub_out.glob("toy_dict/*"))
    res = verify(packed)
    assert res.ok, [f.line() for f in res.failures]


def test_r10_c2_c3_c4_front_page_finding_limits_and_positioning(tmp_path):
    """C2, C3, C4: site renders exploratory cause split on front page, honest limits on site/README, and positioning doc."""
    out = tmp_path / "site"
    build_site(REPO_ROOT, out, built_at="2026-11-25T12:00:00+00:00")
    index_html = (out / "index.html").read_text(encoding="utf-8")
    chat_html = (out / "chat.html").read_text(encoding="utf-8")
    method_html = (out / "method.html").read_text(encoding="utf-8")

    # C2: Front-page finding & exploratory cause split
    for phrase in (
        'id="cause-split"',
        *(html.escape(_CAUSE_SENTENCES[k]) for k in ("graphiti", "mem0", "langmem")),
        "low-fidelity",
        "By rule",
        "exploratory",
    ):
        assert phrase in index_html, f"Missing in index.html: {phrase}"
        assert phrase in chat_html, f"Missing in chat.html: {phrase}"

    # C4: What this does not show on site + README
    for page_name, html_text in (("index.html", index_html), ("chat.html", chat_html), ("method.html", method_html)):
        assert "What this does not show" in html_text, page_name
        assert "Five deciders" in html_text, page_name
        assert "Synthetic cases" in html_text, page_name
        assert "Ingest-error cases left out" in html_text, page_name
        assert "exploratory" in html_text, page_name
        assert "One chat track only" in html_text, page_name

    # Public wording, zero em dash, zero Rank #, zero AI-generated across all 7 pages
    for page in sorted(out.glob("*.html")):
        txt = page.read_text(encoding="utf-8")
        assert "\u2014" not in txt and "&mdash;" not in txt, f"Em dash in {page.name}"
        assert "Rank #" not in txt, f"Rank # in {page.name}"
        assert not re.search(r"ai[- ]generated", txt, re.I), page.name
        for th_tag in re.findall(r"<th\b[^>]*>", txt):
            assert "scope=" in th_tag, f"Missing scope in {page.name}: {th_tag}"

    # C3: docs/positioning.md
    pos = (REPO_ROOT / "docs" / "positioning.md").read_text(encoding="utf-8")
    for token in ("LongMemEval", "LoCoMo", "MemoryAgentBench", "https://github.com/xiaowu0162/LongMemEval", "https://github.com/snap-research/locomo"):
        assert token in pos, token
    assert "\u2014" not in pos and "&mdash;" not in pos
