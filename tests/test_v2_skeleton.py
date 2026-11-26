"""Unit tests for Phase 0a v2 skeleton in memory-bench v2 branch.

Covers:
1. TrackSpec protocol, registry, and AccountReviewTrack delegation.
2. Case.from_dict() v2 schema support (`valid_actions`, optional v1 keys) + byte-exact v1 dumps_case().
3. Track-aware records_of() handling v2 ISO-dated text safely while keeping v1 unchanged.
4. 11-tuple ROW_KEY_V2, ingest_pass == 2 isolation from summarise(), and multi-partition summaries.
5. Pass-keyed audit_views() and atomic manifest sealing of view-audit.json.
6. OllamaChatTransport DEFAULT_NUM_CTX = 65536, context_truncated detection, and ContextOverflowError
   on gate/floor calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from memory_bench.adapters.base import records_of
from memory_bench.cases.sample import dev_cases
from memory_bench.cases.schema import Case, Event, dumps_case, read_jsonl
from memory_bench.deciders.base import DecisionInput
from memory_bench.deciders.model import ModelDecider
from memory_bench.deciders.transports import (
    DEFAULT_NUM_CTX,
    ContextOverflowError,
    OllamaChatTransport,
    RawReply,
)
from memory_bench.engine import files as F
from memory_bench.engine.audit import VIEW_AUDIT, audit_views
from memory_bench.engine.views import views_path
from memory_bench.gateway.proxy import MEMORY_CALLS
from memory_bench.scoring.metrics import (
    ROW_KEY_V2,
    SUMMARY_SCHEMA,
    SUMMARY_SCHEMA_V2,
    row_key_v2,
    summarise,
)
from memory_bench.tracks import (
    AccountReviewTrack,
    TrackSpec,
    get_track,
    list_tracks,
    render_v2_record,
)


def _make_row(
    case_id: str,
    kind: str,
    system: str | None = None,
    budget: int | None = None,
    action: str = "EXPLAIN",
    a_star: str = "EXPLAIN",
    *,
    track: str = "account_review",
    mode: str = "blind_adapter",
    eval_mode: str = "mode_a",
    ingest_pass: int = 1,
    repeat: int = 0,
    decider: str = "test-decider",
) -> dict:
    row = {
        "case_id": case_id,
        "decider": decider,
        "kind": kind,
        "system": system,
        "config_label": None if system is None else "default",
        "budget": budget,
        "repeat": repeat,
        "action": action,
        "a_star": a_star,
        "mechanism": "override",
        "cls": "none",
        "view_tokens": 100,
        "view_units": 5,
    }
    if track != "account_review" or mode != "blind_adapter" or eval_mode != "mode_a" or ingest_pass != 1:
        row.update(track=track, mode=mode, eval_mode=eval_mode, ingest_pass=ingest_pass)
    return row


def test_track_registry_and_account_review_delegation():
    assert "account_review" in list_tracks()
    track = get_track("account_review")
    assert isinstance(track, TrackSpec)
    assert isinstance(track, AccountReviewTrack)
    assert track.meta.case_schema == "memory_bench.case/1"
    assert track.meta.supports_mode_b is False

    case = dev_cases()[0]
    assert track.oracle(case) == case.a_star
    rendered = track.render_record(case, case.events[0])
    assert rendered.startswith(f"{case.events[0].id} {case.events[0].date} ")
    schema = track.action_schema()
    assert "properties" in schema and "action" in schema["properties"]


def test_case_schema_v2_roundtrip_and_v1_byte_identity():
    v2_dict = {
        "schema": "memory_bench.case/2",
        "track": "coding_agent",
        "case_id": "code_override_0001",
        "mechanism": "override",
        "query_text": "Implement webhook signature check.",
        "valid_actions": ["EDIT_FILE", "PATCH_AND_TEST", "REQUEST_INFO", "ESCALATE", "EXPLAIN"],
        "a_star": "PATCH_AND_TEST",
        "d_star": ["ev_pr_042"],
        "events": [
            {
                "id": "ev_pr_042",
                "date": "2026-03-14",
                "type": "pr_review",
                "subtype": "override",
                "text": "[ev_pr_042 2026-03-14] Use HMAC-SHA256 instead of legacy MD5.",
            }
        ],
        "repo_snapshot": {"repo": "synth-webhook", "commit": "abc1234"},
        "metadata": {"difficulty": "medium"},
    }
    c = Case.from_dict(v2_dict)
    assert c.track == "coding_agent"
    assert c.schema == "memory_bench.case/2"
    assert c.valid_actions == v2_dict["valid_actions"]
    assert c.seed == 0
    assert c.query_period == ""
    assert c.events[0].text == v2_dict["events"][0]["text"]
    assert c.repo_snapshot == {"repo": "synth-webhook", "commit": "abc1234"}

    # v1 data/table/cases.jsonl byte-exact SHA-256 preservation
    v1_cases_path = Path(__file__).resolve().parents[1] / "data" / "table" / "cases.jsonl"
    if v1_cases_path.exists():
        raw_bytes = v1_cases_path.read_bytes()
        loaded = list(read_jsonl(v1_cases_path))
        re_emitted = "".join(dumps_case(item) + "\n" for item in loaded).encode("utf-8")
        assert hashlib.sha256(re_emitted).hexdigest() == hashlib.sha256(raw_bytes).hexdigest()
        assert hashlib.sha256(re_emitted).hexdigest() == "7d787c201eba8dd39b248a95020f0f7b602508e888b51ad54f870e1797f814c6"


def test_records_of_v2_plain_format_and_v1_preservation():
    v1_case = dev_cases()[0]
    v1_recs = records_of(v1_case)
    assert len(v1_recs) == len(v1_case.events)
    assert v1_recs[0].id == v1_case.events[0].id

    ev = Event(
        id="ev_pr_042",
        date="2026-03-14",
        type="pr_review",
        subtype="override",
        text="[ev_pr_042 2026-03-14] Use HMAC-SHA256.",
    )
    v2_case = Case(
        case_id="code_001",
        track="coding_agent",
        schema="memory_bench.case/2",
        events=[ev],
        query_text="Check webhook",
        a_star="PATCH_AND_TEST",
    )
    recs = records_of(v2_case)
    assert len(recs) == 1
    assert recs[0].id == "ev_pr_042"
    assert recs[0].date == "2026-03-14"
    # Stripped leading bracket and plain space-delimited format
    assert not recs[0].text.startswith("[")
    assert recs[0].text == "ev_pr_042 2026-03-14 pr_review override Use HMAC-SHA256."
    assert render_v2_record(ev) == recs[0].text


def test_row_key_v2_dual_ingest_isolation_and_multi_partition():
    # Single-partition v1 rows + ingest_pass=2 rows
    v1_rows = [
        _make_row("c1", "gate", action="EXPLAIN", a_star="EXPLAIN"),
        _make_row("c1", "floor", action="REQUEST_INFO", a_star="EXPLAIN"),
        _make_row("c1", "view", system="bm25", budget=1000, action="EXPLAIN", a_star="EXPLAIN"),
        _make_row("c2", "gate", action="HOLD", a_star="HOLD"),
        _make_row("c2", "floor", action="REQUEST_INFO", a_star="HOLD"),
        _make_row("c2", "view", system="bm25", budget=1000, action="HOLD", a_star="HOLD"),
    ]
    # Add ingest_pass=2 rows with deliberately wrong actions; they MUST NOT pollute summarise()
    with_pass2 = [
        *v1_rows,
        _make_row("c1", "view", system="bm25", budget=1000, action="HOLD", a_star="EXPLAIN", ingest_pass=2),
        _make_row("c2", "view", system="bm25", budget=1000, action="EXPLAIN", a_star="HOLD", ingest_pass=2),
    ]
    assert len(ROW_KEY_V2) == 11
    assert row_key_v2(v1_rows[2]) != row_key_v2(with_pass2[-2])

    s_v1 = summarise(v1_rows, seed=11, reps=50)
    s_p2 = summarise(with_pass2, seed=11, reps=50)
    assert s_v1["schema"] == SUMMARY_SCHEMA
    assert s_v1 == s_p2  # Exact identity: ingest_pass=2 completely isolated!

    # Multi-partition v2 run emits schema v2 + partitions dict
    v2_part = [
        _make_row("k1", "gate", action="EXPLAIN", a_star="EXPLAIN", track="coding_agent", mode="harness_mcp", eval_mode="mode_a"),
        _make_row("k1", "floor", action="REQUEST_INFO", a_star="EXPLAIN", track="coding_agent", mode="harness_mcp", eval_mode="mode_a"),
        _make_row("k1", "view", system="bm25", budget=1000, action="EXPLAIN", a_star="EXPLAIN", track="coding_agent", mode="harness_mcp", eval_mode="mode_a"),
    ]
    s_multi = summarise([*v1_rows, *v2_part], seed=11, reps=50)
    assert s_multi["schema"] == SUMMARY_SCHEMA_V2
    assert set(s_multi["partitions"].keys()) == {
        "account_review/blind_adapter/mode_a",
        "coding_agent/harness_mcp/mode_a",
    }


def test_audit_views_pass_keyed_and_seals_manifest(tmp_path: Path):
    run_dir = tmp_path / "run_audit"
    run_dir.mkdir()
    vp = views_path(run_dir)
    vp.parent.mkdir(parents=True, exist_ok=True)
    # Pass 1 succeeds cleanly; Pass 2 has a failed memory call inside its window
    view_lines = [
        {
            "case_id": "c1",
            "system": "mem0",
            "config_label": "default",
            "ingest_pass": 1,
            "at": "2026-04-01T10:00:10+00:00",
            "ingest_seconds": 5.0,
            "retrieve_seconds": 1.0,
            "records": [],
        },
        {
            "case_id": "c1",
            "system": "mem0",
            "config_label": "default",
            "ingest_pass": 2,
            "at": "2026-04-01T10:05:10+00:00",
            "ingest_seconds": 5.0,
            "retrieve_seconds": 1.0,
            "records": [],
        },
    ]
    vp.write_text("\n".join(json.dumps(x) for x in view_lines) + "\n")

    call_lines = [
        {
            "case_id": "c1",
            "adapter": "mem0",
            "config_label": "default",
            "ingest_pass": 1,
            "status": 200,
            "at": "2026-04-01T10:00:07+00:00",
        },
        {
            "case_id": "c1",
            "adapter": "mem0",
            "config_label": "default",
            "ingest_pass": 2,
            "status": 500,
            "at": "2026-04-01T10:05:07+00:00",
        },
    ]
    (run_dir / MEMORY_CALLS).write_text("\n".join(json.dumps(x) for x in call_lines) + "\n")
    (run_dir / F.MANIFEST).write_text(
        json.dumps({"run_id": "r1", "files": {"metrics.jsonl": "0" * 64}})
    )

    out = audit_views(run_dir, write=True, seal_manifest=True)
    assert out["views_checked"] == 2
    assert out["views_with_failed_call"] == 1
    assert (run_dir / VIEW_AUDIT).exists()

    sealed_manifest = json.loads((run_dir / F.MANIFEST).read_text())
    assert VIEW_AUDIT in sealed_manifest["files"]
    assert sealed_manifest["files"][VIEW_AUDIT] == F.sha256_file(run_dir / VIEW_AUDIT)


def test_ollama_num_ctx_default_and_context_overflow_on_gate_floor():
    assert DEFAULT_NUM_CTX == 65536

    captured_body: dict = {}

    def fake_opener(url, data, headers, timeout):
        nonlocal captured_body
        captured_body = json.loads(data.decode("utf-8"))
        body = json.dumps({
            "message": {"content": '{"action": "EXPLAIN", "reason": "ok"}'},
            "prompt_eval_count": 65536,
            "eval_count": 12,
        }).encode("utf-8")
        return 200, body

    cfg = {
        "id": "local-qwen",
        "transport": "ollama",
        "base_url": "http://127.0.0.1:11535",
        "model": "qwen3:32b",
        "prompt_version": "v1",
    }
    t = OllamaChatTransport(cfg, opener=fake_opener)
    reply = t.send([{"role": "user", "content": "hi"}], repeat=0)
    assert captured_body["options"]["num_ctx"] == 65536
    assert reply.context_truncated is True
    assert t.truncated == 1

    # ModelDecider raises ContextOverflowError on gate and floor, NOT on view
    gw = MagicMock()
    gw.call.return_value = RawReply(
        text='{"action": "EXPLAIN", "reason": "ok"}',
        input_tokens=65536,
        output_tokens=10,
        context_truncated=True,
    )
    decider = ModelDecider(
        cfg={
            "id": "local-qwen",
            "transport": "ollama",
            "base_url": "http://127.0.0.1:11535",
            "model": "qwen3:32b",
            "prompt_version": "v1",
            "params": {},
        },
        transport=t,
        gateway=gw,
    )
    inp = DecisionInput(case_id="c1", query_text="Review account.", records_text="e1 2026-01-01 note ok", record_ids=["e1"], repeat=0)

    with pytest.raises(ContextOverflowError):
        decider.decide(inp, context={"kind": "gate"})
    with pytest.raises(ContextOverflowError):
        decider.decide(inp, context={"kind": "floor"})

    # View calls tolerate truncation without raising ContextOverflowError
    assert decider.decide(inp, context={"kind": "view"}) == "EXPLAIN"
