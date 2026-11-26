"""The run engine: gate, floor and view decisions per case (CHARTER section 4.2, docs/run-files.md).

Per case, in case order:
1. the `full` adapter builds one stored view; the decider reads it once for the gate row
   (repeat 0) and R more times for the floor rows (repeats 1..R);
2. for each adapter (config order) and budget (ascending): reset, ingest, retrieve,
   enforce_budget, then R decide calls on that one stored view.
The case's rows are appended to metrics.jsonl and flushed once the case is done.

`decide_all` is the one place decide calls happen. With `parallel` > 1 the decision calls of one case
(gate, floor repeats, every adapter x budget x repeat view) go to a thread pool shared by the run, while
adapter work stays sequential on the main thread; the main thread waits for the case's calls and writes
its rows in canonical order, so metrics.jsonl and summary.json do not depend on `parallel`. With
`parallel` = 1 no pool is made and calls run inline, as before. Adapters come from the registry or are
passed in as instances (product adapters).

Model deciders call through a `Gateway` (docs/deciders.md section 4). With a gateway, spend.json
comes from its meter and a `CapReached` ends the run with manifest state `stopped_at_cap`; the
same directory resumes with a higher cap.

Product adapters (class attribute `needs_proxy = True`, docs/product-proxy.md section 5) are given as a
registry name or a class and built with `proxy=`: the engine starts one `ProductProxy` per run from
`RunConfig.memory_side`, wires one cap across the decider and the memory side and stops the proxy at the end.
They go through the view stage (engine/views.py, docs/view-stage.md) before the decision loop: one reset,
ingest and retrieve per case at the largest budget, on `ingest_workers` threads, kept in
`private/views.jsonl`. The decision loop then cuts the stored view per budget instead of calling the product.
Builtin adapters stay inline, so a run without a product adapter writes the same files as before.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, Executor, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .. import __version__
from ..adapters import ADAPTERS, get_adapter
from ..adapters.base import MemoryAdapter, Record, Retrieved, count_tokens, enforce_budget, records_of
from ..cases.schema import Case, read_jsonl
from ..deciders.base import PARSE_ERROR, VALID_OUTPUTS, Decider, DecisionInput
from ..deciders.model import decider_spec
from ..gateway.cache import DEFAULT_PATH
from ..gateway.meter import CALLS, CapReached, Gateway
from ..gateway.proxy import MEMORY_CALLS, ProductProxy, load_memory_side, memory_side_spec
from ..scoring import summarise
from ..scoring.metrics import DEFAULT_CONFIG_LABEL, cell_name
from . import files as F
from .freeze import ProtocolError, check_run
from .ledgers import stamp_spend, validate_ledger
from .opencode_meter import needs_zen_opener, zen_urllib_opener
from .views import MAX_INGEST_WORKERS, ViewStore, view_stage, views_path

KIND_ORDER = {"gate": 0, "floor": 1, "view": 2}
FULL = "full"
MAX_PARALLEL = 8


class RunError(RuntimeError):
    """The run cannot start or its files are inconsistent."""


class ResumeMismatch(RunError):
    """The run directory belongs to a run with a different configuration."""


def check_opencode_binary_version(decider: Decider) -> str | None:
    """Pin check for `opencode_cli` deciders: verify `<cmd> --version` matches cfg['opencode_version'].

    Returns the verified version string (e.g. '2.3.0') for `opencode_cli` deciders, or None for others.
    Aborts with RunError if the binary version differs or cannot be queried.
    """
    cfg = getattr(decider, "cfg", None)
    if not isinstance(cfg, dict) or cfg.get("transport") != "opencode_cli":
        return None
    expected = str(cfg.get("opencode_version") or "2.3.0").strip().lstrip("v")
    cmd = list(cfg.get("command") or ["opencode"])
    exe = cmd[0]
    resolved = shutil.which(exe) or exe
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunError(f"opencode binary check failed for {resolved!r}: {exc}") from None
    raw = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0 or not raw:
        raise RunError(f"opencode binary check failed ({resolved!r}, exit {proc.returncode}): {raw!r}")
    m = re.search(r"v?(\d+\.\d+\.\d+)", raw)
    actual = m.group(1) if m else raw
    if actual != expected:
        raise RunError(
            f"opencode binary version mismatch at {resolved!r}: expected {expected!r} "
            f"(frozen protocol config), got {actual!r} ({raw!r}); aborting run"
        )
    return actual


@dataclass(kw_only=True)
class RunConfig:
    out_dir: str | Path
    decider: Decider
    run_id: str | None = None  # defaults to the name of out_dir
    cases_path: str | Path | None = None
    cases: list[Case] | None = None  # give exactly one of cases_path and cases
    adapters: list[str | type | MemoryAdapter] = field(default_factory=lambda: ["bm25", "recent"])  # never "full"
    budgets: list[int] = field(default_factory=lambda: [1000])
    repeats: int = 3
    grade: str = "exploratory"
    cap_usd: float = 0.0
    boot_seed: int = 11
    boot_reps: int = 1000
    track: str = "account_review"
    gateway: Gateway | None = None  # defaults to decider.gateway for a ModelDecider; its cap_usd wins
    parallel: int = 1  # decision calls in flight at once, 1..MAX_PARALLEL; does not change result files
    memory_side: str | Path | dict | None = None  # memory-side config for product adapters (docs/product-proxy.md)
    proxy_cache: str | Path | None = None  # the proxy's call cache; default the gateway's, else DEFAULT_PATH
    allow_unpriced: bool = False  # a memory-side upstream with a null price (a gateway's own flag also counts)
    proxy_options: dict = field(default_factory=dict)  # test seams passed to ProductProxy: opener, runner, sleep
    ingest_workers: int = 1  # view-stage threads for product adapters, 1..MAX_INGEST_WORKERS (docs/view-stage.md)
    protocol: str | Path | None = None  # protocol/v1.json: run the checks of docs/freeze.md section 4 first
    adapter_files: dict[str, str | Path] = field(default_factory=dict)  # system id ("mem0/k25") -> config file
    ledger: str | None = None  # optional two-ledger tag ("main" or "sonnet", Round 5 A1)


# ---------------------------------------------------------------------------
# Decide seam
# ---------------------------------------------------------------------------

def _valid_outputs_for(track: str) -> tuple[str, ...]:
    if track == "account_review":
        return VALID_OUTPUTS
    from ..tracks import get_track

    return (*get_track(track).meta.valid_actions, PARSE_ERROR)


def decide_one(decider: Decider, inp: DecisionInput, context: dict | None = None) -> str:
    """One decide call. A decider with `accepts_context` gets the context (kind, system, config_label, budget)
    for the gateway's call log and spend attribution."""
    if context is not None and getattr(decider, "accepts_context", False):
        action = decider.decide(inp, context=context)
    else:
        action = decider.decide(inp)
    valid = _valid_outputs_for(getattr(inp, "track", "account_review"))
    if action not in valid:
        raise RunError(f"decider {decider.id!r} returned {action!r} for case {inp.case_id!r}; "
                       f"expected one of {valid}")
    return action


