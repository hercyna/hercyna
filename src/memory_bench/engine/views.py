"""The view stage and the view store (docs/view-stage.md).

Product adapters (`needs_proxy`) are ingested once per case, before the decision loop, by `view_stage`: a pool of
`ingest_workers` threads, each owning its own adapter instances built from the run's specs with the run's proxy.
One work item = reset(case_id), ingest(records), retrieve(query, max(budgets)), then one line in
`<run_dir>/private/views.jsonl`. The decision loop reads the stored view and cuts it per budget with
`enforce_budget`. Builtin adapters never come here.

The store holds view text on purpose. `private/` is never released: it is not in the manifest's `files`, not in
`RELEASED_FILES`, and the text-free scanner does not look there.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from ..adapters.base import MemoryAdapter, Retrieved, Usage, records_of
from ..cases.schema import Case
from ..gateway.meter import CapReached
from . import files as F

PRIVATE = "private"
VIEWS = "views.jsonl"
MAX_INGEST_WORKERS = 8
USAGE_KEYS = ("model_calls", "input_tokens", "output_tokens", "embed_calls")

STORED, ERROR, CAP = "stored", "error", "cap"


def views_path(run_dir: Path) -> Path:
    return Path(run_dir) / PRIVATE / VIEWS


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class ViewStore:
    """`private/views.jsonl`, append-only, one line per (case_id, system, config_label).

    `expect` maps (system, config_label) to (config_sha256, product_version) of the current run. On open the lines
    already there are loaded; a line is reused only when its config_sha256, memory_side_id and product_version match
    the current run, and the newest matching line for a key wins. A partial or unreadable line is skipped. Each new
    line is written whole and flushed under a lock. The directory is made on the first append."""

    def __init__(self, path: str | Path, expect: dict[tuple[str, str], tuple[str, str]], memory_side_id: str | None):
        self.path = Path(path)
        self.expect = dict(expect)
        self.memory_side_id = memory_side_id
        self.lock = threading.Lock()
        self._lines: dict[tuple[str, str, str], dict] = {}
        self._needs_newline = False  # the file ends in a torn line: the next append starts on a fresh line
        if self.path.exists():
            data = self.path.read_bytes()
            self._needs_newline = bool(data) and not data.endswith(b"\n")
            for raw in data.split(b"\n"):
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if self.matches(line):
                    self._lines[(line["case_id"], line["system"], line["config_label"])] = line

    def matches(self, line: Any) -> bool:
        if not isinstance(line, dict):
            return False
        want = self.expect.get((line.get("system"), line.get("config_label")))
        return (want is not None and isinstance(line.get("case_id"), str)
                and line.get("config_sha256") == want[0] and line.get("product_version") == want[1]
                and line.get("memory_side_id") == self.memory_side_id
                and isinstance(line.get("text"), str) and isinstance(line.get("units"), list)
                and isinstance(line.get("record_ids"), list) and isinstance(line.get("usage"), dict))

    def get(self, case_id: str, system: str, config_label: str) -> dict | None:
        with self.lock:
            return self._lines.get((case_id, system, config_label))

    def append(self, line: dict) -> None:
        text = json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(("\n" if self._needs_newline else "") + text)
                fh.flush()
                os.fsync(fh.fileno())
            self._needs_newline = False
            self._lines[(line["case_id"], line["system"], line["config_label"])] = line

    @staticmethod
    def retrieved(line: dict) -> Retrieved:
        """The stored view before any budget cut."""
        u = line.get("usage") or {}
        return Retrieved(text=line["text"], record_ids=[str(x) for x in line["record_ids"]],
                         units=[str(x) for x in line["units"]],
                         usage=Usage(**{k: int(u.get(k, 0)) for k in USAGE_KEYS}))

    def usage_by_name(self, case_ids: Sequence[str], adapters: Sequence[MemoryAdapter]) -> dict[str, dict[str, int]]:
        """Usage totals by adapter name over the stored lines of these cases (reused lines included)."""
        out: dict[str, dict[str, int]] = {}
        for a in adapters:
            u = out.setdefault(a.name, {k: 0 for k in USAGE_KEYS})
            for cid in case_ids:
                line = self.get(cid, a.name, a.config_label)
                if line is not None:
                    for k in USAGE_KEYS:
                        u[k] += int((line.get("usage") or {}).get(k, 0))
        return out


# ---------------------------------------------------------------------------
# Warning attribution
# ---------------------------------------------------------------------------

class ThreadWarnings(logging.Handler):
    """Counts WARNING-and-above log records by logger name for the item running on the emitting thread
    (`record.thread`), so parallel items do not count each other's warnings. A record from a thread that runs no
    item (the proxy's request threads, a product's own helper threads) goes to the one item in flight when there is
    exactly one (as the engine always did with one worker); with several in flight it cannot be attributed and is
    counted in `unattributed`. No message text is kept."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._guard = threading.Lock()
        self._active: dict[int, dict[str, int]] = {}
        self.unattributed: dict[str, int] = {}

    def enter(self, counts: dict[str, int]) -> None:
        with self._guard:
            self._active[threading.get_ident()] = counts

    def leave(self) -> None:
        with self._guard:
            self._active.pop(threading.get_ident(), None)

    def emit(self, record: logging.LogRecord) -> None:
        with self._guard:
            c = self._active.get(record.thread)
            if c is None:
                c = next(iter(self._active.values())) if len(self._active) == 1 else self.unattributed
            c[record.name] = c.get(record.name, 0) + 1


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------

