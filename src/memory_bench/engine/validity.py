"""Validity run (docs/deciders.md section 5): one gate decision per case on the full history.

The sample is the first n cases of each mechanism in file order. Outputs in the out directory:
`validity.json` (schema memory_bench.validity/1) and `decisions.jsonl` (one text-free row per case).
With a gateway the decider's calls are cached, so a rerun in the same folder costs nothing for
finished cases. A CapReached stops cleanly with `"complete": false`.

With `parallel` > 1 the gate calls of several cases are in flight at once (full views are built on the
main thread). On a CapReached the result keeps the cases before the first stopped one, in sample order,
as a sequential run would; later calls that finished are cached for the rerun.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from ..adapters import get_adapter
from ..adapters.base import enforce_budget, records_of
from ..cases.schema import MECHANISMS, Case
from ..deciders.base import PARSE_ERROR, Decider, DecisionInput
from ..deciders.model import decider_spec
from ..gateway.meter import CapReached
from ..scoring.gates import gate_report
from . import files as F
from .run import check_parallel, decide_one, gather

SCHEMA = "memory_bench.validity/1"
VALIDITY_JSON = "validity.json"
DECISIONS = "decisions.jsonl"


def validity_sample(cases: Sequence[Case], n_per_mechanism: int = 10) -> list[Case]:
    """First n cases of each mechanism in file order; mechanisms in the order M0, M1, M2, M3, U1, U2."""
    out: list[Case] = []
    for mech in MECHANISMS:
        out.extend([c for c in cases if c.mechanism == mech][: max(0, int(n_per_mechanism))])
    return out


def _full_view(case: Case):
    full = get_adapter("full")
    try:
        full.reset(case.case_id)
        full.ingest(records_of(case))
        got = full.retrieve(case.query_text, 0)
        return enforce_budget(got, 0, reference=full.reference), full
    finally:
        full.close()


def _acc(correct: int, n: int) -> dict:
    return {"correct": correct, "n": n, "accuracy": (correct / n) if n else None}


def _timed_decide(decider: Decider, inp: DecisionInput, ctx: dict) -> tuple[str, float]:
    t0 = time.perf_counter()
    action = decide_one(decider, inp, ctx)
    return action, time.perf_counter() - t0


def run_validity(decider: Decider, cases: Sequence[Case], out_dir: str | Path, gateway=None,
                 parallel: int = 1) -> dict:
    parallel = check_parallel(parallel)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gateway = gateway if gateway is not None else getattr(decider, "gateway", None)
    if gateway is not None:
        gateway.open_run(out)
    cases = list(cases)
    rows: list[dict] = []
    wall: list[float] = []
    complete = True
    stop_reason = None

    def prepare(case: Case) -> tuple[DecisionInput, dict]:
        view, full = _full_view(case)
        inp = DecisionInput(case_id=case.case_id, query_text=case.query_text, records_text=view.text,
                            record_ids=list(view.record_ids), repeat=0)
        return inp, {"kind": "gate", "system": "full", "config_label": full.config_label, "budget": None}

    def keep(case: Case, action: str, seconds: float) -> None:
        wall.append(seconds)
        rows.append({"case_id": case.case_id, "mechanism": case.mechanism, "a_star": case.a_star,
                     "action": action, "correct": action == case.a_star})

    if parallel == 1:
        for case in cases:
            inp, ctx = prepare(case)
            try:
                action, secs = _timed_decide(decider, inp, ctx)
            except CapReached as exc:
                complete, stop_reason = False, f"CapReached: {exc}"
                break
            keep(case, action, secs)
    else:
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="validity") as pool:
            futures = [pool.submit(_timed_decide, decider, *prepare(case)) for case in cases]
            try:
                results = gather(futures)
            except CapReached as exc:  # gather raised it only because no other error happened
                results, complete, stop_reason = None, False, f"CapReached: {exc}"
        if results is None:  # keep the finished prefix in sample order
            for case, f in zip(cases, futures):
                if f.cancelled() or f.exception() is not None:
                    break
                keep(case, *f.result())
        else:
            for case, (action, secs) in zip(cases, results):
                keep(case, action, secs)

    per: dict[str, dict] = {}
    for mech in MECHANISMS:
        mine = [r for r in rows if r["mechanism"] == mech]
        if mine or any(c.mechanism == mech for c in cases):
            per[mech] = _acc(sum(r["correct"] for r in mine), len(mine))
    overall = _acc(sum(r["correct"] for r in rows), len(rows))
    confusion: dict[str, dict[str, int]] = {}
    for r in rows:
        row = confusion.setdefault(r["a_star"], {})
        row[r["action"]] = row.get(r["action"], 0) + 1
    gate = gate_report(overall["accuracy"], {m: v["accuracy"] for m, v in per.items()})
    if not complete:
        gate["passes"] = False  # a partial run never passes the gate

    result: dict = {
        "schema": SCHEMA, "complete": complete, "stop_reason": stop_reason,
        "decider": decider_spec(decider), "n": len(rows), "n_planned": len(cases),
        "accuracy": {"overall": overall, "per_mechanism": per},
        "confusion": confusion,
        "parse_errors": sum(r["action"] == PARSE_ERROR for r in rows),
        "gate": gate,
        "tokens": None, "usd": None, "seconds_per_call": None,
    }
    if gateway is not None:
        sp = gateway.spend()
        result["tokens"] = {"input": sp["input_tokens"], "output": sp["output_tokens"],
                            "cached_calls": sp["cached_calls"], "calls": sp["calls"]}
        result["usd"] = sp["total_usd"]
        secs = [x["seconds"] for x in gateway.lines]
        result["seconds_per_call"] = round(sum(secs) / len(secs), 6) if secs else None
    elif wall:
        result["seconds_per_call"] = round(sum(wall) / len(wall), 6)

    F.write_text(out / DECISIONS, "".join(F.row_line(r) + "\n" for r in rows))
    F.write_json(out / VALIDITY_JSON, result)
    return result


__all__ = ["validity_sample", "run_validity", "SCHEMA"]
