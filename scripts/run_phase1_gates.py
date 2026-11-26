#!/usr/bin/env python3
"""Sequential Phase 1 60-case full-history validity gate runner (Step 5 OpenCode meter + A1 ledgers).

Launch via Varma's Keychain wrapper (never reads any key):
    with-opencode-zen uv run --no-sync python scripts/run_phase1_gates.py --added

Enforces:
- Strictly frozen `OpenCodeCliTransport` / `AnthropicCommandTransport` wrapped with the Step 5
  metering module (`engine/opencode_meter.py`), tools off (`tools: {"*": false}`).
  Never uses `engine/opencode_runner.py` for any counted run.
- `opencode --version` == 2.3.0 (aborts immediately on mismatch).
- Two-ledger cap enforcement (`engine/ledgers.py`):
  - `sonnet` ledger: cap_usd=20, stop_usd=18
  - `main` ledger:   cap_usd=60, stop_usd=55
- One model at a time, parallel=1.
- Writes `validity.json` (with `meter_step`, `tokens_breakdown` including reasoning/cache tokens),
  `decisions.jsonl`, and `spend.json` (with `ledger`, `cap_usd`, `stop_usd`) per model under
  `results/v2/validity/chat_agent/<decider_id>/`.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from memory_bench.cases.schema import read_jsonl
from memory_bench.deciders.transports import TransportError
from memory_bench.engine import files as F
from memory_bench.engine.ledgers import ledger_for_decider, run_headroom_usd, stamp_spend, sum_ledger_spend
from memory_bench.engine.opencode_meter import load_step5_decider
from memory_bench.engine.run import check_opencode_binary_version
from memory_bench.engine.validity import DEFAULT_MAX_COOLDOWN_S, VALIDITY_JSON, run_validity, validity_sample
from memory_bench.gateway import Gateway

PAID_OPENCODE_CONFIGS = [
    "configs/v2/deciders/opencode-deepseek-v4.1-flash.json",
    "configs/v2/deciders/opencode-glm-5.3-flash.json",
    "configs/v2/deciders/opencode-qwen3.8-flash.json",
]

STEP7_ADDED_CONFIGS = [
    "configs/v2/deciders/opencode-minimax-m3.json",
    "configs/v2/deciders/opencode-mistral-large-4.json",
    "configs/v2/deciders/sonnet-5-5.json",
]

FREE_OPENCODE_CONFIGS = [
    "configs/v2/deciders/opencode-beam-free.json",
    "configs/v2/deciders/opencode-big-pickle.json",
    "configs/v2/deciders/opencode-deepseek-v4.1-flash-free.json",
    "configs/v2/deciders/opencode-flint-40b-free.json",
    "configs/v2/deciders/opencode-hy4-flash-free.json",
    "configs/v2/deciders/opencode-iceman-free.json",
    "configs/v2/deciders/opencode-mimo-v2.6-flash-free.json",
    "configs/v2/deciders/opencode-nemotron-3.5-lightning-free.json",
    "configs/v2/deciders/opencode-nemotron-3.5-super-vl-free.json",
    "configs/v2/deciders/opencode-tidepool-alpha-free.json",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run sequential 60-case Phase 1 gates on Step 5 meter.")
    ap.add_argument("--paid", action="store_true", help="Run the 3 Step 3 paid OpenCode models")
    ap.add_argument("--added", action="store_true", help="Run the 3 Step 7 added deciders (MiniMax, Mistral, Sonnet)")
    ap.add_argument("--cases", default="data/v2/chat_agent/dev_cases.jsonl")
    ap.add_argument("--n-per-mechanism", type=int, default=10)
    ap.add_argument("--cache", default="results/v2/cache/calls.sqlite")
    ap.add_argument("--results-root", default="results/v2")
    ap.add_argument("--out-root", default="results/v2/validity/chat_agent")
    ap.add_argument("--max-run-usd", type=float, default=5.0, help="Per-gate safety ceiling below ledger stop line")
    ap.add_argument("--max-cooldown-s", type=float, default=DEFAULT_MAX_COOLDOWN_S)
    ap.add_argument("--models", default="", help="Optional comma list of config IDs to run")
    args = ap.parse_args(argv)

    all_cases = list(read_jsonl(args.cases))
    sample = validity_sample(all_cases, args.n_per_mechanism)
    want_ids = {x.strip() for x in args.models.split(",") if x.strip()}
    if args.added:
        configs = STEP7_ADDED_CONFIGS
    elif args.paid:
        configs = PAID_OPENCODE_CONFIGS
    else:
        configs = FREE_OPENCODE_CONFIGS

    summary_rows: list[dict] = []
    for cfg_path in configs:
        cfg_obj = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        did = cfg_obj["id"]
        if want_ids and did not in want_ids:
            continue
        out_dir = Path(args.out_root) / did
        existing = out_dir / VALIDITY_JSON
        if existing.is_file():
            prev = F.read_json(existing)
            if prev.get("complete") is True and prev.get("n") == len(sample):
                acc = (prev.get("accuracy") or {}).get("overall", {}).get("accuracy")
                print(
                    f"[SKIP complete] {did}: {prev['n']}/{prev['n_planned']} "
                    f"acc={100 * acc:.1f}% passes={prev['gate']['passes']} usd=${prev.get('usd', 0.0):.6f}",
                    flush=True,
                )
                summary_rows.append(prev)
                continue

        ledger = ledger_for_decider(did)
        price = cfg_obj.get("price") or {}
        is_free = (float(price.get("input_per_m", 0.0)) == 0.0 and float(price.get("output_per_m", 0.0)) == 0.0)
        if is_free:
            cap = 0.0
        else:
            ledger_state = sum_ledger_spend(args.results_root, ledger)
            if ledger_state["at_or_past_stop"]:
                print(
                    f"[STOP] ledger {ledger!r} already at/past stop line "
                    f"(${ledger_state['effective_usd']:.4f} / ${ledger_state['stop_usd']:.2f}); skipping {did}",
                    flush=True,
                )
                continue
            cap = run_headroom_usd(args.results_root, ledger, run_dir=out_dir, max_run_usd=args.max_run_usd)

        print(
            f"[START] {did} ({cfg_obj['model']}, ledger={ledger}, run_cap=${cap:.4f}, step5_meter) -> {out_dir}",
            flush=True,
        )
        t0 = time.monotonic()
        with Gateway(args.cache, cap_usd=cap) as gw:
            dec = load_step5_decider(cfg_path, gw)
            ver = check_opencode_binary_version(dec)
            if ver is not None:
                print(f"  verified opencode binary version: {ver} (Step 5 meter over frozen transports.py, tools off)", flush=True)
            else:
                print(f"  transport={cfg_obj['transport']} (Step 5 meter)", flush=True)
            try:
                res = run_validity(
                    dec,
                    sample,
                    out_dir,
                    gateway=gw,
                    parallel=1,
                    max_cooldown_s=args.max_cooldown_s,
                    ledger=ledger,
                )
            except TransportError as exc:
                out_dir.mkdir(parents=True, exist_ok=True)
                err_res = {
                    "schema": "memory_bench.validity/1",
                    "complete": False,
                    "stop_reason": f"TransportError: {exc}",
                    "decider": {"id": did, "model": cfg_obj["model"], "opencode_binary_version": ver, "meter_step": 5},
                    "meter_step": 5,
                    "ledger": ledger,
                    "n": 0,
                    "n_planned": len(sample),
                    "accuracy": {"overall": {"correct": 0, "n": 0, "accuracy": None}, "per_mechanism": {}},
                    "parse_errors": 0,
                    "context_truncations": 0,
                    "rate_limit_429_count": 0,
                    "cooldown_seconds_total": 0.0,
                    "wall_seconds_total": round(time.monotonic() - t0, 3),
                    "opencode_binary_version": ver,
                    "gate": {"passes": False, "overall_min": 0.9, "per_mechanism_min": 0.8},
                }
                F.write_json(out_dir / VALIDITY_JSON, err_res)
                F.write_json(out_dir / F.SPEND, stamp_spend(gw.spend(), ledger, run_cap_usd=cap))
                print(f"[ERROR] {did}: {exc}", flush=True)
                summary_rows.append(err_res)
                continue

        wall = round(time.monotonic() - t0, 1)
        acc = (res.get("accuracy") or {}).get("overall", {}).get("accuracy")
        acc_s = "-" if acc is None else f"{100 * acc:.1f}%"
        tok = res.get("tokens") or {}
        print(
            f"[DONE] {did}: complete={res['complete']} n={res['n']}/{res['n_planned']} "
            f"acc={acc_s} passes={res['gate']['passes']} parse_err={res['parse_errors']} "
            f"trunc={res.get('context_truncations', 0)} 429s={res.get('rate_limit_429_count', 0)} "
            f"in={tok.get('input', 0)} (cr={tok.get('cache_read', 0)}, cw={tok.get('cache_write', 0)}) "
            f"out={tok.get('output', 0)} (reas={tok.get('reasoning', 0)}) "
            f"usd=${res.get('usd', 0.0):.6f} wall={wall}s stop={res.get('stop_reason')}",
            flush=True,
        )
        summary_rows.append(res)

    print(f"Finished {len(summary_rows)} model(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
