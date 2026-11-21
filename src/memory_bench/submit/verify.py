"""`bench verify DIR` (docs/submission.md section 3): seven checks on a submission folder, in order.

`verify(dir, protocol=None, cases=None)` is pure: it reads files and returns a `VerifyResult`; it prints nothing
and writes nothing. Every failure names its check and a file and carries hashes, counts and keys only. A value
read from the folder is shown only when it is a token (`submit.textfree.TOKEN`); anything else is shown as its
sha256, so a hostile file cannot make the verifier print its text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine import files as F
from ..engine.freeze import DEFAULT_PROTOCOL, TABLE_CASES, check_protocol, load_protocol, system_id
from ..scoring.metrics import ROW_KEY
from .pack import ALLOWED, REQUIRED, SUBMISSION, SUBMISSION_SCHEMA
from .textfree import TOKEN, scan_run

EXIT_FAIL = 5
DEFAULT_RUNS = "results/runs"
CHECKS = {1: "files", 2: "text-free", 3: "rows", 4: "labels", 5: "summary", 6: "protocol", 7: "submission"}
# Run files whose bytes the manifest must hash when present. stability.json and power.json are written after the
# run completes (bench stability, bench power), so the manifest never lists them.
HASHED = (F.METRICS, F.SUMMARY, F.SPEND, F.HEALTH, F.TIMINGS, "calls.jsonl", "memory_calls.jsonl")
ROW_FIELDS = (*ROW_KEY, "a_star", "mechanism", "action")


@dataclass(frozen=True)
class Failure:
    check: int
    file: str
    detail: str

    @property
    def name(self) -> str:
        return CHECKS[self.check]

    def line(self) -> str:
        return f"check {self.check} ({self.name}): {self.file}: {self.detail}"


@dataclass
class VerifyResult:
    dir: str
    grade: str | None  # the manifest's grade ("table" or "exploratory"); None when manifest.json does not parse
    failures: list[Failure] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)  # check 4 without a case file, check 6 for exploratory

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def failed_checks(self) -> list[int]:
        return sorted({f.check for f in self.failures})

    def verdict(self) -> str:
        """The last line `bench verify` prints."""
        if self.failures:
            return (f"verify: FAIL at check(s) {', '.join(map(str, self.failed_checks))}, "
                    f"{len(self.failures)} failure(s)")
        if self.grade == "table":
            return "verify: pass, grade table"
        return "verify: pass, exploratory (check 6 skipped)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe(v: Any) -> str:
    """A value from the folder as it may be printed: a token or a number as is, anything else as its sha256."""
    if v is None or isinstance(v, (bool, int, float)):
        return json.dumps(v)
    if isinstance(v, str) and TOKEN.fullmatch(v):
        return v
    text = v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)
    return "sha256:" + _sha(text.encode("utf-8"))


def _safe_path(path: str) -> str:
    segs = [s for s in path.replace("[]", "").split(".") if s]
    return path if all(TOKEN.fullmatch(s) for s in segs) else "sha256:" + _sha(path.encode("utf-8"))


def _key_str(k: tuple) -> str:
    return "(" + ", ".join(f"{n}={safe(v)}" for n, v in zip(ROW_KEY, k)) + ")"


def _read_rows(p: Path, fail) -> list[dict] | None:
    try:
        data = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        fail(F.METRICS, "cannot be read as UTF-8")
        return None
    rows: list[dict] = []
    bad = False
    for i, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            fail(F.METRICS, f"line {i} does not parse as JSON")
            bad = True
            continue
        if not isinstance(row, dict):
            fail(F.METRICS, f"line {i} is not an object")
            bad = True
            continue
        miss = [k for k in ROW_FIELDS if k not in row]
        if miss:
            fail(F.METRICS, f"line {i} misses keys {', '.join(miss)}")
            bad = True
            continue
        rows.append(row)
    return None if bad else rows


def _adapter_triples(manifest: dict) -> list[tuple]:
    return [(a.get("name"), a.get("config_label"), a.get("product_version"))
            for a in list(manifest.get("adapters") or [])[1:]]


# ---------------------------------------------------------------------------
# the seven checks
# ---------------------------------------------------------------------------

def _check_files(d: Path, manifest: dict, fail) -> None:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        fail(F.MANIFEST, "manifest lists no files (the run is not complete)")
        files = {}
    for name, want in files.items():
        if name not in ALLOWED:
            fail(safe(name), "named in manifest.files but not an allowed submission file")
            continue
        p = d / name
        if not p.is_file():
            fail(name, f"missing (manifest sha256 {safe(want)})")
            continue
        got = F.sha256_file(p)
        if got != want:
            fail(name, f"sha256 {got} differs from manifest {safe(want)}")
    for name in (*REQUIRED, SUBMISSION):
        if not (d / name).is_file():
            fail(name, "required file is missing")
    for name in HASHED:
        if (d / name).is_file() and name not in files:
            fail(name, "present but not hashed in manifest.files")
    for p in sorted(d.iterdir()):
        if p.name not in ALLOWED or not p.is_file():
            kind = "folder" if p.is_dir() else "file"
            fail(safe(p.name), f"{kind} outside the allowed list")


def _check_textfree(d: Path, fail) -> None:
    try:
        violations = scan_run(d)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        fail("-", f"a released file does not parse ({type(exc).__name__})")
        return
    for v in violations:
        fail(v.file, f"line {v.line} path {_safe_path(v.path)} length {v.length} sha256 {v.sha256}")


def _case_set(manifest: dict, rows: list[dict], cases_ids: list[str] | None) -> list[str]:
    if cases_ids is not None:
        return cases_ids
    seen: dict[str, None] = {}
    for r in rows:
        seen.setdefault(r["case_id"], None)
    return list(seen)


def _check_rows(manifest: dict, rows: list[dict], cases_ids: list[str] | None, fail) -> None:
    deciders = [x.get("id") for x in manifest.get("deciders") or []]
    adapters = list(manifest.get("adapters") or [])
    if not adapters:
        fail(F.MANIFEST, "manifest lists no adapters")
        return
    full = (adapters[0].get("name"), adapters[0].get("config_label"))
    views = [(a.get("name"), a.get("config_label")) for a in adapters[1:]]
    budgets = list(manifest.get("budgets") or [])
    reps = manifest.get("repeats")
    if not isinstance(reps, int) or reps < 1:
        fail(F.MANIFEST, f"repeats {safe(reps)} is not a positive integer")
        return
    seen: set[tuple] = set()
    for i, r in enumerate(rows, 1):
        k = F.row_key(r)
        if k in seen:
            fail(F.METRICS, f"row {i} repeats the key {_key_str(k)}")
        seen.add(k)
        if r["decider"] not in deciders:
            fail(F.METRICS, f"row {i} has decider {safe(r['decider'])}, not in the manifest")
    case_set = _case_set(manifest, rows, cases_ids)
    n = (manifest.get("cases") or {}).get("n")
    if len(case_set) != n:
        fail(F.METRICS, f"{len(case_set)} cases, the manifest says {safe(n)}")
    expected: set[tuple] = set()
    for cid in case_set:
        for did in deciders:
            expected.add((cid, did, "gate", *full, None, 0))
            expected.update((cid, did, "floor", *full, None, rr) for rr in range(1, reps + 1))
            for s in views:
                for b in budgets:
                    expected.update((cid, did, "view", *s, b, rr) for rr in range(1, reps + 1))
    in_set = set(case_set)
    for k in sorted(expected - seen, key=repr):
        fail(F.METRICS, f"missing row {_key_str(k)}")
    for k in sorted(seen - expected, key=repr):
        why = "row for a case outside the set" if k[0] not in in_set else "row outside the run's plan"
        fail(F.METRICS, f"{why} {_key_str(k)}")


def _load_cases(path: Path, fail) -> tuple[list[str], dict[str, tuple[str, str]], str] | None:
    from ..cases.schema import read_jsonl

    try:
        cases = list(read_jsonl(path))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail(str(path), f"case file does not load ({type(exc).__name__})")
        return None
    ids = [c.case_id for c in cases]
    labels = {c.case_id: (c.a_star, c.mechanism) for c in cases}
    return ids, labels, F.cases_sha256(cases)


def _check_labels(manifest: dict, rows: list[dict] | None, case_path: Path,
                  loaded: tuple[list[str], dict, str], fail) -> None:
    _ids, labels, sha = loaded
    want = (manifest.get("cases") or {}).get("sha256")
    if sha != want:
        fail(F.MANIFEST, f"cases sha256 {safe(want)} differs from {case_path.name} {sha}")
    for i, r in enumerate(rows or [], 1):
        lab = labels.get(r["case_id"])
        if lab is None:
            continue  # check 3 names rows for cases outside the set
        if r["a_star"] != lab[0]:
            fail(F.METRICS, f"row {i} {_key_str(F.row_key(r))}: a_star differs from the case file")
        if r["mechanism"] != lab[1]:
            fail(F.METRICS, f"row {i} {_key_str(F.row_key(r))}: mechanism differs from the case file")


def _check_summary(d: Path, manifest: dict, rows: list[dict] | None, fail) -> None:
    from ..scoring import summarise

    if rows is None:
        fail(F.SUMMARY, "cannot rebuild: metrics.jsonl does not parse")
        return
    p = d / F.SUMMARY
    if not p.is_file():
        fail(F.SUMMARY, "missing")
        return
    boot = manifest.get("bootstrap") or {}
    try:
        rebuilt = {**summarise(rows, seed=boot.get("seed", 11), reps=boot.get("reps", 1000)),
                   "grade": manifest.get("grade") or "exploratory"}
    except Exception as exc:  # noqa: BLE001 - any scoring error is a failed check, named by type only
        fail(F.SUMMARY, f"cannot rebuild from metrics.jsonl ({type(exc).__name__})")
        return
    want = F.dumps_json(rebuilt).encode("utf-8")
    got = p.read_bytes()
    if got != want:
        fail(F.SUMMARY, f"sha256 {_sha(got)} differs from the rebuilt summary {_sha(want)} "
                        f"(seed {safe(boot.get('seed'))}, reps {safe(boot.get('reps'))})")


def _check_protocol(manifest: dict, protocol: Path, fail) -> None:
    name = str(protocol)
    try:
        proto = load_protocol(protocol)
        mismatches = check_protocol(protocol)
    except (OSError, ValueError) as exc:
        fail(name, f"cannot read the protocol ({type(exc).__name__})")
        return
    got = (manifest.get("protocol") or {}).get("protocol_sha256")
    if got != proto.get("protocol_sha256"):
        fail(F.MANIFEST, f"protocol_sha256 {safe(got)} differs from {protocol.name} {proto.get('protocol_sha256')}")
    for m in mismatches:
        fail(name, f"freeze --check: {m.line()}")
    v = ((proto.get("steps") or {}).get("1") or {}).get("values") or {}
    deciders = [x.get("id") for x in manifest.get("deciders") or []]
    for did in deciders:
        if did not in v.get("deciders", []):
            fail(F.MANIFEST, f"decider {safe(did)} is not a frozen decider")
    for a in manifest.get("adapters") or []:
        sid = system_id(str(a.get("name")), str(a.get("config_label")))
        if sid not in v.get("systems", []):
            fail(F.MANIFEST, f"system {safe(sid)} is not a frozen system")
    boot = manifest.get("bootstrap") or {}
    for what, got_v, want in (("budgets", sorted(manifest.get("budgets") or []), sorted(v.get("budgets", []))),
                              ("repeats", manifest.get("repeats"), v.get("repeats")),
                              ("boot seed", boot.get("seed"), v.get("boot_seed")),
                              ("boot reps", boot.get("reps"), v.get("boot_reps"))):
        if got_v != want:
            fail(F.MANIFEST, f"{what} {safe(got_v)} differ from the frozen {safe(want)}")


def _check_submission(d: Path, manifest: dict, runs: Path, fail) -> None:
    p = d / SUBMISSION
    try:
        sub = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        fail(SUBMISSION, "does not parse")
        return
    if not isinstance(sub, dict):
        fail(SUBMISSION, "is not an object")
        return
    if sub.get("schema") != SUBMISSION_SCHEMA:
        fail(SUBMISSION, f"schema {safe(sub.get('schema'))} is not {SUBMISSION_SCHEMA}")
    want = {"run_id": manifest.get("run_id"),
            "protocol_sha256": (manifest.get("protocol") or {}).get("protocol_sha256")}
    for k, w in want.items():
        if sub.get(k) != w:
            fail(SUBMISSION, f"{k} {safe(sub.get(k))} differs from the manifest {safe(w)}")
    triple = (sub.get("system"), sub.get("config_label"), sub.get("product_version"))
    if triple not in _adapter_triples(manifest):
        fail(SUBMISSION, f"system, config_label, product_version {', '.join(map(safe, triple))} match no "
                         f"adapter in the manifest")
    if "grade" in sub and sub["grade"] != (manifest.get("grade") or "exploratory"):
        fail(SUBMISSION, f"grade {safe(sub['grade'])} differs from the manifest {safe(manifest.get('grade'))}")
    rep = sub.get("reproduced")
    if rep is None:
        return
    rid = rep.get("run_id") if isinstance(rep, dict) else None
    if not isinstance(rid, str) or not TOKEN.fullmatch(rid) or "/" in rid or rid in (".", ".."):
        fail(SUBMISSION, f"reproduced.run_id {safe(rid)} is not a run id")
        return
    mp = runs / rid / F.MANIFEST
    try:
        other = F.read_json(mp)
    except (OSError, ValueError):
        fail(SUBMISSION, f"reproduced run {rid} has no readable manifest under {runs}")
        return
    if other.get("state") != "complete":
        fail(SUBMISSION, f"reproduced run {rid} is not complete (state {safe(other.get('state'))})")
    if other.get("grade") != "table":
        fail(SUBMISSION, f"reproduced run {rid} is not table-grade (grade {safe(other.get('grade'))})")
    if triple not in _adapter_triples(other):
        fail(SUBMISSION, f"reproduced run {rid} has no adapter with the same system, config_label and "
                         f"product_version")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def verify(dir: str | Path, protocol: str | Path | None = None, cases: str | Path | None = None, *,
           runs: str | Path | None = None) -> VerifyResult:
    """Run the seven checks on a submission folder. `protocol` defaults to protocol/v1.json, `cases` to
    data/table/cases.jsonl for a table-grade run (check 4 is skipped for an exploratory run without `cases`),
    `runs` (where a `reproduced` run lives) to results/runs. Relative defaults resolve against the working
    directory, like every other bench command."""
    d = Path(dir)
    res = VerifyResult(dir=str(d), grade=None)

    def failer(check: int):
        return lambda file, detail: res.failures.append(Failure(check, file, detail))

    if not d.is_dir():
        failer(1)(str(d), "not a folder")
        return res
    try:
        manifest = F.read_json(d / F.MANIFEST)
        if not isinstance(manifest, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, UnicodeDecodeError):
        failer(1)(F.MANIFEST, "missing or does not parse; no other check can run")
        return res
    res.grade = "table" if manifest.get("grade") == "table" else "exploratory"

    _check_files(d, manifest, failer(1))
    _check_textfree(d, failer(2))
    rows = _read_rows(d / F.METRICS, failer(3)) if (d / F.METRICS).is_file() else None

    case_path = Path(cases) if cases is not None else (Path(TABLE_CASES) if res.grade == "table" else None)
    loaded = _load_cases(case_path, failer(4)) if case_path is not None else None
    hash_ok = loaded is not None and loaded[2] == (manifest.get("cases") or {}).get("sha256")
    if rows is not None:
        _check_rows(manifest, rows, loaded[0] if hash_ok else None, failer(3))
    elif not (d / F.METRICS).is_file():
        failer(3)(F.METRICS, "missing")
    if case_path is None:
        res.skipped.append(4)
    elif loaded is not None:
        _check_labels(manifest, rows, case_path, loaded, failer(4))
    _check_summary(d, manifest, rows, failer(5))
    if res.grade == "table":
        _check_protocol(manifest, Path(protocol) if protocol is not None else Path(DEFAULT_PROTOCOL), failer(6))
    else:
        res.skipped.append(6)
    _check_submission(d, manifest, Path(runs) if runs is not None else Path(DEFAULT_RUNS), failer(7))
    res.failures.sort(key=lambda f: f.check)  # stable: the order within a check is kept
    return res


__all__ = ["verify", "VerifyResult", "Failure", "CHECKS", "EXIT_FAIL", "safe"]
