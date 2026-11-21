"""Protocol freeze and the run grade (docs/freeze.md, CHARTER section 5).

- `freeze_step` writes one step of `protocol/v1.json` from `protocol/v1.spec.json`: a sha256 per matched file,
  the step's values, `step_sha256` over the canonical JSON of `{files, values}`, the UTC time and the git commit.
  It refuses a dirty or untracked matched file, a missing earlier step, a step already frozen, a glob that
  matches nothing, a missing file and a null value.
- `check_protocol` recomputes every hash of every frozen step and returns the mismatches (hashes only).
- `check_run` holds the four run checks of section 4. It raises `ProtocolError` naming the failed check, or
  returns the grade and the manifest's `protocol` entry.

Paths in the spec are relative to the repo root: the git top level of the spec's (or protocol's) folder.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SPEC_SCHEMA = "memory_bench.protocol_spec/1"
PROTOCOL_SCHEMA = "memory_bench.protocol/1"
DEFAULT_SPEC = "protocol/v1.spec.json"
DEFAULT_PROTOCOL = "protocol/v1.json"
TABLE_CASES = "data/table/cases.jsonl"
EXIT_MISMATCH = 4
GLOB_CHARS = set("*?[")
DEFAULT_CONFIG_LABEL = "default"


class FreezeError(RuntimeError):
    """`bench freeze --step` refuses."""


class ProtocolError(RuntimeError):
    """A run with --protocol failed a check (docs/freeze.md section 4). The run stops; exit 2."""


@dataclass(frozen=True)
class Mismatch:
    path: str
    expected: str
    actual: str

    def line(self) -> str:
        return f"{self.path}  expected {self.expected}  actual {self.actual}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def canonical(obj: Any) -> str:
    """Sorted keys, no spaces, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(Path(p).read_bytes())


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)


