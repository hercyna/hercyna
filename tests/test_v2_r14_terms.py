"""Round 14 tests for first-run CLI terms acceptance (`bench terms`, prompt, flag, env var, version bump)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from memory_bench import terms as T

REPO = Path(__file__).resolve().parents[1]


def _run_bench(*args: str, env_override: dict[str, str] | None = None, stdin_text: str | None = None):
    env = {k: v for k, v in os.environ.items() if k != "HERCYNA_ACCEPT_TERMS"}
    env["PYTHONPATH"] = str(REPO / "src")
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "memory_bench.cli", *args],
        cwd=str(REPO),
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
    )


def test_r14_bench_terms_and_version_run_without_prior_acceptance(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    r_ver = _run_bench("terms", "--version", env_override=env)
    assert r_ver.returncode == 0, r_ver.stderr
    assert r_ver.stdout.strip() == T.current_version(REPO)

    r_full = _run_bench("terms", env_override=env)
    assert r_full.returncode == 0, r_full.stderr
    for num in ("1.", "2.", "3.", "4.", "5.", "6."):
        assert num in r_full.stdout
    assert "Apache" in r_full.stdout and "CC BY 4.0" in r_full.stdout


def test_r14_non_interactive_blocks_without_acceptance_and_never_hangs(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    r = _run_bench("freeze", "--check", "--protocol", "protocol/v2.json", env_override={"XDG_CONFIG_HOME": str(cfg)})
    assert r.returncode == 2
    assert "Type 'accept' to continue" in r.stderr or "Non-interactive run blocked" in r.stderr
    assert "--accept-terms" in r.stderr
    assert "HERCYNA_ACCEPT_TERMS=1" in r.stderr
    assert not (cfg / "hercyna" / "accepted.json").exists()


def test_r14_flag_and_env_var_bypass_prompt_for_automation(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    r_flag = _run_bench(
        "freeze",
        "--check",
        "--protocol",
        "protocol/v2.json",
        "--accept-terms",
        env_override={"XDG_CONFIG_HOME": str(cfg)},
    )
    assert r_flag.returncode == 0, r_flag.stderr
    assert "all hashes match" in r_flag.stdout

    r_flag_before = _run_bench(
        "--accept-terms",
        "freeze",
        "--check",
        "--protocol",
        "protocol/v2.json",
        env_override={"XDG_CONFIG_HOME": str(cfg)},
    )
    assert r_flag_before.returncode == 0, r_flag_before.stderr

    r_env = _run_bench(
        "freeze",
        "--check",
        "--protocol",
        "protocol/v2.json",
        env_override={"XDG_CONFIG_HOME": str(cfg), "HERCYNA_ACCEPT_TERMS": "1"},
    )
    assert r_env.returncode == 0, r_env.stderr
    assert not (cfg / "hercyna" / "accepted.json").exists()


def test_r14_interactive_prompt_accept_reject_version_bump_and_zero_pii(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.delenv("HERCYNA_ACCEPT_TERMS", raising=False)

    # Interactive non-accept refuses without writing file
    class FakeStdin:
        def __init__(self, txt: str) -> None:
            self._txt = txt

        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return self._txt

    import argparse

    monkeypatch.setattr(sys, "stdin", FakeStdin("no\n"))
    assert T.ensure_terms_accepted(argparse.Namespace(accept_terms=False)) is False
    assert not T.accepted_path().exists()

    # Interactive exact 'accept' records version + date with zero PII
    monkeypatch.setattr(sys, "stdin", FakeStdin("accept\n"))
    assert T.ensure_terms_accepted(argparse.Namespace(accept_terms=False)) is True
    rec = json.loads(T.accepted_path().read_text(encoding="utf-8"))
    assert set(rec) == {"terms_version", "accepted_date"}
    assert rec["terms_version"] == T.current_version(REPO)
    for pii_key in ("name", "email", "user", "hostname", "ip"):
        assert pii_key not in json.dumps(rec).lower()

    # Subsequent call succeeds without prompt when same version accepted
    monkeypatch.setattr(sys, "stdin", FakeStdin("invalid\n"))
    assert T.is_accepted() is True
    assert T.ensure_terms_accepted(argparse.Namespace(accept_terms=False)) is True

    # Version bump asks again
    T.accepted_path().write_text(json.dumps({"terms_version": "1999-01-01-old", "accepted_date": "1999-01-01"}))
    assert T.is_accepted() is False
    monkeypatch.setattr(sys, "stdin", FakeStdin("decline\n"))
    assert T.ensure_terms_accepted(argparse.Namespace(accept_terms=False)) is False
