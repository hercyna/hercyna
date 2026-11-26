"""20-case JSON Extraction Fidelity Probe for shared memory-side extractors (spec §4.5, R3 A4).

Samples 20 constraint-bearing cases (5 each of H1, H2, H3, HU2) from `chat_agent` dev cases
using a `secrets`-drawn logged seed (R3 A6), chunks each case's rendered records into chunks of
size 25 (`chunk_rule = 25`), and verifies whether the candidate extractor model emits valid JSON
retaining every decisive (`d_star`) constraint with zero dropped constraints.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from ..adapters.base import records_of
from ..cases.schema import Case
from ..deciders.model import ModelDecider, decider_spec
from ..deciders.transports import RateLimitError
from ..gateway.meter import CapReached, Gateway
from . import files as F
from .run import check_opencode_binary_version
from .seeds import draw_seed

log = logging.getLogger("memory_bench.extractor_probe")

PROBE_SCHEMA = "memory_bench.extractor_probe/1"
PROBE_JSON = "extractor_probe.json"
PROBE_ROWS = "probe_rows.jsonl"
CHUNK_SIZE = 25
PROBE_MECHANISMS = ("H1", "H2", "H3", "HU2")
DEFAULT_MAX_COOLDOWN_S = 21600.0

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_EXTRACTOR_SYSTEM = """You are a structured memory extractor for a multi-session workspace assistant.
Read the chunk of 25 conversation records below and extract EVERY active or notable user preference,
preference override, time-bound permission/approval (including expiration date), standing prohibition/boundary,
and standing rule.

Do NOT omit any preference override, permission expiration, standing prohibition, or standing rule.
Ignore routine status/checkpoint chatter unless it sets or changes a rule, preference, permission, or boundary.

