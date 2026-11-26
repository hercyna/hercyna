"""v2 core tests: tracks, metrics, deciders and the kappa plan."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from memory_bench.adapters.base import records_of
from memory_bench.cases.schema import dumps_case, read_jsonl, write_jsonl
from memory_bench.deciders.base import DecisionInput
from memory_bench.deciders.mock import MockNoisyDecider, MockOracleDecider
from memory_bench.deciders.model import decider_spec, load_config, load_decider
from memory_bench.deciders.transports import (
    OllamaChatTransport,
    OpenCodeCliTransport,
    RateLimitError,
    make_transport,
)
from memory_bench.engine import RunConfig, files as F, run
from memory_bench.engine.audit import VIEW_AUDIT
from memory_bench.engine.freeze import check_protocol, load_protocol
from memory_bench.engine.validity import run_validity, validity_sample
from memory_bench.gateway import Gateway
from memory_bench.scoring.acbi import compute_acbi
from memory_bench.scoring.chow import chow_summary
from memory_bench.scoring.roi import compute_roi
from memory_bench.scoring.temporal import compute_temporal
from memory_bench.submit.pack import pack
from memory_bench.submit.textfree import is_free_text, scan_run
from memory_bench.submit.verify import verify
from memory_bench.tracks import ChatAgentTrack, get_track, list_tracks
from memory_bench.tracks.chat_agent import (
    CHAT_ACTIONS,
    CHAT_MECHANISMS,
    EXECUTE_OR_ANSWER,
    REQUEST_INFO,
    SLOTS,
    generate_chat_cases,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_OPENCODE_MODELS = {
    "opencode/beam-free": "open-weight",
    "opencode/big-pickle": "free, weights not public",
    "opencode/deepseek-v4.1-flash-free": "open-weight",
    "opencode/flint-40b-free": "open-weight",
    "opencode/hy4-flash-free": "open-weight",
    "opencode/iceman-free": "free, weights not public",
    "opencode/mimo-v2.6-flash-free": "open-weight",
    "opencode/nemotron-3.5-lightning-free": "open-weight",
    "opencode/nemotron-3.5-super-vl-free": "open-weight",
    "opencode/tidepool-alpha-free": "free, weights not public",
}


# ---------------------------------------------------------------------------
# A2: Local runs stay off shared Ollama :11434
# ---------------------------------------------------------------------------

def test_a2_ollama_requires_explicit_base_url():
    with pytest.raises(ValueError, match="11535|base_url"):
        OllamaChatTransport({"model": "qwen3:8b"})
    with pytest.raises(ValueError, match="11535|base_url"):
        OllamaChatTransport({"model": "qwen3:8b", "base_url": ""})
    with pytest.raises(ValueError, match="11535|base_url"):
        load_config({
            "id": "local-test",
            "transport": "ollama",
            "model": "qwen3:8b",
            "price": {"input_per_m": 0, "output_per_m": 0},
        })
    cfg = load_config({
        "id": "local-test",
        "transport": "ollama",
        "base_url": "http://127.0.0.1:11535",
        "model": "qwen3:8b",
        "price": {"input_per_m": 0, "output_per_m": 0},
    })
    t = make_transport(cfg)
    assert isinstance(t, OllamaChatTransport)
    assert t.url == "http://127.0.0.1:11535/api/chat"


def test_a2_v2_local_configs_point_to_private_port_11535():
    dec_path = REPO_ROOT / "configs" / "v2" / "local" / "ollama-decider-private.example.json"
    mem_path = REPO_ROOT / "configs" / "v2" / "memory-side" / "local-ollama-private.example.json"
    dec = load_config(dec_path)
    assert dec["base_url"] == "http://127.0.0.1:11535"
    assert "11434" not in dec["base_url"]

    mem = json.loads(mem_path.read_text(encoding="utf-8"))
    assert mem["chat"]["base_url"] == "http://127.0.0.1:11535/v1"
    assert mem["embeddings"]["base_url"] == "http://127.0.0.1:11535/v1"
    assert "11434" not in mem["chat"]["base_url"]
    assert "11434" not in mem["embeddings"]["base_url"]


# ---------------------------------------------------------------------------
# A6: OpenCode CLI transport & Phase 1 decider roster
# ---------------------------------------------------------------------------

def test_a6_opencode_cli_isolated_xdg_memory_db_and_tools_off():
    captured: dict[str, Any] = {}

    def fake_runner(argv, stdin, timeout, env=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        cfg_dir = Path(captured["env"]["OPENCODE_CONFIG_DIR"])
        captured["cfg_text"] = (cfg_dir / "opencode.jsonc").read_text(encoding="utf-8")
        ndjson = "\n".join([
            json.dumps({"type": "text", "part": {"text": '{"action":"USE_UPDATED_PREF","reason":"override"}'}}),
            json.dumps({"type": "step_finish", "part": {"tokens": {"input": 320, "output": 24}}}),
        ])
        return 0, ndjson, ""

    t = OpenCodeCliTransport(
        {"model": "opencode/deepseek-v4.1-flash-free", "command": ["opencode"]},
        runner=fake_runner,
        sleep=lambda s: None,
    )
    reply = t.send(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
        repeat=0,
    )
    assert json.loads(reply.text)["action"] == "USE_UPDATED_PREF"
    assert reply.input_tokens == 320
    assert reply.output_tokens == 24
    assert "--standalone" in captured["argv"]
    assert "--format" in captured["argv"] and "json" in captured["argv"]
    assert captured["env"]["OPENCODE_DB"] == ":memory:"
    assert captured["env"]["XDG_CONFIG_HOME"] != str(Path.home() / ".config")
    cfg_obj = json.loads(captured["cfg_text"])
    assert cfg_obj["tools"] == {"*": False}
    assert cfg_obj["permission"] == {"*": "deny"}


def test_a6_opencode_cli_rate_limit_backoff_without_switching_models():
    sleeps: list[float] = []
    models_seen: list[str] = []

    def rate_limited_runner(argv, stdin, timeout, env=None):
        m_idx = argv.index("--model") + 1
        models_seen.append(argv[m_idx])
        err = {"type": "error", "error": {"type": "provider.free-limit", "status": 429, "retryAfterMs": 2000}}
        return 1, json.dumps(err), ""

    t = OpenCodeCliTransport(
        {"model": "opencode/beam-free", "command": ["opencode"], "retries": 2, "max_retry_sleep_s": 5.0},
        runner=rate_limited_runner,
        sleep=sleeps.append,
    )
    with pytest.raises(RateLimitError) as exc:
        t.send([{"role": "user", "content": "hi"}], repeat=0)
    assert exc.value.retry_after_ms == 2000
    assert models_seen == ["opencode/beam-free", "opencode/beam-free", "opencode/beam-free"]
    assert len(sleeps) == 2
    assert all(s >= 2.0 for s in sleeps)


def test_a6_phase1_decider_roster_and_provenance(tmp_path):
    dec_dir = REPO_ROOT / "configs" / "v2" / "deciders"
    files = sorted(dec_dir.glob("*.json"))
    assert len(files) == 17
    cfgs = [load_config(p) for p in files]
    by_id = {c["id"]: c for c in cfgs}
    for anth_id in ("haiku-5-5", "sonnet-5-5"):
        assert anth_id in by_id
        anth = by_id[anth_id]
        assert anth["transport"] == "anthropic_command"
        assert anth["command"] == ["anthropic-api"]
        assert "claude" not in anth["command"][0]
        assert anth["price"]["input_per_m"] > 0
        assert anth["price"].get("as_of")
        assert anth["price"].get("source")
    assert by_id["sonnet-5-5"]["weights_status"] == "proprietary"
    assert not by_id["sonnet-5-5"].get("weights_url")

    oc_free = [c for c in cfgs if c["transport"] == "opencode_cli" and c["price"]["input_per_m"] == 0]
    assert len(oc_free) == 10
    assert {c["model"]: c["weights_status"] for c in oc_free} == EXPECTED_OPENCODE_MODELS

    oc_paid = [c for c in cfgs if c["transport"] == "opencode_cli" and c["price"]["input_per_m"] > 0]
    assert len(oc_paid) == 5
    assert {c["model"] for c in oc_paid} == {
        "opencode/deepseek-v4.1-flash",
        "opencode/glm-5.3-flash",
        "opencode/qwen3.8-flash",
        "opencode/minimax-m3",
        "opencode/mistral-large-4",
    }
    for c in oc_paid:
        assert c["price"].get("as_of")
        assert c["price"].get("source")
    for c in [*oc_free, *oc_paid]:
        assert c["snapshot_date"] == "2026-11-23"
        assert c["opencode_version"] == "2.3.0"
        if c["weights_status"] == "open-weight":
            assert str(c.get("weights_url", "")).startswith("https://huggingface.co/")
        else:
            assert c["weights_status"] == "free, weights not public"
            assert not c.get("weights_url")
    # Ensure no Ollama decider sits in the Phase 1 roster
    assert all(c["transport"] != "ollama" for c in cfgs)

    # Ensure decider_spec snapshot fields are text-free TOKEN-safe
    with Gateway(tmp_path / "tmp_spec_check.sqlite", 0.0) as gw:
        d = load_decider(files[1], gw)
        spec = decider_spec(d)
        assert spec["snapshot_date"] == "2026-11-23"
        assert spec["opencode_version"] == "2.3.0"
        assert not is_free_text(".deciders[].snapshot_date", spec["snapshot_date"])
        assert not is_free_text(".deciders[].opencode_version", spec["opencode_version"])


# ---------------------------------------------------------------------------
# A3: chat_agent generator, balance, determinism, oracle, records_of, text-free
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def chat_dev_300():
    return generate_chat_cases(split="dev", seed=2026112301, n=300)


def test_a3_chat_track_registered():
    assert "chat_agent" in list_tracks()
    track = get_track("chat_agent")
    assert isinstance(track, ChatAgentTrack)
    assert track.meta.valid_actions == CHAT_ACTIONS
    assert track.meta.mechanisms == CHAT_MECHANISMS


def test_a3_balance_and_temporal_metadata(chat_dev_300):
    assert len(chat_dev_300) == 300
    mech_counts = Counter(c.mechanism for c in chat_dev_300)
    assert mech_counts == {"H0": 150, "H1": 30, "H2": 30, "H3": 30, "HU1": 30, "HU2": 30}

    for mech, expected_n in mech_counts.items():
        subset = [c for c in chat_dev_300 if c.mechanism == mech]
        slot_counts = Counter(c.metadata["slot"] for c in subset)
        assert set(slot_counts) == set(SLOTS)
        assert set(slot_counts.values()) == {expected_n // len(SLOTS)}

        dist_counts = Counter(c.metadata["distance"] for c in subset)
        assert dist_counts == {"near": expected_n // 3, "middle": expected_n // 3, "far": expected_n // 3}

        len_counts = Counter(c.metadata["length_bucket"] for c in subset)
        assert len_counts == {"short": expected_n // 3, "medium": expected_n // 3, "long": expected_n // 3}

    for c in chat_dev_300:
        assert c.track == "chat_agent"
        assert c.schema == "memory_bench.case/2"
        assert 50 <= len(c.events) <= 200
        assert c.metadata["slot_counts_balanced"] is True
        lag = c.metadata["override_lag_events"]
        dist = c.metadata["distance"]
        if dist == "near":
            assert lag <= 10
        elif dist == "middle":
            assert 11 <= lag <= 30
        else:
            assert lag > 30


def test_a3_determinism(chat_dev_300):
    again = generate_chat_cases(split="dev", seed=2026112301, n=300)
    assert [dumps_case(c) for c in chat_dev_300] == [dumps_case(c) for c in again]


def test_a3_oracle_agreement_and_counterfactual_d_star(chat_dev_300):
    track = get_track("chat_agent")
    for c in chat_dev_300:
        # Full history agrees 100% with a_star
        assert track.oracle(c) == c.a_star
        all_ids = {e.id for e in c.events}
        if c.mechanism in ("H1", "H2", "H3"):
            assert len(c.d_star) >= 1
            without_dstar = all_ids - set(c.d_star)
            assert track.oracle(c, visible_ids=without_dstar) != c.a_star
        if c.mechanism == "H1":
            assert len(c.d_star) == 2
            # Dropping the later override reverts to routine execution
            assert track.oracle(c, visible_ids=all_ids - {c.d_star[1]}) == EXECUTE_OR_ANSWER
        if c.mechanism == "HU2":
            assert len(c.d_star) == 2
            # Both retrieved -> REQUEST_INFO; partial (1 of 2) -> EXECUTE_OR_ANSWER (Siren Effect); none -> REQUEST_INFO
            assert track.oracle(c, visible_ids=set(c.d_star)) == REQUEST_INFO
            assert track.oracle(c, visible_ids={c.d_star[0]}) == EXECUTE_OR_ANSWER
            assert track.oracle(c, visible_ids={c.d_star[1]}) == EXECUTE_OR_ANSWER
            assert track.oracle(c, visible_ids=all_ids - set(c.d_star)) == REQUEST_INFO


def test_a3_records_of_round_trip(chat_dev_300):
    for c in chat_dev_300:
        recs = records_of(c)
        assert len(recs) == len(c.events)
        prev_date = ""
        for ev, rec in zip(c.events, recs):
            assert rec.id == ev.id
            assert rec.date == ev.date
            dt.date.fromisoformat(rec.date)
            assert rec.date >= prev_date
            prev_date = rec.date
            assert not rec.text.startswith("[")
            p0, p1, _ = rec.text.split(" ", 2)
            assert p0 == ev.id and p1 == ev.date


def test_a3_prompts_parse_and_chow_roi_perfect_oracle_zero_risk(chat_dev_300):
    track = get_track("chat_agent")
    c = chat_dev_300[0]
    recs = records_of(c)
    inp = DecisionInput(
        case_id=c.case_id,
        query_text=c.query_text,
        records_text="\n".join(r.text for r in recs[:5]),
        record_ids=[r.id for r in recs[:5]],
        track="chat_agent",
    )
    msgs = track.build_messages(inp, "v1")
    assert len(msgs) == 2 and msgs[0]["role"] == "system"
    schema = track.action_schema("v1")
    assert schema["properties"]["action"]["enum"] == list(CHAT_ACTIONS)
    assert track.parse_reply('```json\n{"action": "REFUSE_OR_GUARD", "reason": "prohibited"}\n```') == {
        "action": "REFUSE_OR_GUARD",
        "reason": "prohibited",
    }
    assert track.parse_reply('{"action": "EXPLAIN", "reason": "wrong track"}') is None

    # Perfect oracle on HU1/HU2 (where a_star == REQUEST_INFO) has Chow L(lambda) == 0.0
    rows = [
        {"case_id": "u1", "decider": "d", "kind": "gate", "system": "full", "config_label": "default",
         "budget": None, "repeat": 0, "action": "REQUEST_INFO", "a_star": "REQUEST_INFO", "mechanism": "HU2"},
        {"case_id": "u1", "decider": "d", "kind": "floor", "system": "full", "config_label": "default",
         "budget": None, "repeat": 1, "action": "REQUEST_INFO", "a_star": "REQUEST_INFO", "mechanism": "HU2"},
        {"case_id": "u1", "decider": "d", "kind": "view", "system": "empty", "config_label": "default",
         "budget": 1000, "repeat": 1, "action": "REQUEST_INFO", "a_star": "REQUEST_INFO", "mechanism": "HU2"},
        {"case_id": "u1", "decider": "d", "kind": "view", "system": "dense_raw", "config_label": "default",
         "budget": 1000, "repeat": 1, "action": "REQUEST_INFO", "a_star": "REQUEST_INFO", "mechanism": "HU2"},
    ]
    ch = chow_summary(rows, seed=11, reps=20, exploratory=True)
    assert ch["deciders"]["d"]["cells"]["dense_raw/default@1000"]["by_lambda"]["5.0"]["risk"]["value"] == 0.0
    roi = compute_roi(rows, seed=11, reps=20, exploratory=True)
    assert roi["deciders"]["d"]["cells"]["dense_raw/default@1000"]["risk_reduction_pts"] == 0.0


# ---------------------------------------------------------------------------
# A4: End-to-end $0 chat pipeline (metrics, summary, acbi, sealed view-audit, verify)
# ---------------------------------------------------------------------------

def _fake_embed_opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    body = json.loads(data.decode("utf-8"))
    texts = body.get("input", [])
    if isinstance(texts, str):
        texts = [texts]
    vocab = ("report_format", "travel_policy", "invoice_export", "meeting_window", "dataset_export",
             "purchase_approval", "override", "standing", "prohibit", "boundary", "expires", "renew")
    items = []
    for idx, txt in enumerate(texts):
        low = str(txt).lower()
        vec = [float(low.count(tok)) + 0.01 for tok in vocab]
        h = int(hashlib.sha256(low.encode("utf-8")).hexdigest()[:6], 16) / float(16**6)
        vec.append(h)
        items.append({"object": "embedding", "index": idx, "embedding": vec})
    payload = json.dumps({
        "object": "list",
        "data": items,
        "model": body.get("model", "nomic-embed-text"),
        "usage": {"prompt_tokens": sum(max(1, len(str(t)) // 4) for t in texts), "total_tokens": 10},
    }).encode("utf-8")
    return 200, payload


def test_a4_zero_dollar_chat_pipeline_passes_verify(tmp_path, chat_dev_300):
    sample = validity_sample(chat_dev_300, n_per_mechanism=2)  # 12 stratified cases (2 per H0..HU2)
    cases_file = tmp_path / "chat_dev_sample.jsonl"
    write_jsonl(cases_file, sample)

    # Validity gate passes with MockOracleDecider
    vres = run_validity(MockOracleDecider(sample), sample, tmp_path / "val")
    assert vres["gate"]["passes"] is True
    assert list(vres["accuracy"]["per_mechanism"]) == list(CHAT_MECHANISMS)

    ms_cfg = {
        "id": "offline-mock-embed",
        "chat": {
            "base_url": "http://127.0.0.1:11535/v1",
            "model": "mock-chat",
            "price": {"input_per_m": 0, "output_per_m": 0, "as_of": "2026-11-23", "source": "offline mock"},
            "assumed_max_output_tokens": 2048,
        },
        "embeddings": {
            "base_url": "http://127.0.0.1:11535/v1",
            "model": "nomic-embed-text",
            "dims": 13,
            "price": {"input_per_m": 0, "output_per_m": 0, "as_of": "2026-11-23", "source": "offline mock"},
        },
        "timeout_s": 30,
        "retries": 1,
    }
    run_dir = tmp_path / "dryrun-chat"
    decider = MockNoisyDecider(sample, flip_rate=0.05, seed=11)
    manifest = run(RunConfig(
        out_dir=run_dir,
        cases=sample,
        decider=decider,
        adapters=["dense_raw"],
        budgets=[1000],
        repeats=2,
        boot_seed=11,
        boot_reps=50,
        memory_side=ms_cfg,
        proxy_cache=tmp_path / "cache.sqlite",
        proxy_options={"opener": _fake_embed_opener},
    ))
    assert manifest["state"] == "complete"
    assert manifest["track"] == "chat_agent"
    assert VIEW_AUDIT in manifest["files"]
    va = F.read_json(run_dir / VIEW_AUDIT)
    assert va["views_with_failed_call"] == 0
    assert va["views_checked"] == len(sample)

    # Compute standalone text-free acbi.json and temporal stats
    rows = F.read_metrics(run_dir / F.METRICS)
    views = [json.loads(line) for line in (run_dir / "private" / "views.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    acbi = compute_acbi(sample, rows, views, seed=11, reps=50, exploratory=True)
    F.write_json(run_dir / F.ACBI, acbi)
    temp = compute_temporal(sample, rows, seed=11, reps=50, exploratory=True)
    assert temp["deciders"][decider.id]["n_gated"] > 0

    # Text-free scan passes on the run folder (including acbi.json and view-audit.json)
    assert scan_run(run_dir) == []

    # Pack exploratory submission and verify all checks pass!
    packed = pack(run_dir, tmp_path / "pack", exploratory=True)
    assert (packed / F.ACBI).is_file()
    assert (packed / VIEW_AUDIT).is_file()
    vres_pack = verify(packed, cases=cases_file)
    assert vres_pack.ok, [f.line() for f in vres_pack.failures]


# ---------------------------------------------------------------------------
# A1: protocol/v2.json integrity (asserted whenever protocol/v2.json is present)
# ---------------------------------------------------------------------------

def test_a1_protocol_v2_frozen_and_v1_untouched():
    v1_path = REPO_ROOT / "protocol" / "v1.json"
    assert v1_path.is_file()
    v1 = load_protocol(v1_path)
    assert v1["protocol_sha256"].startswith("99494597")

    v2_path = REPO_ROOT / "protocol" / "v2.json"
    if not v2_path.is_file():
        pytest.skip("protocol/v2.json not frozen yet")
    mismatches = check_protocol(v2_path)
    assert mismatches == [], [m.line() for m in mismatches]
    v2 = load_protocol(v2_path)
    s1 = v2["steps"]["1"]["values"]
    assert s1["min_context_tokens"] == 65536
    assert s1["max_output_tokens_mode_a"] == 2048
    assert s1["chow_lambdas"] == [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0]
    assert s1["separation"]["print_order"] is False
    assert s1["kappa_plan"]["labeller_2"] == "PENDING_VARMA_ASSIGNMENT"
    assert s1["kappa_plan"]["sample_n"] == 60
    assert s1["kappa_plan"]["kappa_min"] == 0.70
    assert "chat_agent" in s1["holm_families"]
    assert len(s1["phase1_deciders"]) == 11
    assert s1["local_ollama_excluded"] is True
    assert "2" in v2["steps"]
    assert "data/v2/chat_agent/table_cases.jsonl" in v2["steps"]["2"]["files"]


def test_a4_committed_dryrun_passes_pack_and_verify(tmp_path):
    dry = REPO_ROOT / "results" / "v2" / "dryrun-chat-dev"
    cases = REPO_ROOT / "data" / "v2" / "chat_agent" / "dev_dryrun60.jsonl"
    if not dry.is_dir():
        pytest.skip("private results/v2/dryrun-chat-dev omitted from public export")
    assert dry.is_dir() and cases.is_file()
    assert scan_run(dry) == []
    packed = pack(dry, tmp_path / "pack", exploratory=True)
    vres = verify(packed, cases=cases)
    assert vres.ok, [f.line() for f in vres.failures]


def test_b1_chat_html_case_anatomy_and_exploratory_instruments(tmp_path):
    import re
    from memory_bench.site.build import build as build_site

    out = tmp_path / "site"
    build_site(REPO_ROOT, out)
    html = (out / "chat.html").read_text(encoding="utf-8")
    assert "Case anatomy" in html
    assert "Registered exploratory instruments" in html
    assert "exploratory" in html
    assert "USE_UPDATED_PREF" in html
    assert "Asymmetric Conflict Hazard" in html
    assert "Decision cost by risk ratio" in html
    assert "Registered Phase 1 decider roster" in html
    assert "served build not verified" in html
    assert "mock-noisy" not in html
    assert "ctab2026112302" not in html
    assert not re.search(r"style\s*=", html, re.I)
    for bad in (r"\bcommit\b", r"wrong_commit", r"\babstention\b", r"Rule\s+L"):
        assert not re.search(bad, html, re.I), f"forbidden vocabulary {bad} in chat.html"