def _refused(proxy: Any, a: MemoryAdapter, case_id: str) -> bool:
    """True when the proxy refused a call of this item at the cap. A proxy without the per-scope record falls
    back to its global flag (conservative: the item's view is not stored)."""
    refused = getattr(proxy, "refused", None)
    if refused is None:
        return bool(proxy.cap_reached)
    with proxy.lock:
        return (a.name, a.config_label, case_id) in refused


def _run_item(case: Case, a: MemoryAdapter, inst: MemoryAdapter, *, budget: int, proxy: Any, store: ViewStore,
              health: Any, warn: ThreadWarnings, memory_side_id: str | None,
              expect: dict[tuple[str, str], tuple[str, str]]) -> str:
    """reset, ingest, retrieve on this thread's own instance, then one store line. Returns STORED, ERROR or CAP."""
    cid = case.case_id
    seconds: dict[str, float] = {}
    seen: dict[str, int] = {}

    def call(name: str, fn: Callable, *args: Any) -> Any:
        counts: dict[str, int] = {}
        warn.enter(counts)
        t0 = time.perf_counter()
        try:
            return fn(*args)
        finally:
            dt = time.perf_counter() - t0
            warn.leave()
            health.record_call(a, cid, None, name, dt, counts)
            seconds[name] = dt
            for k, n in counts.items():
                seen[k] = seen.get(k, 0) + n

    try:
        records = records_of(case)
        call("reset", inst.reset, cid)
        call("ingest", inst.ingest, records)
        got: Retrieved = call("retrieve", inst.retrieve, case.query_text, budget)
        if _refused(proxy, a, cid):  # the product may have swallowed a refused call: its view is not whole
            return CAP
        u = got.usage
        sha, version = expect[(a.name, a.config_label)]
        line = {"case_id": cid, "system": a.name, "config_label": a.config_label, "config_sha256": sha,
                "memory_side_id": memory_side_id, "product_version": version,
                "text": str(got.text), "units": [str(x) for x in got.units],
                "record_ids": [str(x) for x in got.record_ids],
                "usage": {k: int(getattr(u, k, 0)) for k in USAGE_KEYS},
                "ingest_seconds": round(seconds.get("ingest", 0.0), 6),
                "retrieve_seconds": round(seconds.get("retrieve", 0.0), 6),
                "warnings": {"total": sum(seen.values()), "by_logger": dict(sorted(seen.items()))},
                "at": F.now()}
    except Exception as exc:  # noqa: BLE001 - recorded in health.json; this pair has no view
        if proxy.cap_reached or _refused(proxy, a, cid):
            return CAP
        health.error(a, cid, exc)
        return ERROR
    store.append(line)
    health.ok(a)
    return STORED