Reply with a single JSON object only:
{
  "constraints": [
    {
      "turn_id": "turn_042",
      "slot": "report_format",
      "kind": "preference_set | preference_override | permission_grant | boundary_prohibit | standing_rule",
      "summary": "brief statement including any expiration date or replaced value"
    }
  ]
}"""


def sample_probe_cases(cases: Sequence[Case], seed: int, n_per_mech: int = 5) -> list[Case]:
    """Stratified 20-case sample (5 each of H1, H2, H3, HU2) using the logged seed."""
    rng = random.Random(f"extractor_probe|{seed}")
    out: list[Case] = []
    for mech in PROBE_MECHANISMS:
        pool = [c for c in cases if c.mechanism == mech and c.d_star]
        if len(pool) < n_per_mech:
            raise ValueError(f"need at least {n_per_mech} {mech} cases with d_star, got {len(pool)}")
        picked = list(pool)
        rng.shuffle(picked)
        out.extend(picked[:n_per_mech])
    return out


def _parse_constraints_json(raw: str) -> list[dict[str, Any]] | None:
    text = raw.strip()
    if not text:
        return None
    candidates = [text]
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        candidates.append(text[s : e + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("constraints"), list):
            return [x for x in obj["constraints"] if isinstance(x, dict)]
    return None


def _matches_event(item: dict[str, Any], ev_id: str, slot: str, ev_fields: dict[str, Any]) -> bool:
    tid = str(item.get("turn_id") or "").strip()
    if tid == ev_id:
        return True
    blob = json.dumps(item, sort_keys=True).lower()
    if ev_id.lower() in blob:
        return True
    item_slot = str(item.get("slot") or "").strip().lower()
    if item_slot == slot.lower() or slot.lower() in blob:
        # Check for decisive value / expiry / prohibition cue
        for key in ("value", "rule_value", "expires_on", "replaces"):
            v = str(ev_fields.get(key) or "").strip().lower()
            if v and v in blob:
                return True
        if ev_fields.get("active") is True and ("prohibit" in blob or "never" in blob or "boundary" in blob):
            return True
    return False


def run_extractor_probe(
    decider: ModelDecider,
    cases: Sequence[Case],
    out_dir: str | Path,
    gateway: Gateway | None = None,
    *,
    seed: int | None = None,
    seed_log: str | Path = "results/v2/seed_draws.jsonl",
    chunk_size: int = CHUNK_SIZE,
    max_cooldown_s: float = DEFAULT_MAX_COOLDOWN_S,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Run the 20-case chunk-25 JSON Extraction Fidelity Probe (spec §4.5)."""
    oc_bin_ver = check_opencode_binary_version(decider)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gateway = gateway if gateway is not None else getattr(decider, "gateway", None)
    if gateway is not None:
        gateway.open_run(out)

    used_seed = seed if seed is not None else draw_seed(
        f"a4_extractor_fidelity_probe_{decider.id}",
        log_path=seed_log,
        extra={"decider": decider.id, "n_cases": 20, "chunk_size": chunk_size},
    )
    sample = sample_probe_cases(cases, used_seed, n_per_mech=5)
    sleep_fn = sleep or time.sleep

    rows: list[dict[str, Any]] = []
    wall_total = 0.0
    rate_limit_429_count = 0
    cooldown_seconds_total = 0.0
    complete = True
    stop_reason: str | None = None

    for case in sample:
        recs = records_of(case)
        ev_map = {e.id: e for e in case.events}
        d_set = set(case.d_star)
        target_slot = str((case.metadata or {}).get("slot") or "")

        # Evaluate every 25-record chunk that contains at least one decisive turn in d_star
        for c_idx in range(0, len(recs), chunk_size):
            chunk = recs[c_idx : c_idx + chunk_size]
            chunk_d = [r.id for r in chunk if r.id in d_set]
            if not chunk_d:
                continue
            chunk_text = "\n".join(r.text for r in chunk)
            msgs = [
                {"role": "system", "content": _EXTRACTOR_SYSTEM},
                {"role": "user", "content": f"Records chunk ({len(chunk)} turns):\n{chunk_text}\n\nReturn JSON."},
            ]
            ctx = {
                "kind": "extractor_probe",
                "system": "extractor_probe",
                "config_label": f"chunk{chunk_size}",
                "budget": chunk_size,
                "case_id": f"{case.case_id}_c{c_idx // chunk_size}",
                "track": case.track,
            }
            stopped = False
            while True:
                t0 = time.perf_counter()
                try:
                    reply = gateway.call(decider.cfg, decider.transport, msgs, repeat=0, context=ctx)
                    secs = time.perf_counter() - t0
                    wall_total += secs
                    break
                except CapReached as exc:
                    complete, stop_reason = False, f"CapReached: {exc}"
                    stopped = True
                    break
                except RateLimitError as exc:
                    rate_limit_429_count += 1
                    wait_s = (float(exc.retry_after_ms) / 1000.0 + 2.0) if exc.retry_after_ms else 60.0
                    if cooldown_seconds_total + wait_s > max_cooldown_s:
                        complete = False
                        stop_reason = f"probe not completed (rate limit): cooldown exceeded {max_cooldown_s:.0f}s"
                        stopped = True
                        break
                    log.warning("extractor_probe %s: HTTP 429; sleeping %.1fs", decider.id, wait_s)
                    sleep_fn(wait_s)
                    cooldown_seconds_total += wait_s
            if stopped:
                break

            parsed = _parse_constraints_json(reply.text)
            json_ok = parsed is not None
            retained = 0
            if parsed is not None:
                for did in chunk_d:
                    ev = ev_map[did]
                    if any(_matches_event(item, did, target_slot, ev.fields) for item in parsed):
                        retained += 1
            dropped = len(chunk_d) - retained
            rows.append({
                "case_id": case.case_id,
                "mechanism": case.mechanism,
                "chunk_index": c_idx // chunk_size,
                "chunk_size": len(chunk),
                "decisive_in_chunk": len(chunk_d),
                "decisive_retained": retained,
                "dropped_constraints": dropped,
                "json_ok": json_ok,
                "extracted_count": len(parsed) if parsed is not None else 0,
                "seconds": round(secs, 4),
            })
        if not complete:
            break

    total_chunks = len(rows)
    json_ok_chunks = sum(1 for r in rows if r["json_ok"])
    total_decisive = sum(r["decisive_in_chunk"] for r in rows)
    total_retained = sum(r["decisive_retained"] for r in rows)
    total_dropped = sum(r["dropped_constraints"] for r in rows)

    by_mech: dict[str, dict[str, Any]] = {}
    for mech in PROBE_MECHANISMS:
        mrows = [r for r in rows if r["mechanism"] == mech]
        md = sum(r["decisive_in_chunk"] for r in mrows)
        mr = sum(r["decisive_retained"] for r in mrows)
        by_mech[mech] = {
            "chunks": len(mrows),
            "json_ok": sum(1 for r in mrows if r["json_ok"]),
            "decisive_total": md,
            "decisive_retained": mr,
            "dropped_constraints": md - mr,
            "retention_rate": (mr / md) if md else None,
        }

    d_spec = decider_spec(decider)
    if oc_bin_ver is not None:
        d_spec["opencode_binary_version"] = oc_bin_ver

    sp = gateway.spend() if gateway is not None else None
    result: dict[str, Any] = {
        "schema": PROBE_SCHEMA,
        "complete": complete,
        "stop_reason": stop_reason,
        "seed": used_seed,
        "chunk_size": chunk_size,
        "n_cases_planned": len(sample),
        "n_cases_evaluated": len({r["case_id"] for r in rows}),
        "decider": d_spec,
        "chunks_tested": total_chunks,
        "json_parse_ok": json_ok_chunks,
        "json_parse_rate": (json_ok_chunks / total_chunks) if total_chunks else None,
        "decisive_total": total_decisive,
        "decisive_retained": total_retained,
        "dropped_constraints": total_dropped,
        "retention_rate": (total_retained / total_decisive) if total_decisive else None,
        "passes_zero_drop": bool(complete and total_chunks > 0 and json_ok_chunks == total_chunks and total_dropped == 0),
        "by_mechanism": by_mech,
        "wall_seconds_total": round(wall_total, 4),
        "rate_limit_429_count": rate_limit_429_count,
        "cooldown_seconds_total": round(cooldown_seconds_total, 3),
        "usd": sp["total_usd"] if sp is not None else 0.0,
    }
    if oc_bin_ver is not None:
        result["opencode_binary_version"] = oc_bin_ver

    F.write_text(out / PROBE_ROWS, "".join(F.row_line(r) + "\n" for r in rows))
    F.write_json(out / PROBE_JSON, result)
    if sp is not None:
        F.write_json(out / F.SPEND, sp)
    return result


__all__ = ["run_extractor_probe", "sample_probe_cases", "PROBE_SCHEMA", "PROBE_JSON", "PROBE_ROWS"]