def repo_root(path: str | Path) -> Path:
    """The git top level of the folder holding `path`; without git, the folder's parent (protocol/..)."""
    folder = Path(path).resolve().parent
    try:
        out = _git(folder, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        out = None
    if out is not None and out.returncode == 0 and out.stdout.strip():
        return Path(out.stdout.strip()).resolve()
    return folder.parent


def expand(root: Path, patterns: Sequence[str]) -> list[str]:
    """Matched files, relative POSIX paths. Each glob is expanded in sorted order; a glob that matches nothing
    and a missing literal file are errors. Order: spec order, globs sorted, first occurrence kept."""
    out: list[str] = []
    for pat in patterns:
        if GLOB_CHARS & set(pat):
            hits = sorted(p.relative_to(root).as_posix() for p in root.glob(pat) if p.is_file())
            if not hits:
                raise FreezeError(f"glob {pat!r} matches no file")
        else:
            if not (root / pat).is_file():
                raise FreezeError(f"file {pat!r} is missing")
            hits = [pat]
        out.extend(h for h in hits if h not in out)
    return out


def _nulls(obj: Any, path: str = "values") -> list[str]:
    if obj is None:
        return [path]
    if isinstance(obj, dict):
        return [n for k, v in obj.items() for n in _nulls(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [n for i, v in enumerate(obj) for n in _nulls(v, f"{path}[{i}]")]
    return []


def step_sha256(files: Mapping[str, str], values: Any) -> str:
    return sha256_bytes(canonical({"files": dict(files), "values": values}).encode("utf-8"))


def protocol_sha256(steps: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of all frozen steps (every key of every step)."""
    return sha256_bytes(canonical(dict(steps)).encode("utf-8"))


def load_spec(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("schema") != SPEC_SCHEMA:
        raise FreezeError(f"{path}: schema is {spec.get('schema')!r}, expected {SPEC_SCHEMA!r}")
    if not isinstance(spec.get("steps"), dict) or not spec["steps"]:
        raise FreezeError(f"{path}: no steps")
    return spec


def load_protocol(path: str | Path) -> dict:
    proto = json.loads(Path(path).read_text(encoding="utf-8"))
    if proto.get("schema") != PROTOCOL_SCHEMA:
        raise ValueError(f"{path}: schema is {proto.get('schema')!r}, expected {PROTOCOL_SCHEMA!r}")
    return proto


def _write(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# bench freeze --step N
# ---------------------------------------------------------------------------

def _dirty(root: Path, files: list[str]) -> list[str]:
    """Matched files whose bytes no commit describes: changed, staged, untracked or ignored."""
    st = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *files)
    if st.returncode != 0:
        raise FreezeError(f"git status failed in {root}: {st.stderr.strip()}")
    bad = {line[3:].strip() for line in st.stdout.splitlines() if line.strip()}
    tracked = _git(root, "ls-files", "--", *files)
    known = set(tracked.stdout.splitlines())
    bad |= {f for f in files if f not in known}
    return sorted(bad)


def freeze_step(step: int, spec_path: str | Path = DEFAULT_SPEC, out_path: str | Path = DEFAULT_PROTOCOL,
                now: str | None = None) -> dict:
    """Freeze one step into the protocol file. Returns the written protocol."""
    spec_path, out_path = Path(spec_path), Path(out_path)
    spec = load_spec(spec_path)
    key = str(int(step))
    if key not in spec["steps"]:
        raise FreezeError(f"step {key} is not in {spec_path} (steps: {', '.join(spec['steps'])})")
    root = repo_root(spec_path)
    if _git(root, "rev-parse", "HEAD").returncode != 0:
        raise FreezeError(f"{root} is not a git repository with a commit; the hash must describe a commit")
    proto = load_protocol(out_path) if out_path.exists() else None
    if proto is not None and proto.get("version") != spec.get("version"):
        raise FreezeError(f"{out_path} is version {proto.get('version')!r}, the spec is {spec.get('version')!r}")
    frozen = (proto or {}).get("steps", {})
    if key in frozen:
        raise FreezeError(f"step {key} is already frozen in {out_path}; a change makes a new protocol version "
                          f"(charter section 5 rule 3)")
    if int(key) > 1 and str(int(key) - 1) not in frozen:
        raise FreezeError(f"step {int(key) - 1} is not frozen in {out_path}; freeze it first")
    s = spec["steps"][key]
    values = s.get("values", {})
    nulls = _nulls(values)
    if nulls:
        raise FreezeError(f"step {key} has null values: {', '.join(nulls)}")
    files = expand(root, s.get("files", []))
    dirty = _dirty(root, files)
    if dirty:
        raise FreezeError(f"uncommitted changes in matched files: {', '.join(dirty)}")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise FreezeError(f"no git commit in {root}")
    hashes = {f: sha256_file(root / f) for f in files}
    entry = {"title": s.get("title"), "patterns": list(s.get("files", [])),
             "frozen_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "git_commit": head.stdout.strip(), "files": hashes, "values": values,
             "step_sha256": step_sha256(hashes, values)}
    steps = {**frozen, key: entry}
    out = {"schema": PROTOCOL_SCHEMA, "version": spec.get("version"),
           "spec": root_relative(spec_path, root), "steps": steps, "protocol_sha256": protocol_sha256(steps)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write(out_path, out)
    return out


def root_relative(p: str | Path, root: Path) -> str:
    rp = Path(p).resolve()
    try:
        return rp.relative_to(root).as_posix()
    except ValueError:
        return str(rp)


# ---------------------------------------------------------------------------
# bench freeze --check
# ---------------------------------------------------------------------------

def check_protocol(protocol_path: str | Path = DEFAULT_PROTOCOL) -> list[Mismatch]:
    """Recompute every hash of every frozen step. Empty list when all match."""
    path = Path(protocol_path)
    proto = load_protocol(path)
    root = repo_root(path)
    out: list[Mismatch] = []
    steps = proto.get("steps") or {}
    for key in sorted(steps, key=int):
        st = steps[key]
        for f, want in st.get("files", {}).items():
            p = root / f
            got = sha256_file(p) if p.is_file() else "missing"
            if got != want:
                out.append(Mismatch(f, want, got))
        got = step_sha256(st.get("files", {}), st.get("values"))
        if got != st.get("step_sha256"):
            out.append(Mismatch(f"step {key} step_sha256", str(st.get("step_sha256")), got))
    got = protocol_sha256(steps)
    if got != proto.get("protocol_sha256"):
        out.append(Mismatch("protocol_sha256", str(proto.get("protocol_sha256")), got))
    return out


# ---------------------------------------------------------------------------
# Run checks (section 4)
# ---------------------------------------------------------------------------

def system_of(spec: Any) -> tuple[str, str]:
    """(adapter name, config label) of an adapter spec: a config dict, a registry name, a class or an instance."""
    if isinstance(spec, dict):
        return str(spec.get("adapter")), str(spec.get("config_label") or DEFAULT_CONFIG_LABEL)
    if isinstance(spec, str):
        return spec, DEFAULT_CONFIG_LABEL
    return str(getattr(spec, "name", spec)), str(getattr(spec, "config_label", DEFAULT_CONFIG_LABEL))


def system_id(name: str, label: str) -> str:
    return name if label == DEFAULT_CONFIG_LABEL else f"{name}/{label}"


def _frozen_file(proto: dict, root: Path, given: str | Path, what: str) -> tuple[str, str]:
    """(relative path, frozen sha256) of a given config file, which must be one of the frozen files."""
    rel = root_relative(given, root)
    for st in proto["steps"].values():
        if rel in st.get("files", {}):
            return rel, st["files"][rel]
    raise ProtocolError(f"check 3 (product adapters): {what} {given} is not a frozen file")


def check_run(protocol_path: str | Path, *, decider_id: str, adapters: Sequence[Any],
              adapter_files: Mapping[str, str | Path], budgets: Iterable[int], repeats: int, boot_seed: int,
              boot_reps: int, memory_side: Any, cases_sha256: str, is_product) -> dict:
    """The four checks of docs/freeze.md section 4, before any model call. Raises ProtocolError naming the failed
    check. Returns {"grade", "protocol"} for the manifest. `adapter_files` maps a system id (`mem0/k25`) to the
    config file it was loaded from; `is_product(spec)` tells a product adapter from a baseline."""
    from ..adapters import ADAPTERS  # late: the adapters package imports engine-free modules only

    path = Path(protocol_path)
    try:
        proto = load_protocol(path)
        mismatches = check_protocol(path)
    except (OSError, ValueError) as exc:
        raise ProtocolError(f"check 1 (freeze --check): cannot read {path}: {exc}") from None
    if mismatches:
        raise ProtocolError("check 1 (freeze --check): " + "; ".join(m.line() for m in mismatches))
    steps = proto.get("steps") or {}
    if "1" not in steps:
        raise ProtocolError(f"check 1 (freeze --check): step 1 is not frozen in {path}")
    root = repo_root(path)
    v = steps["1"]["values"]

    # check 2: decider, systems, budgets, repeats, bootstrap
    systems = ["full", *(system_id(*system_of(a)) for a in adapters)]
    fails = []
    if decider_id not in v.get("deciders", []):
        fails.append(f"decider {decider_id!r} is not in the frozen deciders {v.get('deciders')}")
    extra = [s for s in systems if s not in v.get("systems", [])]
    if extra:
        fails.append(f"systems {extra} are not in the frozen systems {v.get('systems')}")
    for name, got, want in (("budgets", sorted({int(b) for b in budgets}), sorted(v.get("budgets", []))),
                            ("repeats", repeats, v.get("repeats")), ("boot seed", boot_seed, v.get("boot_seed")),
                            ("boot reps", boot_reps, v.get("boot_reps"))):
        if got != want:
            fails.append(f"{name} {got!r} differ from the frozen {want!r}")
    if fails:
        raise ProtocolError("check 2 (deciders and run values): " + "; ".join(fails))

    # check 3: product adapters: package version, adapter config file, memory-side config file
    pins = v.get("product_pins", {})
    products = [a for a in adapters if is_product(a)]
    for a in products:
        name, label = system_of(a)
        sid = system_id(name, label)
        cls = ADAPTERS.get(name) if not isinstance(a, type) else a
        mod = importlib.import_module(cls.__module__) if cls is not None else None
        package = getattr(mod, "PACKAGE", None)
        if package is None or package not in pins:
            raise ProtocolError(f"check 3 (product adapters): {sid}: no pinned package in product_pins {pins}")
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            raise ProtocolError(f"check 3 (product adapters): {sid}: {package} is not installed in this "
                                f"environment (pinned {pins[package]})") from None
        if installed != pins[package]:
            raise ProtocolError(f"check 3 (product adapters): {sid}: {package} {installed} is installed, "
                                f"the frozen pin is {pins[package]}")
        f = adapter_files.get(sid)
        if f is None:
            raise ProtocolError(f"check 3 (product adapters): {sid}: give its adapter config file (configs/adapters/)")
        rel, want = _frozen_file(proto, root, f, "adapter config")
        got = sha256_file(Path(f))
        if got != want:
            raise ProtocolError(f"check 3 (product adapters): adapter config {rel}: sha256 {got} differs from "
                                f"the frozen {want}")
        if isinstance(a, dict) and json.loads(Path(f).read_text(encoding="utf-8")) != a:
            raise ProtocolError(f"check 3 (product adapters): {sid}: the adapter config differs from {rel}")
    if products:
        if not isinstance(memory_side, (str, Path)):
            raise ProtocolError("check 3 (product adapters): give the memory-side config as a file "
                                "(configs/memory-side/)")
        rel, want = _frozen_file(proto, root, memory_side, "memory-side config")
        got = sha256_file(Path(memory_side))
        if got != want:
            raise ProtocolError(f"check 3 (product adapters): memory-side config {rel}: sha256 {got} differs "
                                f"from the frozen {want}")

    # check 4: table cases. The run's cases are hashed as canonical cases.jsonl bytes (engine.files.cases_sha256),
    # which equal the file's bytes for a file written by cases.schema.write_jsonl, and also catch a cut (--n).
    entry: dict[str, Any] = {"path": str(path), "version": proto.get("version"),
                             "protocol_sha256": proto.get("protocol_sha256"), "steps": sorted(steps, key=int)}
    if "2" in steps:
        want = steps["2"].get("files", {}).get(TABLE_CASES)
        if want is None or cases_sha256 != want:
            raise ProtocolError(f"check 4 (table cases): the run's cases sha256 {cases_sha256} differs from the "
                                f"frozen {TABLE_CASES} {want}")
        return {"grade": "table", "protocol": entry}
    entry["exploratory_reason"] = "step 2 (table cases and table seed) is not frozen"
    return {"grade": "exploratory", "protocol": entry}


__all__ = ["freeze_step", "check_protocol", "check_run", "Mismatch", "FreezeError", "ProtocolError",
           "EXIT_MISMATCH", "DEFAULT_SPEC", "DEFAULT_PROTOCOL", "canonical", "step_sha256", "protocol_sha256"]