def gather(futures: Sequence[Future]) -> list:
    """Results of futures in order. On the first exception: cancel what has not started, wait for the calls
    in flight (their replies are cached), then raise. A non-CapReached error wins over CapReached; among
    errors of one kind the earliest future wins."""
    done, _ = wait(futures, return_when=FIRST_EXCEPTION)
    if any(f.exception() is not None for f in done if not f.cancelled()):
        for f in futures:
            f.cancel()
        wait(futures)
        errors = [f.exception() for f in futures if not f.cancelled() and f.exception() is not None]
        raise next((e for e in errors if not isinstance(e, CapReached)), errors[0])
    return [f.result() for f in futures]


def decide_all(decider: Decider, inputs: Sequence[DecisionInput],
               contexts: Sequence[dict] | None = None, executor: Executor | None = None) -> list[str]:
    """Every decide call of one case, results in input order. Inline without an executor; otherwise all
    calls go to the executor at once and this waits for them (see `gather` for errors)."""
    ctxs = list(contexts) if contexts is not None else [None] * len(inputs)
    if executor is None:
        return [decide_one(decider, inp, ctx) for inp, ctx in zip(inputs, ctxs)]
    return gather([executor.submit(decide_one, decider, inp, ctx) for inp, ctx in zip(inputs, ctxs)])


def check_parallel(parallel: int) -> int:
    p = int(parallel)
    if not 1 <= p <= MAX_PARALLEL:
        raise RunError(f"parallel must be between 1 and {MAX_PARALLEL}, got {parallel}")
    return p


def check_ingest_workers(workers: int) -> int:
    w = int(workers)
    if not 1 <= w <= MAX_INGEST_WORKERS:
        raise RunError(f"ingest_workers must be between 1 and {MAX_INGEST_WORKERS}, got {workers}")
    return w


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _load_cases(config: RunConfig) -> tuple[list[Case], str | None]:
    if (config.cases is None) == (config.cases_path is None):
        raise RunError("give exactly one of cases and cases_path")
    if config.cases_path is not None:
        cases, path = list(read_jsonl(config.cases_path)), str(config.cases_path)
    else:
        cases, path = list(config.cases), None
    if not cases:
        raise RunError("no cases")
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise RunError("case ids are not unique")
    return cases, path


def needs_proxy(spec: str | type | MemoryAdapter) -> bool:
    """True for a product adapter: a registry name, class or instance whose class sets needs_proxy."""
    if isinstance(spec, dict):  # an adapter config: {"adapter": name, "config_label": label, "config": {...}}
        cls = ADAPTERS.get(spec.get("adapter"))
    elif isinstance(spec, str):
        cls = ADAPTERS.get(spec)
    else:
        cls = spec if isinstance(spec, type) else type(spec)
    return bool(getattr(cls, "needs_proxy", False))


def spec_name(spec: Any) -> str:
    """The adapter name of a spec, for messages."""
    return str(spec.get("adapter")) if isinstance(spec, dict) else str(getattr(spec, "name", spec))


def _from_config(spec: dict, proxy: ProductProxy | None) -> MemoryAdapter:
    """Build an adapter from a config spec. The label and the config are what the manifest hashes."""
    unknown = set(spec) - {"adapter", "config_label", "config", "description"}
    if unknown or "adapter" not in spec:
        raise RunError("adapter config needs 'adapter' and may have 'config_label', 'config', 'description'; "
                       f"got {sorted(spec)}")
    name, label = spec["adapter"], str(spec.get("config_label") or DEFAULT_CONFIG_LABEL)
    cfg = dict(spec.get("config") or {})
    if name not in ADAPTERS:
        raise RunError(f"unknown adapter {name!r}; choose from {sorted(ADAPTERS)}")
    if needs_proxy(spec):
        if proxy is None:
            raise RunError(f"adapter {name!r} needs the product proxy; pass a memory-side config "
                           f"(bench run --memory-side CONFIG)")
        return ADAPTERS[name](proxy=proxy, config_label=label, **cfg)
    a = get_adapter(name, **cfg)
    a.config_label = label
    return a


