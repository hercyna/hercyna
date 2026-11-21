"""bench health rebuild and bench audit views (Day 14, review B3)."""

import json
import logging

import pytest

from memory_bench.cli import main
from memory_bench.engine import files as F
from memory_bench.engine.audit import VIEW_AUDIT, audit_views
from memory_bench.engine.health import UNKNOWN_LOGGER, rebuild_health
from memory_bench.submit.textfree import RELEASED_FILES, scan_file, scan_run

SECRET = "Customer asked to close the account after the March bill"


def _jsonl(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _t(case, call, seconds, at, warnings=None, by=None, system="prod", label="c25"):
    row = {"at": at, "budget": None, "call": call, "case_id": case, "config_label": label, "seconds": seconds,
           "system": system}
    if warnings is not None:
        row["warnings"] = warnings
    if by is not None:
        row["warnings_by_logger"] = by
    return row


def _metrics(cases, repeats=1):
    rows = []
    for c in cases:
        rows.append({"case_id": c, "decider": "d", "kind": "gate", "system": "full", "config_label": "default",
                     "budget": None, "repeat": 0})
        for r in range(1, repeats + 1):
            rows.append({"case_id": c, "decider": "d", "kind": "floor", "system": "full", "config_label": "default",
                         "budget": None, "repeat": r})
            rows.append({"case_id": c, "decider": "d", "kind": "view", "system": "prod", "config_label": "c25",
                         "budget": 1000, "repeat": r})
    return [{**r, "track": "t", "mechanism": "M0", "a_star": "EXPLAIN", "action": "EXPLAIN", "view_tokens": 1,
             "view_units": 1, "view_sha256": "0" * 64} for r in rows]


@pytest.fixture
def two_attempts(tmp_path):
    """A resumed product run. Attempt 1 (old rows: a warning total only) ingested c1 and c2 and stopped; c2's view
    was not kept. Attempt 2 (rows split by logger) ingested c2 and c3. The engine's health.json covers attempt 2."""
    d = tmp_path / "run"
    d.mkdir()
    timings = [
        _t("c1", "reset", 1.0, "2026-11-20T10:00:00+00:00", 0), _t("c1", "ingest", 10.0, "2026-11-20T10:00:10+00:00", 3),
        _t("c1", "retrieve", 0.5, "2026-11-20T10:00:11+00:00", 0),
        _t("c2", "reset", 1.0, "2026-11-20T10:00:00+00:00", 0), _t("c2", "ingest", 20.0, "2026-11-20T10:00:20+00:00", 4),
        _t("c2", "retrieve", 0.5, "2026-11-20T10:00:21+00:00", 0),
        *[_t(c, "ingest", 0.0, "2026-11-20T10:00:00+00:00", system="full", label="default") for c in ("c1", "c2")],
        _t("c2", "reset", 1.0, "2026-11-20T11:00:00+00:00", 0),
        _t("c2", "ingest", 30.0, "2026-11-20T11:00:30+00:00", 2, {"lib.extract": 2}),
        _t("c2", "retrieve", 1.5, "2026-11-20T11:00:32+00:00", 0),
        _t("c3", "reset", 1.0, "2026-11-20T11:00:00+00:00", 0),
        _t("c3", "ingest", 40.0, "2026-11-20T11:00:40+00:00", 1, {"lib.store": 1}),
        _t("c3", "retrieve", 2.0, "2026-11-20T11:00:42+00:00", 0),
    ]
    _jsonl(d / F.TIMINGS, timings)
    _jsonl(d / F.METRICS, _metrics(["c1", "c2", "c3"]))
    views = [{"case_id": c, "system": "prod", "config_label": "c25", "text": SECRET, "units": [SECRET],
              "record_ids": [], "usage": {}, "ingest_seconds": ing, "retrieve_seconds": ret, "at": at,
              "warnings": {"total": sum(by.values()), "by_logger": by}}
             for c, ing, ret, at, by in (("c1", 10.0, 0.5, "2026-11-20T10:00:11+00:00", {"lib.extract": 3}),
                                         ("c2", 30.0, 1.5, "2026-11-20T11:00:32+00:00", {"lib.extract": 2}),
                                         ("c3", 40.0, 2.0, "2026-11-20T11:00:42+00:00", {"lib.store": 1}))]
    _jsonl(d / "private" / "views.jsonl", views)
    old = {"run_id": "run", "updated": "x", "adapters": [
        {"name": "full", "config_label": "default", "product_version": "builtin", "ok": True, "cases_done": 3,
         "errors": 0, "last_error": None, "ingest_seconds_mean": 0.0, "retrieve_seconds_mean": None,
         "last_ok_at": None, "product_warnings": {"total": 0, "by_logger": {}}},
        {"name": "prod", "config_label": "c25", "product_version": "1.0", "ok": False, "cases_done": 3, "errors": 1,
         "last_error": {"case_id": "c9", "at": "2026-11-20T11:00:00+00:00", "error": "RuntimeError: boom"},
         "ingest_seconds_mean": 35.0, "retrieve_seconds_mean": 1.75, "last_ok_at": "2026-11-20T11:00:42+00:00",
         "product_warnings": {"total": 3, "by_logger": {"lib.extract": 2, "lib.store": 1}}}]}
    F.write_json(d / F.HEALTH, old)
    man = {"run_id": "run", "state": "complete", "budgets": [1000], "repeats": 1,
           "adapters": [{"name": "full", "config_label": "default", "product_version": "builtin"},
                        {"name": "prod", "config_label": "c25", "product_version": "1.0"}],
           "files": {F.HEALTH: F.sha256_file(d / F.HEALTH), F.TIMINGS: F.sha256_file(d / F.TIMINGS)}}
    F.write_json(d / F.MANIFEST, man)
    return d


def test_health_rebuild_spans_every_attempt(two_attempts):
    d = two_attempts
    rep = rebuild_health(d)
    prod = next(a for a in rep["adapters"] if a["name"] == "prod")
    # all five ingest rows of both attempts, not the last attempt's two
    assert prod["ingest_seconds_mean"] == round((10 + 20 + 30 + 40) / 4, 6)
    assert prod["retrieve_seconds_mean"] == round((0.5 + 0.5 + 1.5 + 2.0) / 4, 6)
    # total from every row: 3 + 4 (attempt 1) + 2 + 1 (attempt 2)
    assert prod["product_warnings"]["total"] == 10
    # attempt-2 rows carry their split; attempt-1 rows are split by the stored views of their cases (c1: 3; c2's
    # stored view is from attempt 2, already counted, so attempt 1's 4 warnings on c2 are not explained)
    assert prod["product_warnings"]["by_logger"] == {"lib.extract": 2 + 3, "lib.store": 1, UNKNOWN_LOGGER: 4}
    assert prod["cases_done"] == 3 and prod["errors"] == 1 and prod["last_error"]["case_id"] == "c9"
    assert prod["last_ok_at"] == "2026-11-20T11:00:42+00:00"
    assert prod["attempts"] == {"items": 4, "stored_views": 3}
    full = next(a for a in rep["adapters"] if a["name"] == "full")
    assert full["cases_done"] == 3 and "attempts" not in full
    assert rep["rebuilt"]["timing_rows"] == 14
    # the file and its manifest hash
    assert json.loads((d / F.HEALTH).read_text()) == rep
    man = json.loads((d / F.MANIFEST).read_text())
    assert man["files"][F.HEALTH] == F.sha256_file(d / F.HEALTH)
    assert scan_file(d / F.HEALTH) == [] or all(v.path.endswith("last_error.error") for v in scan_file(d / F.HEALTH))
    assert SECRET not in (d / F.HEALTH).read_text()


def test_health_rebuild_keeps_format(two_attempts):
    rep = rebuild_health(two_attempts)
    keys = {"name", "config_label", "product_version", "ok", "cases_done", "errors", "last_error",
            "ingest_seconds_mean", "retrieve_seconds_mean", "last_ok_at", "product_warnings"}
    for a in rep["adapters"]:
        assert keys <= set(a) and set(a) - keys <= {"attempts"}
        assert set(a["product_warnings"]) == {"total", "by_logger"}


def test_health_rebuild_cli(two_attempts, capsys):
    assert main(["health", "rebuild", str(two_attempts)]) == 0
    out = capsys.readouterr().out
    assert "prod/c25" in out and "warnings 10" in out and SECRET not in out
    assert main(["health", "rebuild", str(two_attempts / "nope")]) == 2


def test_timing_rows_carry_the_split():
    from memory_bench.engine.run import _Health

    class Product:
        name, config_label, product_version, needs_proxy = "p", "c", "1", True

    h = _Health([Product()])
    h.record_call(Product(), "c1", None, "ingest", 1.0, {"b.log": 1, "a.log": 2})
    h.record_call(Product(), "c1", None, "retrieve", 1.0, {})
    assert h.timings[0]["warnings"] == 3 and h.timings[0]["warnings_by_logger"] == {"a.log": 2, "b.log": 1}
    assert h.timings[1]["warnings"] == 0 and "warnings_by_logger" not in h.timings[1]
    logging.getLogger("x")  # no handler left behind by record_call (it adds none)


# ---------------------------------------------------------------- view audit

def _call(case, at, status, adapter="prod", label="c25"):
    return {"adapter": adapter, "at": at, "cached": False, "case_id": case, "config_label": label, "estimated": False,
            "input_tokens": 1, "model": "m", "output_tokens": 1, "phase": "ingest", "reply_sha256": "0" * 64,
            "request_sha256": "0" * 64, "requested_model": "m", "retries": 0, "route": "chat", "seconds": 0.1,
            "status": status, "upstream": "u", "usd": 0.0}


def test_audit_flags_a_view_built_over_a_failed_call(two_attempts):
    d = two_attempts
    calls = [
        _call("c1", "2026-11-20T10:00:05+00:00", 200),
        _call("c1", "2026-11-20T10:00:06+00:00", 402),       # inside c1's window [09:59:55.5, 10:00:11]
        _call("c1", "2026-11-20T09:59:58+00:00", 500),       # inside: within the 5 s margin
        _call("c2", "2026-11-20T10:00:15+00:00", 402),       # an earlier attempt: before c2's window (11:00:00.5)
        _call("c3", "2026-11-20T11:00:43+00:00", None),      # after c3's view: outside
        _call("c4", "2026-11-20T11:00:43+00:00", 402),       # a case with no stored view
    ]
    _jsonl(d / "memory_calls.jsonl", calls)
    res = audit_views(d)
    assert res["views_checked"] == 3 and res["views_with_failed_call"] == 1
    s = res["systems"]["prod/c25"]
    assert s["case_ids"] == ["c1"] and s["failed_calls_in_window"] == 2
    assert res["failed_calls"] == 5 and res["failed_calls_by_status"] == {"402": 3, "500": 1, "none": 1}
    assert res["cases_failed_without_view"] == 1 and s["cases_failed_without_view"] == ["c4"]
    text = (d / VIEW_AUDIT).read_text()
    assert SECRET not in text and json.loads(text) == res
    assert scan_file(d / VIEW_AUDIT) == []
    assert VIEW_AUDIT in RELEASED_FILES
    assert not any(v.file == VIEW_AUDIT for v in scan_run(d))


def test_audit_margin_is_a_parameter(two_attempts):
    _jsonl(two_attempts / "memory_calls.jsonl", [_call("c1", "2026-11-20T09:59:58+00:00", 500)])
    assert audit_views(two_attempts, write=False)["views_with_failed_call"] == 1
    assert audit_views(two_attempts, margin=0.0, write=False)["views_with_failed_call"] == 0


def test_audit_cli_and_refusals(two_attempts, tmp_path, capsys):
    _jsonl(two_attempts / "memory_calls.jsonl", [_call("c1", "2026-11-20T10:00:06+00:00", 402)])
    assert main(["audit", "views", str(two_attempts)]) == 0
    out = capsys.readouterr().out
    assert "views checked 3" in out and "with a failed call inside the window 1" in out and SECRET not in out
    builtin = tmp_path / "b"
    builtin.mkdir()
    assert main(["audit", "views", str(builtin)]) == 2
    assert "no private/views.jsonl" in capsys.readouterr().err
