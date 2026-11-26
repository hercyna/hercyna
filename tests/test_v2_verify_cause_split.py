"""v2 tests: verify support, the offline cause-split diagnostic and release files."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from memory_bench.cases.schema import Case, Event, REQUEST_INFO
from memory_bench.diagnostics.cause_split import (
    CAUSE_KEYS,
    OfflineReplayProxy,
    classify_case_cause,
    compute_cause_split,
)
from memory_bench.engine.freeze import load_protocol
from memory_bench.site.build import build as build_site
from memory_bench.submit.verify import _valid_v2_prefix_sha, verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mk_case(
    cid: str,
    mech: str,
    a_star: str,
    d_star: list[str],
    events: list[Event],
    slot: str = "dataset_export",
) -> Case:
    return Case(
        case_id=cid,
        track="chat_agent",
        mechanism=mech,
        a_star=a_star,
        d_star=d_star,
        events=events,
        query_text="Run the export.",
        metadata={"slot": slot},
    )


def test_r9_a2_classify_case_cause_all_mechanisms():
    """A2: classify_case_cause covers every mechanism and all four mutually exclusive causes."""
    c_h0 = _mk_case(
        "c-h0",
        "H0",
        "EXECUTE_OR_ANSWER",
        [],
        [
            Event(
                id="turn_010",
                date="2026-04-01",
                type="preference",
                subtype="set",
                fields={"slot": "dataset_export", "value": "anonymized_parquet"},
                text="Use anonymized Parquet shards.",
            )
        ],
    )
    assert classify_case_cause(c_h0, "unrelated", "") == "not_stored"
    assert classify_case_cause(c_h0, "User wants anonymized Parquet shards.", "") == "stored_not_returned"
    assert classify_case_cause(c_h0, "Parquet", "Use anonymized Parquet shards.") == "returned_not_used"

    c_h1 = _mk_case(
        "c-h1",
        "H1",
        "USE_UPDATED_PREF",
        ["turn_020", "turn_040"],
        [
            Event(
                id="turn_020",
                date="2026-03-01",
                type="preference",
                subtype="set",
                fields={"slot": "dataset_export", "value": "aggregated_json"},
                text="Always use aggregated summary JSON.",
            ),
            Event(
                id="turn_040",
                date="2026-04-10",
                type="preference",
                subtype="override",
                fields={"slot": "dataset_export", "value": "hashed_csv"},
                text="Switch to hashed-key CSV tables.",
            ),
        ],
    )
    assert classify_case_cause(c_h1, "empty", "") == "not_stored"
    assert classify_case_cause(c_h1, "Switched to hashed-key CSV tables.", "") == "stored_not_returned"
    assert classify_case_cause(c_h1, "hashed csv", "Use hashed-key CSV tables.") == "returned_not_used"
    assert (
        classify_case_cause(
            c_h1,
            "aggregated summary JSON; hashed-key CSV tables",
            "User uses aggregated summary JSON and hashed-key CSV tables.",
        )
        == "conflict_returned"
    )

    c_h2 = _mk_case(
        "c-h2",
        "H2",
        "CHECK_OR_RENEW",
        ["turn_050"],
        [
            Event(
                id="turn_030",
                date="2026-02-01",
                type="permission",
                subtype="grant",
                fields={"slot": "dataset_export", "expires_on": "2026-06-20"},
                text="Grant valid through 2026-06-20.",
            ),
            Event(
                id="turn_050",
                date="2026-04-01",
                type="permission",
                subtype="grant",
                fields={"slot": "dataset_export", "expires_on": "2026-04-13"},
                text="Grant valid only through 2026-04-13.",
            ),
        ],
    )
    assert classify_case_cause(c_h2, "", "") == "not_stored"
    assert classify_case_cause(c_h2, "Expires April 13.", "") == "stored_not_returned"
    assert classify_case_cause(c_h2, "2026-04-13", "Valid through April 13.") == "returned_not_used"
    assert (
        classify_case_cause(c_h2, "04-13 and 06-20", "Valid through 2026-06-20; also through April 13.")
        == "conflict_returned"
    )

    c_h3 = _mk_case(
        "c-h3",
        "H3",
        "REFUSE_OR_GUARD",
        ["turn_060"],
        [
            Event(
                id="turn_055",
                date="2026-03-01",
                type="preference",
                subtype="set",
                fields={"slot": "travel_policy", "value": "rail_standard"},
                text="Use standard-class rail.",
            ),
            Event(
                id="turn_060",
                date="2026-03-20",
                type="boundary",
                subtype="prohibit",
                fields={"slot": "travel_policy", "active": True},
                text="Never finalize or charge any conference travel booking without freeze override.",
            ),
        ],
        slot="travel_policy",
    )
    assert classify_case_cause(c_h3, "rail", "standard-class rail") == "not_stored"
    assert classify_case_cause(c_h3, "never finalize travel without written freeze override", "") == "stored_not_returned"
    assert classify_case_cause(c_h3, "freeze override", "Requires written freeze override.") == "returned_not_used"
    assert (
        classify_case_cause(c_h3, "rail; freeze override", "Use standard-class rail; never finalize without freeze override.")
        == "conflict_returned"
    )

    c_hu1 = _mk_case("c-hu1", "HU1", REQUEST_INFO, [], [])
    assert classify_case_cause(c_hu1, "", "") == "by_rule"

    c_hu2 = _mk_case(
        "c-hu2",
        "HU2",
        REQUEST_INFO,
        ["turn_070", "turn_080"],
        [
            Event(
                id="turn_070",
                date="2026-04-02",
                type="instruction",
                subtype="standing_rule",
                fields={"slot": "dataset_export", "rule_value": "internal_vault_only"},
                text="Write exports exclusively to internal vault.",
            ),
            Event(
                id="turn_080",
                date="2026-04-14",
                type="instruction",
                subtype="standing_rule",
                fields={"slot": "dataset_export", "rule_value": "mirror_partner_bucket"},
                text="Always mirror every export directly to the shared partner bucket.",
            ),
        ],
    )
    assert classify_case_cause(c_hu2, "write to internal vault", "internal vault") == "not_stored"
    assert (
        classify_case_cause(c_hu2, "internal vault AND shared partner bucket", "only internal vault")
        == "stored_not_returned"
    )
    assert (
        classify_case_cause(c_hu2, "both", "Always write to internal vault; also mirror to partner bucket.")
        == "conflict_returned"
    )


def test_r9_a2_compute_cause_split_sums_and_offline_replay_refuses_miss(tmp_path):
    """A2: cause counts sum cleanly per decider & metric; OfflineReplayProxy refuses cache misses."""
    import sqlite3
    import urllib.error
    import urllib.request

    # 1. Verify compute_cause_split sums exactly
    c1 = _mk_case(
        "c1",
        "H0",
        "EXECUTE_OR_ANSWER",
        [],
        [Event(id="t1", date="2026-04-01", type="preference", subtype="set", fields={"slot": "dataset_export", "value": "anonymized_parquet"}, text="parquet")],
    )
    c2 = _mk_case(
        "c2",
        "H1",
        "USE_UPDATED_PREF",
        ["t2", "t3"],
        [
            Event(id="t2", date="2026-03-01", type="preference", subtype="set", fields={"slot": "dataset_export", "value": "aggregated_json"}, text="json"),
            Event(id="t3", date="2026-04-05", type="preference", subtype="override", fields={"slot": "dataset_export", "value": "hashed_csv"}, text="csv"),
        ],
    )
    stores = [
        {"case_id": "c1", "store_text": ""},
        {"case_id": "c2", "store_text": "aggregated summary JSON and hashed-key CSV tables"},
    ]
    views = [
        {"case_id": "c1", "text": "", "units": [], "record_ids": []},
        {"case_id": "c2", "text": "aggregated summary JSON\nhashed-key CSV tables", "units": ["aggregated summary JSON", "hashed-key CSV tables"], "record_ids": []},
    ]
    rows = [
        {"case_id": "c1", "decider": "haiku-5-5", "kind": "gate", "system": "full", "config_label": "default", "budget": None, "repeat": 0, "a_star": "EXECUTE_OR_ANSWER", "action": "EXECUTE_OR_ANSWER"},
        {"case_id": "c2", "decider": "haiku-5-5", "kind": "gate", "system": "full", "config_label": "default", "budget": None, "repeat": 0, "a_star": "USE_UPDATED_PREF", "action": "USE_UPDATED_PREF"},
        {"case_id": "c1", "decider": "haiku-5-5", "kind": "view", "system": "graphiti", "config_label": "e25", "budget": 1000, "repeat": 1, "a_star": "EXECUTE_OR_ANSWER", "action": "REQUEST_INFO"},
        {"case_id": "c2", "decider": "haiku-5-5", "kind": "view", "system": "graphiti", "config_label": "e25", "budget": 1000, "repeat": 1, "a_star": "USE_UPDATED_PREF", "action": "EXECUTE_OR_ANSWER"},
    ]
    res = compute_cause_split(cases=[c1, c2], store_records=stores, view_records=views, metric_rows=rows, reps=40)
    assert res["exploratory"] is True
    hd = res["deciders"]["haiku-5-5"]
    assert hd["wrong"]["total"] == 1
    assert sum(hd["wrong"]["causes"][k]["count"] for k in CAUSE_KEYS) == 1
    assert hd["wrong"]["causes"]["conflict_returned"]["count"] == 1
    assert hd["withheld"]["total"] == 1
    assert sum(hd["withheld"]["causes"][k]["count"] for k in CAUSE_KEYS) == 1
    assert hd["withheld"]["causes"]["not_stored"]["count"] == 1

    # 2. Verify OfflineReplayProxy refuses uncached calls with HTTP 400 (0 network calls)
    db = tmp_path / "empty_cache.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE calls (key TEXT PRIMARY KEY, model TEXT, prompt_version TEXT, reply TEXT, created REAL)")
    con.commit()
    con.close()
    ms = {
        "id": "m",
        "chat": {"base_url": "https://example.invalid/v1", "model": "m"},
        "embeddings": {"base_url": "https://example.invalid/v1", "model": "e"},
    }
    with OfflineReplayProxy(ms, cache_path=db, call_log_paths=[]) as px:
        sc = px.scope("mem0", "k25", "c1")
        req = urllib.request.Request(
            f"{px.base_url}/s/{sc.id}/v1/chat/completions",
            data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTP 400 offline miss refusal")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert px.misses == 1


def test_r9_a1_verify_v2_prefix_and_released_pack():
    """A1: verify accepts Step 1-8 prefix under Step 9 protocol/v2.json and auto-detects v2 packs."""
    proto = load_protocol(REPO_ROOT / "protocol" / "v2.json")
    s8_sha = "d43cb44fd25628424d2d836bc8a7d6939d991737f83b73368d5a90a359fb1552"
    assert _valid_v2_prefix_sha(s8_sha, proto)
    assert not _valid_v2_prefix_sha("0" * 64, proto)

    pack = REPO_ROOT / "results" / "table" / "v2" / "chat_agent" / "haiku-5-5" / "bm25"
    assert pack.is_dir()
    r_explicit = verify(
        pack,
        protocol=REPO_ROOT / "protocol" / "v2.json",
        cases=REPO_ROOT / "data" / "v2" / "chat_agent" / "table_cases.jsonl",
    )
    assert r_explicit.ok, [f.line() for f in r_explicit.failures]

    # Auto-detection via CLI from repo root without flags
    proc = subprocess.run(
        [sys.executable, "-m", "memory_bench.cli", "verify", str(pack)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_r9_a3_release_artifacts_and_zero_ai_or_em_dash_on_built_site(tmp_path):
    """A3: required release files exist and built site contains zero em dashes or AI-generated wording."""
    for rel in (
        "LICENSE",
        "LICENSE-DATA",
        "CITATION.cff",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        ".github/workflows/ci.yml",
    ):
        p = REPO_ROOT / rel
        assert p.is_file(), f"Missing release artifact {rel}"
        txt = p.read_text(encoding="utf-8")
        assert "\u2014" not in txt and "&mdash;" not in txt, f"Em dash in {rel}"

    assert "v2.0.0-rc1" in (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "bench freeze --check" in ci and "pytest" in ci

    out = tmp_path / "site"
    build_site(REPO_ROOT, out, built_at="2026-11-25T12:00:00+00:00")
    pages = sorted(out.glob("*.html"))
    assert len(pages) == 22
    assert {
        "index.html",
        "v1.html",
        "coding.html",
        "chat.html",
        "method.html",
        "submit.html",
        "terms.html",
        "legal.html",
        "privacy.html",
        "changelog.html",
        "corrections.html",
        "404.html",
        "why.html",
        "about.html",
    } <= {p.name for p in pages}
    for page in pages:
        body = page.read_text(encoding="utf-8")
        assert "\u2014" not in body and "&mdash;" not in body, f"Em dash in {page.name}"
        assert not re.search(r"ai[- ]generated", body, re.I), f"AI wording in {page.name}"
        if page.name == "chat.html":
            assert "#chat-ranked-pairs" not in body or "Lower wrong action" in body
            assert "Rank #" not in body