def _build_adapters(specs: Sequence[str | type | MemoryAdapter],
                    proxy: ProductProxy | None = None) -> list[MemoryAdapter]:
    out: list[MemoryAdapter] = []
    for s in specs:
        if isinstance(s, dict):
            a = _from_config(s, proxy)
        elif needs_proxy(s):
            if not isinstance(s, (str, type)):
                raise RunError(f"product adapter {getattr(s, 'name', s)!r} needs the product proxy: pass its "
                               f"registry name or its class, so the engine can build it with proxy=")
            if proxy is None:
                raise RunError(f"adapter {getattr(s, 'name', s)!r} needs the product proxy; pass a memory-side "
                               f"config (bench run --memory-side CONFIG)")
            a = (ADAPTERS[s] if isinstance(s, str) else s)(proxy=proxy)
        elif isinstance(s, str):
            a = get_adapter(s)
        else:
            a = s() if isinstance(s, type) else s
        if a.name == FULL or a.reference:
            raise RunError("the full (reference) adapter runs for every case; do not list it in adapters")
        out.append(a)
    keys = [(a.name, a.config_label) for a in out]
    if len(set(keys)) != len(keys):
        raise RunError(f"adapters repeat a (name, config_label) pair: {keys}")
    if not out:
        raise RunError("no adapters")
    return out


def _adapter_entry(a: MemoryAdapter) -> dict[str, Any]:
    cfg = getattr(a, "config", {}) or {}
    cfg_text = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return {"name": a.name, "product_version": a.product_version, "config_label": a.config_label,
            "config_sha256": F.sha256_text(cfg_text)}


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _ms_key(spec: dict | None) -> tuple | None:
    """What a resume must keep of the memory side: its id and the two pinned models (not the config hash,
    so a price note can be updated between attempts)."""
    return None if spec is None else (spec.get("id"), spec.get("chat_model"), spec.get("embeddings_model"))


def _check_resume(old: dict, new: dict) -> None:
    def adapters(m: dict) -> list:
        return [(a["name"], a["config_label"]) for a in m.get("adapters", [])]

    checks = {
        "cases sha256": (old.get("cases", {}).get("sha256"), new["cases"]["sha256"]),
        "decider id": ([d["id"] for d in old.get("deciders", [])], [d["id"] for d in new["deciders"]]),
        "adapters": (adapters(old), adapters(new)),
        "budgets": (old.get("budgets"), new["budgets"]),
        "repeats": (old.get("repeats"), new["repeats"]),
        "memory side": (_ms_key(old.get("memory_side")), _ms_key(new.get("memory_side"))),
        "grade": (old.get("grade", "exploratory"), new["grade"]),
        "protocol sha256": ((old.get("protocol") or {}).get("protocol_sha256"),
                            (new.get("protocol") or {}).get("protocol_sha256")),
    }
    diffs = [f"{k}: directory has {a!r}, config has {b!r}" for k, (a, b) in checks.items() if a != b]
    if diffs:
        raise ResumeMismatch(f"refusing to resume in {new['_dir']}: " + "; ".join(diffs))


# ---------------------------------------------------------------------------
# Health and timings
# ---------------------------------------------------------------------------

