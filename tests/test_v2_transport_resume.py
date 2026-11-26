"""v2 transport tests: binary pin, 429 resume, extractor probe and the seed draw."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from memory_bench.deciders.model import load_decider
from memory_bench.deciders.transports import ContextOverflowError
from memory_bench.engine.extractor_probe import PROBE_JSON, run_extractor_probe, sample_probe_cases
from memory_bench.engine.run import RunError, check_opencode_binary_version
from memory_bench.engine.seeds import draw_seed
from memory_bench.engine.validity import run_validity, validity_sample
from memory_bench.gateway import Gateway
from memory_bench.tracks.chat_agent import generate_chat_cases


@pytest.fixture(scope="module")
def chat_dev_60():
    return generate_chat_cases(split="dev", seed=2026112301, n=60)


def test_a6_draw_seed_logs_jsonl(tmp_path):
    log = tmp_path / "seed_draws.jsonl"
    s1 = draw_seed("test_probe_1", log_path=log, extra={"k": 1})
    s2 = draw_seed("test_probe_2", log_path=log)
    assert isinstance(s1, int) and s1 >= 1
    assert isinstance(s2, int) and s2 >= 1
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2
    assert rows[0]["purpose"] == "test_probe_1" and rows[0]["seed"] == s1
    assert rows[0]["source"] == "secrets.randbelow"
    assert rows[1]["purpose"] == "test_probe_2" and rows[1]["seed"] == s2


def test_a2_opencode_binary_version_pin_accepts_230_and_aborts_on_241(tmp_path):
    cfg = {
        "id": "oc-test",
        "transport": "opencode_cli",
        "command": ["opencode"],
        "model": "opencode/beam-free",
        "opencode_version": "2.3.0",
        "price": {"input_per_m": 0, "output_per_m": 0},
    }
    with Gateway(tmp_path / "c.sqlite", 0.0) as gw:
        dec = load_decider(cfg, gw)

    ok_proc = mock.Mock(returncode=0, stdout="opencode v2.3.0\n", stderr="")
    with mock.patch("memory_bench.engine.run.subprocess.run", return_value=ok_proc):
        assert check_opencode_binary_version(dec) == "2.3.0"

    bad_proc = mock.Mock(returncode=0, stdout="opencode v2.4.1\n", stderr="")
    with mock.patch("memory_bench.engine.run.subprocess.run", return_value=bad_proc):
        with pytest.raises(RunError, match="2.3.0.*2.4.1"):
            check_opencode_binary_version(dec)


def test_a2_validity_429_wait_resume_and_6h_limit(tmp_path, chat_dev_60):
    sample = validity_sample(chat_dev_60, n_per_mechanism=1)  # 6 cases
    cfg = {
        "id": "oc-retry-test",
        "transport": "opencode_cli",
        "command": ["opencode"],
        "model": "opencode/beam-free",
        "opencode_version": "2.3.0",
        "retries": 0,
        "max_retry_sleep_s": 1.0,
        "price": {"input_per_m": 0, "output_per_m": 0},
    }
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky_runner(argv, stdin, timeout, env=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # First call hits 429 with 5s retryAfterMs
            err = {"type": "error", "error": {"type": "provider.free-limit", "status": 429, "retryAfterMs": 5000}}
            return 1, json.dumps(err), ""
        # Subsequent calls succeed with valid JSON matching the requested action
        ndjson = "\n".join([
            json.dumps({"type": "text", "part": {"text": '{"action":"EXECUTE_OR_ANSWER","reason":"ok"}'}}),
            json.dumps({"type": "step_finish", "part": {"tokens": {"input": 100, "output": 10}}}),
        ])
        return 0, ndjson, ""

    ok_proc = mock.Mock(returncode=0, stdout="opencode v2.3.0\n", stderr="")
    with mock.patch("memory_bench.engine.run.subprocess.run", return_value=ok_proc):
        with Gateway(tmp_path / "c.sqlite", 0.0) as gw:
            dec = load_decider(cfg, gw, runner=flaky_runner, sleep=lambda s: None)
            res = run_validity(dec, sample, tmp_path / "v1", gateway=gw, sleep=sleeps.append)

    assert res["complete"] is True
    assert res["rate_limit_429_count"] == 1
    assert res["opencode_binary_version"] == "2.3.0"
    assert len(sleeps) == 1 and sleeps[0] >= 5.0
    assert (tmp_path / "v1" / "spend.json").is_file()

    # Now test 6h cooldown ceiling stops with "gate not completed (rate limit)"
    def always_429(argv, stdin, timeout, env=None):
        err = {"type": "error", "error": {"type": "provider.free-limit", "status": 429, "retryAfterMs": 15000000}}
        return 1, json.dumps(err), ""

    sleeps2: list[float] = []
    with mock.patch("memory_bench.engine.run.subprocess.run", return_value=ok_proc):
        with Gateway(tmp_path / "c2.sqlite", 0.0) as gw2:
            dec2 = load_decider(cfg, gw2, runner=always_429, sleep=lambda s: None)
            res2 = run_validity(dec2, sample, tmp_path / "v2", gateway=gw2, max_cooldown_s=21600.0, sleep=sleeps2.append)
    assert res2["complete"] is False
    assert res2["gate"]["passes"] is False
    assert "gate not completed (rate limit)" in (res2["stop_reason"] or "")


def test_a4_extractor_probe_zero_drop_and_seed_log(tmp_path):
    cases_300 = generate_chat_cases(split="dev", seed=2026112301, n=300)
    picked = sample_probe_cases(cases_300, seed=12345, n_per_mech=5)
    assert len(picked) == 20
    assert sorted({c.mechanism for c in picked}) == ["H1", "H2", "H3", "HU2"]

    cfg = {
        "id": "oc-probe-test",
        "transport": "opencode_cli",
        "command": ["opencode"],
        "model": "opencode/beam-free",
        "opencode_version": "2.3.0",
        "price": {"input_per_m": 0, "output_per_m": 0},
    }

    def perfect_extractor_runner(argv, stdin, timeout, env=None):
        prompt = argv[-1]
        # Extract every turn_NNN line whose type is preference/permission/boundary/instruction
        items = []
        for line in prompt.splitlines():
            parts = line.split(" ", 4)
            if len(parts) >= 4 and parts[0].startswith("turn_") and parts[2] in (
                "preference", "permission", "boundary", "instruction"
            ):
                items.append({"turn_id": parts[0], "slot": "auto", "kind": parts[2], "summary": parts[4] if len(parts) > 4 else ""})
        ndjson = "\n".join([
            json.dumps({"type": "text", "part": {"text": json.dumps({"constraints": items})}}),
            json.dumps({"type": "step_finish", "part": {"tokens": {"input": 500, "output": 100}}}),
        ])
        return 0, ndjson, ""

    ok_proc = mock.Mock(returncode=0, stdout="opencode v2.3.0\n", stderr="")
    seed_log = tmp_path / "seeds.jsonl"
    with mock.patch("memory_bench.engine.run.subprocess.run", return_value=ok_proc):
        with Gateway(tmp_path / "pc.sqlite", 0.0) as gw:
            dec = load_decider(cfg, gw, runner=perfect_extractor_runner, sleep=lambda s: None)
            res = run_extractor_probe(dec, cases_300, tmp_path / "probe", gateway=gw, seed_log=seed_log)

    assert res["complete"] is True
    assert res["n_cases_evaluated"] == 20
    assert res["dropped_constraints"] == 0
    assert res["passes_zero_drop"] is True
    assert res["json_parse_rate"] == 1.0
    assert (tmp_path / "probe" / PROBE_JSON).is_file()
    assert (tmp_path / "probe" / "spend.json").is_file()
    assert len(seed_log.read_text(encoding="utf-8").splitlines()) == 1


def test_b3_opencode_zen_stdin_runner_passes_prompt_on_stdin_and_denies_tools(tmp_path):
    from memory_bench.engine.opencode_runner import opencode_zen_stdin_runner

    cfg_home = tmp_path / "cfg"
    (cfg_home / "opencode").mkdir(parents=True)
    env = {"XDG_CONFIG_HOME": str(cfg_home), "OPENCODE_CONFIG_DIR": str(cfg_home / "opencode"), "OPENCODE_DB": ":memory:"}
    captured = {}

    def fake_run(cmd, *, input=None, capture_output=True, text=True, timeout=60, env=None, cwd=None):
        captured["cmd"] = list(cmd)
        captured["input"] = input
        captured["env"] = dict(env or {})
        captured["cwd"] = cwd
        captured["cfg"] = json.loads((cfg_home / "opencode" / "opencode.jsonc").read_text(encoding="utf-8"))
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("memory_bench.engine.opencode_runner.subprocess.run", side_effect=fake_run):
        rc, _, _ = opencode_zen_stdin_runner(
            ["opencode", "run", "--standalone", "--format", "json", "--model", "opencode/beam-free", "BIG PROMPT"],
            None,
            30.0,
            env=env,
        )
    assert rc == 0
    assert "BIG PROMPT" not in captured["cmd"]
    assert captured["input"] == "BIG PROMPT"
    assert "OPENCODE_CONFIG_DIR" not in captured["env"]
    assert captured["cfg"]["permission"]["bash"] == "deny"
    assert captured["cfg"]["permission"]["webfetch"] == "deny"
    assert Path(captured["cwd"]).name == "work"