def view_stage(cases: Sequence[Case], products: Sequence[tuple[Any, MemoryAdapter]], proxy: Any, store: ViewStore,
               health: Any, workers: int, *, budget: int, make_adapter: Callable[[Any], MemoryAdapter],
               done: Callable[[str, MemoryAdapter], bool], progress: Callable[[], None],
               expect: dict[tuple[str, str], tuple[str, str]]) -> dict[str, int]:
    """Compute and store every missing product view (docs/view-stage.md section 2.2).

    products: (spec, run adapter) pairs in config order; the run adapter names the pair for health and the store,
    each worker builds its own instance from the spec with `make_adapter`. Work items are (case, product) pairs
    with no reusable store line, in case order, skipping pairs whose metric rows are all present (`done`). With
    one worker the stage runs on the calling thread. A product error is recorded and that pair has no view. At
    the cap no new item starts, items in flight finish or fail, and `CapReached` is raised once they have.
    `progress` runs after every finished item (under the stage's lock)."""
    items = [(c, spec, a) for c in cases for spec, a in products
             if not done(c.case_id, a) and store.get(c.case_id, a.name, a.config_label) is None]
    stats = {"items": len(items), STORED: 0, ERROR: 0, CAP: 0, "not_started": 0}
    if not items:
        return stats
    workers = max(1, min(int(workers), len(items)))
    qlock, io_lock = threading.Lock(), threading.Lock()
    state = {"next": 0, "stop": False}
    fatal: list[BaseException] = []
    warn = ThreadWarnings()
    memory_side_id = store.memory_side_id

    def worker() -> None:
        own: dict[tuple[str, str], MemoryAdapter] = {}  # this thread's instances; never shared
        try:
            while True:
                with qlock:
                    if state["stop"] or proxy.cap_reached or state["next"] >= len(items):
                        return
                    case, spec, a = items[state["next"]]
                    state["next"] += 1
                k = (a.name, a.config_label)
                if k not in own:
                    own[k] = make_adapter(spec)
                out = _run_item(case, a, own[k], budget=budget, proxy=proxy, store=store, health=health, warn=warn,
                                memory_side_id=memory_side_id, expect=expect)
                with io_lock:
                    stats[out] += 1
                    progress()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            with qlock:
                fatal.append(exc)
                state["stop"] = True
        finally:
            for inst in own.values():
                try:
                    inst.close()
                except Exception:  # noqa: BLE001 - closing must not mask the run's own outcome
                    pass

    root = logging.getLogger()
    root.addHandler(warn)
    try:
        if workers == 1:
            worker()
        else:
            threads = [threading.Thread(target=worker, name=f"ingest-{i}", daemon=True) for i in range(workers)]
            for t in threads:
                t.start()
            try:
                for t in threads:
                    t.join()
            except BaseException:
                with qlock:
                    state["stop"] = True  # no new item; items in flight finish
                for t in threads:
                    t.join()
                raise
    finally:
        root.removeHandler(warn)
        if warn.unattributed:
            health.add_unattributed(warn.unattributed)
    stats["not_started"] = len(items) - state["next"]
    if fatal:
        raise fatal[0]
    if stats[CAP] or (stats["not_started"] and proxy.cap_reached):
        raise CapReached(getattr(proxy, "cap_detail", None) or "memory-side call refused at the cap")
    return stats


__all__ = ["ViewStore", "ThreadWarnings", "view_stage", "views_path", "PRIVATE", "VIEWS", "MAX_INGEST_WORKERS"]
