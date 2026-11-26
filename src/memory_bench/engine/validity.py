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

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Sequence

from ..adapters import get_adapter
from ..adapters.base import enforce_budget, records_of
from ..cases.schema import MECHANISMS, Case
from ..deciders.base import PARSE_ERROR, Decider, DecisionInput
from ..deciders.model import decider_spec
from ..deciders.transports import ContextOverflowError, RateLimitError
from ..gateway.meter import CapReached
from ..scoring.gates import gate_report
from . import files as F
from .ledgers import stamp_spend, validate_ledger
from .run import check_opencode_binary_version, check_parallel, decide_one, gather

log = logging.getLogger("memory_bench.validity")
SCHEMA = "memory_bench.validity/1"
VALIDITY_JSON = "validity.json"
DECISIONS = "decisions.jsonl"
DEFAULT_MAX_COOLDOWN_S = 21600.0  # 6 hours cumulative 429 cooldown ceiling per model (R3 A2)


def _mechanisms_for(cases: Sequence[Case]) -> tuple[str, ...]:
    if cases:
        track = getattr(cases[0], "track", "account_review")
        if track != "account_review":
            from ..tracks import get_track

            return get_track(track).meta.mechanisms
    return MECHANISMS


def validity_sample(cases: Sequence[Case], n_per_mechanism: int = 10) -> list[Case]:
    """First n cases of each mechanism in file order, ordered by the track's canonical mechanisms."""
    out: list[Case] = []
    for mech in _mechanisms_for(cases):
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
                 parallel: int = 1, *, max_cooldown_s: float = DEFAULT_MAX_COOLDOWN_S,
                 sleep: Callable[[float], None] | None = None, ledger: str | None = None) -> dict:
    if ledger is not None:
        validate_ledger(ledger)
    parallel = check_parallel(parallel)
    oc_bin_ver = check_opencode_binary_version(decider)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gateway = gateway if gateway is not None else getattr(decider, "gateway", None)
    if gateway is not None:
        gateway.open_run(out)
    cases = list(cases)
    is_v2_track = bool(cases and getattr(cases[0], "track", "account_review") != "account_review")
    rows: list[dict] = []
    wall: list[float] = []
    complete = True
    stop_reason = None
    context_truncations = 0
    rate_limit_429_count = 0
    cooldown_seconds_total = 0.0
    sleep_fn = sleep or time.sleep

    def prepare(case: Case) -> tuple[DecisionInput, dict]:
        view, full = _full_view(case)
        inp = DecisionInput(case_id=case.case_id, query_text=case.query_text, records_text=view.text,
                            record_ids=list(view.record_ids), repeat=0, track=getattr(case, "track", "account_review"))
        return inp, {"kind": "gate", "system": "full", "config_label": full.config_label, "budget": None}

    def keep(case: Case, action: str, seconds: float) -> None:
        wall.append(seconds)
        rows.append({"case_id": case.case_id, "mechanism": case.mechanism, "a_star": case.a_star,
                     "action": action, "correct": action == case.a_star})

    def _build_result(done_flag: bool, reason: str | None) -> dict:
        per: dict[str, dict] = {}
        for mech in _mechanisms_for(cases):
            mine = [r for r in rows if r["mechanism"] == mech]
            if mine or any(c.mechanism == mech for c in cases):
                per[mech] = _acc(sum(r["correct"] for r in mine), len(mine))
        overall = _acc(sum(r["correct"] for r in rows), len(rows))
        confusion: dict[str, dict[str, int]] = {}
        for r in rows:
            row = confusion.setdefault(r["a_star"], {})
            row[r["action"]] = row.get(r["action"], 0) + 1
        gate = gate_report(overall["accuracy"], {m: v["accuracy"] for m, v in per.items()})
        if not done_flag or context_truncations > 0:
            gate["passes"] = False  # a partial or truncated run never passes the gate

        d_spec = decider_spec(decider)
        if oc_bin_ver is not None:
            d_spec["opencode_binary_version"] = oc_bin_ver
        meter_step = getattr(decider, "meter_step", None)
        if meter_step is None:
            meter_step = getattr(getattr(decider, "transport", None), "meter_step", None)
        if meter_step is not None:
            d_spec["meter_step"] = int(meter_step)

        result: dict = {
            "schema": SCHEMA, "complete": done_flag, "stop_reason": reason,
            "decider": d_spec, "n": len(rows), "n_planned": len(cases),
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
        tb_fn = getattr(getattr(decider, "transport", None), "token_breakdown", None)
        if callable(tb_fn):
            tb = tb_fn()
            if isinstance(tb, dict):
                result["tokens_breakdown"] = tb
                if isinstance(result.get("tokens"), dict):
                    result["tokens"]["reasoning"] = int(tb.get("reasoning", 0))
                    result["tokens"]["cache_read"] = int(tb.get("cache_read", 0))
                    result["tokens"]["cache_write"] = int(tb.get("cache_write", 0))
        if meter_step is not None:
            result["meter_step"] = int(meter_step)
        if ledger is not None:
            result["ledger"] = ledger
        if is_v2_track:
            result["track"] = cases[0].track
            result["parallel"] = parallel
            result["wall_seconds_total"] = round(sum(wall), 6)
            result["context_truncations"] = context_truncations
            result["rate_limit_429_count"] = rate_limit_429_count
            result["cooldown_seconds_total"] = round(cooldown_seconds_total, 3)
            if oc_bin_ver is not None:
                result["opencode_binary_version"] = oc_bin_ver
        return result

    def _flush(done_flag: bool, reason: str | None) -> dict:
        res = _build_result(done_flag, reason)
        F.write_text(out / DECISIONS, "".join(F.row_line(r) + "\n" for r in rows))
        F.write_json(out / VALIDITY_JSON, res)
        if gateway is not None:
            sp = gateway.spend()
            if ledger is not None:
                sp = stamp_spend(sp, ledger, run_cap_usd=float(sp.get("cap_usd", 0.0)))
            F.write_json(out / F.SPEND, sp)
        return res

    if parallel == 1:
        for case in cases:
            inp, ctx = prepare(case)
            stopped = False
            while True:
                try:
                    action, secs = _timed_decide(decider, inp, ctx)
                    keep(case, action, secs)
                    break
                except CapReached as exc:
                    complete, stop_reason = False, f"CapReached: {exc}"
                    stopped = True
                    break
                except ContextOverflowError:
                    context_truncations += 1
                    keep(case, "CONTEXT_OVERFLOW", 0.0)
                    break
                except RateLimitError as exc:
                    rate_limit_429_count += 1
                    wait_s = (float(exc.retry_after_ms) / 1000.0 + 2.0) if exc.retry_after_ms else 60.0
                    if cooldown_seconds_total + wait_s > max_cooldown_s:
                        complete = False
                        stop_reason = (
                            f"gate not completed (rate limit): cumulative cooldown "
                            f"{cooldown_seconds_total + wait_s:.1f}s exceeds {max_cooldown_s:.0f}s limit"
                        )
                        stopped = True
                        break
                    log.warning(
                        "validity %s: HTTP 429 on %s (count=%d); sleeping %.1fs before resume (total cooldown %.1fs)",
                        decider.id, case.case_id, rate_limit_429_count, wait_s, cooldown_seconds_total + wait_s,
                    )
                    _flush(False, f"waiting_429:{wait_s:.0f}s")
                    sleep_fn(wait_s)
                    cooldown_seconds_total += wait_s
            if stopped:
                break
            if is_v2_track:
                _flush(False, "in_progress")
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

    return _flush(complete, stop_reason)


__all__ = ["validity_sample", "run_validity", "SCHEMA"]