class _WarningCounter(logging.Handler):
    """Counts WARNING-and-above log records by logger name while a product call runs. A product that drops
    a memory because the shared model's reply did not parse says so only in its log; the count makes that
    visible. No message text is kept."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.counts: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        self.counts[record.name] = self.counts.get(record.name, 0) + 1


class _Health:
    """Per-adapter health and the timing rows. Every update is under one lock, so the view stage's worker
    threads can record calls, errors and successes at once (docs/view-stage.md section 2.2)."""

    def __init__(self, adapters: list[MemoryAdapter]):
        self.lock = threading.RLock()
        self.entries = {self._k(a): {"name": a.name, "config_label": a.config_label,
                                     "product_version": a.product_version, "errors": 0, "last_error": None,
                                     "last_ok_at": None, "_ingest": [], "_retrieve": [], "_warnings": {}}
                        for a in adapters}
        self.timings: list[dict] = []
        self.unattributed: dict[str, int] = {}  # product warnings the view stage could not tie to one item

    @staticmethod
    def _k(a: MemoryAdapter) -> tuple[str, str]:
        return (a.name, a.config_label)

    def timed(self, a: MemoryAdapter, case_id: str, budget: int | None, call: str, fn, *args):
        """One inline adapter call on the calling thread (builtin adapters and `full`; a product adapter here
        gets the counter the engine used before the view stage)."""
        counter = _WarningCounter() if getattr(a, "needs_proxy", False) else None
        root = logging.getLogger()
        if counter is not None:
            root.addHandler(counter)
        t0 = time.perf_counter()
        try:
            out = fn(*args)
        except BaseException:
            if counter is not None:  # a failed call has no timing row; its warnings still count
                with self.lock:
                    seen = self.entries[self._k(a)]["_warnings"]
                    for name, n in counter.counts.items():
                        seen[name] = seen.get(name, 0) + n
            raise
        finally:
            if counter is not None:
                root.removeHandler(counter)
        self.record_call(a, case_id, budget, call, time.perf_counter() - t0,
                         counter.counts if counter is not None else None)
        return out

    def record_call(self, a: MemoryAdapter, case_id: str, budget: int | None, call: str, seconds: float,
                    warnings: dict[str, int] | None = None) -> None:
        """One timing row. `warnings` (product adapters only, so builtin rows keep their shape): counts by
        logger name of the WARNING-and-above records attributed to this call."""
        row = {"at": F.now(), "case_id": case_id, "system": a.name, "config_label": a.config_label,
               "budget": budget, "call": call, "seconds": round(seconds, 6)}
        if warnings is not None:
            row["warnings"] = sum(warnings.values())
            if row["warnings"]:  # by logger, so `bench health rebuild` can split the total (rows with none keep their shape)
                row["warnings_by_logger"] = dict(sorted(warnings.items()))
        with self.lock:
            e = self.entries[self._k(a)]
            for name, n in (warnings or {}).items():
                e["_warnings"][name] = e["_warnings"].get(name, 0) + n
            self.timings.append(row)
            if call in ("ingest", "retrieve"):
                e["_" + call].append(seconds)

    def take_timings(self) -> list[dict]:
        with self.lock:
            rows, self.timings = self.timings, []
        return rows

    def add_unattributed(self, counts: dict[str, int]) -> None:
        with self.lock:
            for name, n in counts.items():
                self.unattributed[name] = self.unattributed.get(name, 0) + n

    def ok(self, a: MemoryAdapter) -> None:
        with self.lock:
            self.entries[self._k(a)]["last_ok_at"] = F.now()

    def error(self, a: MemoryAdapter, case_id: str, exc: BaseException) -> None:
        with self.lock:
            e = self.entries[self._k(a)]
            e["errors"] += 1
            e["last_error"] = {"case_id": case_id, "at": F.now(), "error": f"{type(exc).__name__}: {exc}"}

    def report(self, run_id: str, cases_done: dict[tuple[str, str], int]) -> dict:
        def mean(xs: list[float]) -> float | None:
            return round(sum(xs) / len(xs), 6) if xs else None

        out = []
        with self.lock:
            for k, e in self.entries.items():
                out.append({"name": e["name"], "config_label": e["config_label"],
                            "product_version": e["product_version"], "ok": e["errors"] == 0,
                            "cases_done": cases_done.get(k, 0), "errors": e["errors"],
                            "last_error": e["last_error"],
                            "ingest_seconds_mean": mean(e["_ingest"]), "retrieve_seconds_mean": mean(e["_retrieve"]),
                            "last_ok_at": e["last_ok_at"],
                            "product_warnings": {"total": sum(e["_warnings"].values()),
                                                 "by_logger": dict(sorted(e["_warnings"].items()))}})
            rep = {"run_id": run_id, "updated": F.now(), "adapters": out}
            if self.unattributed:  # only when the view stage saw such records, so other runs keep their shape
                rep["unattributed_warnings"] = {"total": sum(self.unattributed.values()),
                                                "by_logger": dict(sorted(self.unattributed.items()))}
        return rep


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

class _Plan:
    """Expected row keys and the canonical row order."""

    def __init__(self, cases: list[Case], decider_id: str, full: MemoryAdapter, adapters: list[MemoryAdapter],
                 budgets: list[int], repeats: int):
        self.cases, self.did, self.full, self.adapters = cases, decider_id, full, adapters
        self.budgets, self.repeats = budgets, repeats
        self.case_idx = {c.case_id: i for i, c in enumerate(cases)}
        self.sys_idx = {(full.name, full.config_label): -1}
        self.sys_idx.update({(a.name, a.config_label): i for i, a in enumerate(adapters)})

    def full_keys(self, cid: str) -> list[tuple]:
        f = (self.full.name, self.full.config_label)
        return [(cid, self.did, "gate", *f, None, 0)] + [(cid, self.did, "floor", *f, None, r)
                                                         for r in range(1, self.repeats + 1)]

    def view_keys(self, cid: str, a: MemoryAdapter, budget: int) -> list[tuple]:
        return [(cid, self.did, "view", a.name, a.config_label, budget, r) for r in range(1, self.repeats + 1)]

    def all_keys(self, cid: str) -> list[tuple]:
        keys = self.full_keys(cid)
        for a in self.adapters:
            for b in self.budgets:
                keys += self.view_keys(cid, a, b)
        return keys

    def sort_key(self, row: dict) -> tuple:
        try:
            ci = self.case_idx[row["case_id"]]
            si = self.sys_idx[(row["system"], row["config_label"])]
        except KeyError:
            raise RunError(f"metrics.jsonl has a row outside this run's plan: {F.row_key(row)}") from None
        if row["decider"] != self.did:
            raise RunError(f"metrics.jsonl has a row of decider {row['decider']!r}, run decider is {self.did!r}")
        b = row["budget"]
        return (ci, KIND_ORDER[row["kind"]], si, -1 if b is None else b, row["repeat"])


def _fetch_view(a: MemoryAdapter, case: Case, records: list[Record], budget: int | None,
                health: _Health, usage: dict, proxy: ProductProxy | None = None) -> Retrieved:
    cid = case.case_id

    def call(name: str, fn, *args):
        try:
            return health.timed(a, cid, budget, name, fn, *args)
        finally:
            if proxy is not None and proxy.cap_reached:  # the memory side hit the one cap: stop the run
                raise CapReached(proxy.cap_detail or "memory-side call refused at the cap")

    call("reset", a.reset, cid)
    call("ingest", a.ingest, records)
    got = call("retrieve", a.retrieve, case.query_text, 0 if budget is None else budget)
    got = enforce_budget(got, 0 if budget is None else budget, reference=a.reference)
    u = usage.setdefault(a.name, {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "embed_calls": 0})
    for k in u:
        u[k] += int(getattr(got.usage, k, 0))
    return got


def _view_fields(v: Retrieved) -> dict[str, Any]:
    units = list(v.units)
    return {"view_tokens": count_tokens(units) if units else count_tokens([v.text]),
            "view_units": len(units), "view_sha256": F.sha256_text(v.text)}


def _run_case(case: Case, plan: _Plan, decider: Decider, present: set[tuple], track: str,
              health: _Health, usage: dict, executor: Executor | None = None,
              proxy: ProductProxy | None = None, store: ViewStore | None = None) -> tuple[list[dict], list[str]]:
    """Rows still missing for this case, in canonical order, and the names of cells skipped on error. A product
    adapter's view comes from the store (the view stage made it) and is cut per budget; a pair with no stored
    view failed in the stage, so its cells stay incomplete."""
    cid = case.case_id
    base = {"case_id": cid, "track": track, "mechanism": case.mechanism, "a_star": case.a_star,
            "decider": decider.id}
    records: list[Record] | None = None
    pending: list[tuple[dict, DecisionInput]] = []
    failed: list[str] = []

    def add(a: MemoryAdapter, kind: str, budget: int | None, repeat: int, v: Retrieved, vf: dict, out: list):
        key = (cid, decider.id, kind, a.name, a.config_label, budget, repeat)
        if key in present:
            return
        row = {**base, "kind": kind, "system": a.name, "config_label": a.config_label, "budget": budget,
               "repeat": repeat, **vf}
        out.append((row, DecisionInput(case_id=cid, query_text=case.query_text, records_text=v.text,
                                       record_ids=list(v.record_ids), repeat=repeat, track=track)))

    full = plan.full
    if any(k not in present for k in plan.full_keys(cid)):
        records = records_of(case)
        try:
            v = _fetch_view(full, case, records, None, health, usage, proxy)
        except CapReached:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded in health.json, case skipped
            health.error(full, cid, exc)
            return [], [cell_name(full.name, full.config_label, None)]
        health.ok(full)
        vf = _view_fields(v)
        add(full, "gate", None, 0, v, vf, pending)
        for r in range(1, plan.repeats + 1):
            add(full, "floor", None, r, v, vf, pending)

    for a in plan.adapters:
        mine: list[tuple[dict, DecisionInput]] = []
        if store is not None and getattr(a, "needs_proxy", False):
            if all(k in present for b in plan.budgets for k in plan.view_keys(cid, a, b)):
                continue
            line = store.get(cid, a.name, a.config_label)
            if line is None:  # the stage recorded the error in health.json
                failed.append(a.name)
                continue
            stored = ViewStore.retrieved(line)
            for b in plan.budgets:
                if all(k in present for k in plan.view_keys(cid, a, b)):
                    continue
                v = enforce_budget(stored, b, reference=a.reference)
                vf = _view_fields(v)
                for r in range(1, plan.repeats + 1):
                    add(a, "view", b, r, v, vf, mine)
            pending.extend(mine)
            continue
        try:
            for b in plan.budgets:
                if all(k in present for k in plan.view_keys(cid, a, b)):
                    continue
                if records is None:
                    records = records_of(case)
                v = _fetch_view(a, case, records, b, health, usage, proxy)
                vf = _view_fields(v)
                for r in range(1, plan.repeats + 1):
                    add(a, "view", b, r, v, vf, mine)
        except CapReached:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded in health.json, this adapter skipped for the case
            health.error(a, cid, exc)
            failed.append(a.name)
            continue
        if mine:
            health.ok(a)
        pending.extend(mine)

    contexts = [{k: row[k] for k in ("kind", "system", "config_label", "budget")} for row, _ in pending]
    actions = decide_all(decider, [inp for _, inp in pending], contexts, executor)
    return [{**row, "action": act} for (row, _), act in zip(pending, actions)], failed


def _incomplete(plan: _Plan, present: set[tuple]) -> list[dict]:
    out: dict[str, list[str]] = {}
    full = plan.full
    for c in plan.cases:
        cid = c.case_id
        if any(k not in present for k in plan.full_keys(cid)):
            out.setdefault(cell_name(full.name, full.config_label, None), []).append(cid)
        for a in plan.adapters:
            for b in plan.budgets:
                if any(k not in present for k in plan.view_keys(cid, a, b)):
                    out.setdefault(cell_name(a.name, a.config_label, b), []).append(cid)
    return [{"cell": k, "missing_cases": v} for k, v in out.items()]


def _cases_done(plan: _Plan, present: set[tuple]) -> dict[tuple[str, str], int]:
    done: dict[tuple[str, str], int] = {}
    full = plan.full
    for c in plan.cases:
        cid = c.case_id
        k = (full.name, full.config_label)
        done[k] = done.get(k, 0) + all(x in present for x in plan.full_keys(cid))
        for a in plan.adapters:
            k = (a.name, a.config_label)
            done[k] = done.get(k, 0) + all(x in present for b in plan.budgets for x in plan.view_keys(cid, a, b))
    return done


def _spend(config: RunConfig, decider: Decider, adapters: list[MemoryAdapter], usage: dict,
           gateway: Gateway | None = None, proxy: ProductProxy | None = None) -> dict:
    """From the gateway's meter when there is one; all zero for mock deciders and builtin adapters. With a
    product proxy, by_adapter[name].memory_side is the proxy's measured spend, total_usd includes it and
    memory_side_usd is the memory side's total. Without one the shape is unchanged."""
    out = _decider_spend(config, decider, adapters, usage, gateway)
    if proxy is not None:
        mem = proxy.spend()
        zero = {"calls": 0, "cached_calls": 0, "embed_calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        for a in adapters:
            b = out["by_adapter"].setdefault(a.name, {"usd": 0.0, "calls": 0, "cached_calls": 0, "input_tokens": 0,
                                                      "output_tokens": 0})
            b["memory_side"] = dict(mem.get(a.name, zero))
        out["memory_side_usd"] = proxy.spent_usd()
        out["total_usd"] = round(out["total_usd"] + out["memory_side_usd"], 6)
    if config.ledger is not None:
        out = stamp_spend(out, config.ledger, run_cap_usd=float(out.get("cap_usd", config.cap_usd)))
    return out


def _decider_spend(config: RunConfig, decider: Decider, adapters: list[MemoryAdapter], usage: dict,
                   gateway: Gateway | None = None) -> dict:
    if gateway is not None:
        out = gateway.spend()
        out["by_decider"].setdefault(decider.id, {"usd": 0.0, "calls": 0, "cached_calls": 0, "input_tokens": 0,
                                                  "output_tokens": 0})
        for a in adapters:  # memory-side usage the adapter reported (not metered in dollars here)
            u = usage.get(a.name, {})
            b = out["by_adapter"].setdefault(a.name, {"usd": 0.0, "calls": 0, "cached_calls": 0,
                                                      "input_tokens": 0, "output_tokens": 0})
            b["memory_side"] = {k: u.get(k, 0) for k in ("model_calls", "embed_calls", "input_tokens",
                                                         "output_tokens")}
        return out
    zero = {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}
    by_adapter = {}
    for a in adapters:
        u = usage.get(a.name, {})
        by_adapter[a.name] = {"usd": 0.0, "calls": u.get("model_calls", 0) + u.get("embed_calls", 0),
                              "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
    calls = sum(v["calls"] for v in by_adapter.values())
    return {"cap_usd": float(config.cap_usd), "total_usd": 0.0, "calls": calls,
            "input_tokens": sum(v["input_tokens"] for v in by_adapter.values()),
            "output_tokens": sum(v["output_tokens"] for v in by_adapter.values()),
            "by_stage": {k: dict(zero) for k in ("gate", "floor", "view")},
            "by_decider": {decider.id: dict(zero)}, "by_adapter": by_adapter}


def run(config: RunConfig) -> dict:
    """Run (or resume) one run directory. Returns the final manifest."""
    check_ingest_workers(config.ingest_workers)  # before any file
    products = [s for s in config.adapters if needs_proxy(s)]
    if products and config.memory_side is None:  # before any file or call
        names = [spec_name(s) for s in products]
        raise RunError(f"product adapters {names} need the product proxy; pass a memory-side config "
                       f"(bench run --memory-side CONFIG)")
    if config.ledger is not None:
        validate_ledger(config.ledger)
    ms_cfg = load_memory_side(config.memory_side) if config.memory_side is not None else None
    grade, protocol = _grade(config)  # before any file or call; a failed check raises ProtocolError
    run_dir = Path(config.out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    cases, cases_path = _load_cases(config)
    full = get_adapter(FULL)
    budgets = sorted({int(b) for b in config.budgets})
    if not budgets or budgets[0] <= 0:
        raise RunError(f"budgets must be positive integers, got {config.budgets}")
    if config.repeats < 1:
        raise RunError(f"repeats must be at least 1, got {config.repeats}")
    parallel = check_parallel(config.parallel)
    decider = config.decider
    run_id = config.run_id or run_dir.resolve().name
    gateway = config.gateway if config.gateway is not None else getattr(decider, "gateway", None)
    own = getattr(decider, "gateway", None)
    if own is not None and own is not gateway:
        raise RunError("the decider's gateway and RunConfig.gateway differ; pass one gateway")
    cap_usd = float(gateway.cap_usd) if gateway is not None else float(config.cap_usd)
    proxy: ProductProxy | None = None
    if products:
        allow = bool(config.allow_unpriced or (gateway is not None and gateway.allow_unpriced))
        cache = config.proxy_cache or (gateway.cache_path if gateway is not None else DEFAULT_PATH)
        proxy_opts = dict(config.proxy_options)
        if "opener" not in proxy_opts and needs_zen_opener(ms_cfg):
            proxy_opts["opener"] = zen_urllib_opener
        proxy = ProductProxy(ms_cfg, run_dir=run_dir, cache_path=cache, cap_usd=cap_usd, allow_unpriced=allow,
                             **proxy_opts)
    try:
        if proxy is not None:
            proxy.start()
        adapters = _build_adapters(config.adapters, proxy)
        return _run(config, run_dir, cases, cases_path, adapters, full, budgets, parallel, decider, run_id,
                    gateway, cap_usd, proxy, grade, protocol)
    finally:
        if proxy is not None:
            if gateway is not None:
                gateway.extra_spent = _zero_spent  # the gateway may serve a later run without this proxy
            proxy.stop()


def _zero_spent() -> float:
    return 0.0


def _grade(config: RunConfig) -> tuple[str, dict | None]:
    """The run grade and the manifest's protocol entry (docs/freeze.md section 4). Without a protocol every run
    is exploratory; with one, the four checks run and a failure raises ProtocolError (no silent downgrade)."""
    if config.protocol is None:
        if config.grade != "exploratory":
            raise RunError(f"grade {config.grade!r} needs a protocol (bench run --protocol PATH)")
        return "exploratory", None
    cases, _ = _load_cases(config)
    res = check_run(config.protocol, decider_id=config.decider.id, adapters=list(config.adapters),
                    adapter_files=config.adapter_files, budgets=config.budgets, repeats=config.repeats,
                    boot_seed=config.boot_seed, boot_reps=config.boot_reps, memory_side=config.memory_side,
                    cases_sha256=F.cases_sha256(cases), is_product=needs_proxy)
    return res["grade"], res["protocol"]


def _run(config: RunConfig, run_dir: Path, cases: list[Case], cases_path: str | None,
         adapters: list[MemoryAdapter], full: MemoryAdapter, budgets: list[int], parallel: int, decider: Decider,
         run_id: str, gateway: Gateway | None, cap_usd: float, proxy: ProductProxy | None,
         grade: str = "exploratory", protocol: dict | None = None) -> dict:
    m_path, x_path = run_dir / F.MANIFEST, run_dir / F.METRICS
    track = config.track
    if track == "account_review" and cases and getattr(cases[0], "track", "account_review") != "account_review":
        track = cases[0].track
    d_spec = decider_spec(decider)
    oc_bin_ver = check_opencode_binary_version(decider)
    if oc_bin_ver is not None:
        d_spec["opencode_binary_version"] = oc_bin_ver
    meter_step = getattr(decider, "meter_step", None)
    if meter_step is None:
        meter_step = getattr(getattr(decider, "transport", None), "meter_step", None)
    if meter_step is not None:
        d_spec["meter_step"] = int(meter_step)
    new = {
        "run_id": run_id, "grade": grade, "protocol": protocol, "track": track,
        "cases": {"path": cases_path, "sha256": F.cases_sha256(cases), "n": len(cases)},
        "deciders": [d_spec],
        "adapters": [_adapter_entry(a) for a in [full, *adapters]],
        "budgets": budgets, "repeats": config.repeats, "parallel": parallel,
        "bootstrap": {"seed": config.boot_seed, "reps": config.boot_reps},
        "cap_usd": cap_usd,
        "memory_side": memory_side_spec(proxy.cfg) if proxy is not None else None,
        "code": {"version": __version__, "git_commit": _git_commit()},
        "_dir": str(run_dir),
    }
    if meter_step is not None:
        new["meter_step"] = int(meter_step)
    if config.ledger is not None:
        new["ledger"] = config.ledger
    old = F.read_json(m_path) if m_path.exists() else None
    if old is None and x_path.exists():
        raise RunError(f"{x_path} exists without a manifest.json; refusing to resume")
    if old is not None:
        _check_resume(old, new)
    del new["_dir"]
    manifest = {**new, "created": (old or {}).get("created") or F.now(), "started": F.now(), "finished": None,
                "state": "running", "error": None, "incomplete_cells": [], "files": {},
                "attempts": int((old or {}).get("attempts", 0)) + 1}
    F.write_json(m_path, manifest)
    if gateway is not None:
        gateway.open_run(run_dir)
    if gateway is not None and proxy is not None:  # one cap across the decider and the memory side
        gateway.extra_spent = lambda: proxy.spent_usd() + proxy.reserved_usd()
        proxy.spent_elsewhere = lambda: gateway.spent_usd() + gateway.reserved_usd()

    plan = _Plan(cases, decider.id, full, adapters, budgets, config.repeats)
    health = _Health([full, *adapters])
    usage: dict = {}
    present: set[tuple] = set()
    executor = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="decide") if parallel > 1 else None
    # (spec, run adapter) of each product adapter, config order; the stage builds its own instances from the spec
    products = [(s, a) for s, a in zip(config.adapters, adapters) if getattr(a, "needs_proxy", False)]
    store: ViewStore | None = None
    if products and proxy is not None:
        entries = {(e["name"], e["config_label"]): e for e in manifest["adapters"]}
        expect = {(a.name, a.config_label): (entries[(a.name, a.config_label)]["config_sha256"],
                                             entries[(a.name, a.config_label)]["product_version"])
                  for _, a in products}
        store = ViewStore(views_path(run_dir), expect, proxy.cfg["id"])
    try:
        existing = F.read_metrics(x_path, repair=True)
        for r in existing:
            k = F.row_key(r)
            if k in present:
                raise RunError(f"metrics.jsonl has a duplicate row key {k}")
            plan.sort_key(r)  # every row must belong to this plan
            present.add(k)

        if store is not None:
            run_adapters = [a for _, a in products]
            case_ids = [c.case_id for c in cases]

            def product_usage() -> None:  # spend.json counts the stored lines this run uses, reused ones included
                usage.update(store.usage_by_name(case_ids, run_adapters))

            def progress() -> None:  # after every finished item, so a long ingest can be watched
                F.append_jsonl(run_dir / F.TIMINGS, health.take_timings())
                product_usage()
                F.write_json(run_dir / F.SPEND, _spend(config, decider, adapters, usage, gateway, proxy))
                F.write_json(run_dir / F.HEALTH, health.report(run_id, _cases_done(plan, present)))

            def done(cid: str, a: MemoryAdapter) -> bool:  # every metric row of the pair is already written
                return all(k in present for b in plan.budgets for k in plan.view_keys(cid, a, b))

            try:
                view_stage(cases, products, proxy, store, health, config.ingest_workers, budget=max(budgets),
                           make_adapter=lambda spec: _build_adapters([spec], proxy)[0], done=done,
                           progress=progress, expect=expect)
            finally:
                product_usage()

        for case in cases:
            if all(k in present for k in plan.all_keys(case.case_id)):
                continue
            rows, _failed = _run_case(case, plan, decider, present, track, health, usage, executor, proxy,
                                      store)
            F.append_rows(x_path, rows)
            F.append_jsonl(run_dir / F.TIMINGS, health.take_timings())
            present.update(F.row_key(r) for r in rows)

        rows = F.read_metrics(x_path)
        ordered = sorted(rows, key=plan.sort_key)
        if ordered != rows:
            F.write_text(x_path, "".join(F.row_line(r) + "\n" for r in ordered))
        F.write_json(run_dir / F.SUMMARY, {**summarise(ordered, seed=config.boot_seed, reps=config.boot_reps),
                                           "grade": manifest["grade"]})
        F.write_json(run_dir / F.SPEND, _spend(config, decider, adapters, usage, gateway, proxy))
        F.write_json(run_dir / F.HEALTH, health.report(run_id, _cases_done(plan, present)))
        (run_dir / F.TIMINGS).touch()
        extra_sealed: list[str] = []
        if views_path(run_dir).exists() and (run_dir / MEMORY_CALLS).exists():
            from .audit import VIEW_AUDIT, audit_views

            audit_views(run_dir, write=True, seal_manifest=False)
            extra_sealed.append(VIEW_AUDIT)
        names = [*F.RESULT_FILES, *(n for n in (CALLS, MEMORY_CALLS) if (run_dir / n).exists()), *extra_sealed]
        manifest.update(state="complete", finished=F.now(), incomplete_cells=_incomplete(plan, present),
                        files={n: F.sha256_file(run_dir / n) for n in names})
        F.write_json(m_path, manifest)
        return manifest
    except CapReached as exc:
        # Rows of finished cases are on disk; the stopped case's calls are cached, so a resume re-asks
        # them for free; finished product views are in the store. No summary: the run is incomplete.
        F.append_jsonl(run_dir / F.TIMINGS, health.take_timings())
        F.write_json(run_dir / F.SPEND, _spend(config, decider, adapters, usage, gateway, proxy))
        F.write_json(run_dir / F.HEALTH, health.report(run_id, _cases_done(plan, present)))
        manifest.update(state="stopped_at_cap", finished=F.now(), error=f"CapReached: {exc}",
                        incomplete_cells=_incomplete(plan, present))
        F.write_json(m_path, manifest)
        return manifest
    except BaseException as exc:
        try:
            F.append_jsonl(run_dir / F.TIMINGS, health.take_timings())
            F.write_json(run_dir / F.HEALTH, health.report(run_id, _cases_done(plan, present)))
            if gateway is not None or proxy is not None:  # the dollars already spent stay visible after a failure
                F.write_json(run_dir / F.SPEND, _spend(config, decider, adapters, usage, gateway, proxy))
        finally:
            manifest.update(state="failed", finished=F.now(), error=f"{type(exc).__name__}: {exc}",
                            incomplete_cells=_incomplete(plan, present))
            F.write_json(m_path, manifest)
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        for a in [full, *adapters]:
            try:
                a.close()
            except Exception:  # noqa: BLE001 - closing must not mask the run's own outcome
                pass


def rebuild_summary(run_dir: str | Path) -> dict:
    """Recompute summary.json from metrics.jsonl alone, with the bootstrap seed and reps in the manifest."""
    run_dir = Path(run_dir)
    m_path = run_dir / F.MANIFEST
    manifest = F.read_json(m_path)
    boot = manifest.get("bootstrap") or {}
    summary = {**summarise(F.read_metrics(run_dir / F.METRICS), seed=boot.get("seed", 11), reps=boot.get("reps", 1000)),
               "grade": manifest.get("grade") or "exploratory"}
    F.write_json(run_dir / F.SUMMARY, summary)
    if manifest.get("files"):
        manifest["files"][F.SUMMARY] = F.sha256_file(run_dir / F.SUMMARY)
        F.write_json(m_path, manifest)
    return summary


__all__ = ["RunConfig", "RunError", "ResumeMismatch", "ProtocolError", "run", "needs_proxy", "spec_name", "rebuild_summary", "decide_all",
           "decide_one",
           "gather", "check_parallel", "MAX_PARALLEL", "DEFAULT_CONFIG_LABEL"]
