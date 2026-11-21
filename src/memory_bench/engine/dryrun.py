"""Dry run (docs/deciders.md section 6): every prompt a run would send, counted, with no model call.

`dry_run(config)` takes a dict with the inputs of a run:
    cases      list[Case]
    decider    decider config path or dict (read with load_config; no transport is built)
    adapters   names or instances, without "full" (default ["bm25", "recent"])
    budgets    token budgets (default [1000])
    repeats    R (default 3)
Input tokens are `estimate_tokens` over each message's content. Likely output tokens per call are the
config's `assumed_output_tokens` (default 1500); the upper bound is `params.max_tokens` (the assumed
value when the config has none). The reminder retry is not counted.

A product adapter (needs_proxy) is not built and makes no call: its memory-side dollars are not estimated
(the printout says so) and its decider view prompts are counted on the full view cut to the budget with the
shared rule, the most a product view can hold.
"""

from __future__ import annotations

from typing import Any

from ..adapters import get_adapter
from ..adapters.base import enforce_budget, records_of
from ..deciders.base import DecisionInput
from ..deciders.model import load_config
from ..deciders.prompts import build_prompt
from ..deciders.transports import estimate_tokens
from .run import FULL, RunError, _build_adapters, needs_proxy, spec_name

DEFAULT_ASSUMED_OUTPUT = 1500
STAGES = ("gate", "floor", "view")


def _stage() -> dict[str, Any]:
    return {"calls": 0, "input_tokens": 0, "output_tokens_likely": 0, "output_tokens_upper": 0,
            "usd_likely": None, "usd_upper": None, "unpriced": False}


def _usd(price: dict | None, tin: int, tout: int) -> float | None:
    if price is None:
        return None
    return round(tin * price["input_per_m"] / 1e6 + tout * price["output_per_m"] / 1e6, 6)


def dry_run(config: dict[str, Any]) -> dict[str, Any]:
    cases = list(config["cases"])
    if not cases:
        raise RunError("no cases")
    cfg = load_config(config["decider"])
    specs = list(config.get("adapters") or ["bm25", "recent"])
    products = [s for s in specs if needs_proxy(s)]
    adapters = _build_adapters([s for s in specs if not needs_proxy(s)]) if len(products) < len(specs) else []
    product_names = [spec_name(s) for s in products]
    budgets = sorted({int(b) for b in (config.get("budgets") or [1000])})
    if budgets[0] <= 0:
        raise RunError(f"budgets must be positive integers, got {budgets}")
    repeats = int(config.get("repeats", 3))
    if repeats < 1:
        raise RunError(f"repeats must be at least 1, got {repeats}")

    assumed = int(cfg.get("assumed_output_tokens", DEFAULT_ASSUMED_OUTPUT))
    upper = int(cfg["params"].get("max_tokens") or assumed)
    price = cfg.get("price")
    version = cfg["prompt_version"]
    stages = {s: _stage() for s in STAGES}

    def count(stage: str, case, text: str, repeat: int) -> None:
        msgs = build_prompt(DecisionInput(case_id=case.case_id, query_text=case.query_text, records_text=text,
                                          record_ids=[], repeat=repeat), version)
        st = stages[stage]
        st["calls"] += 1
        st["input_tokens"] += sum(estimate_tokens(m["content"]) for m in msgs)
        st["output_tokens_likely"] += assumed
        st["output_tokens_upper"] += upper

    full = get_adapter(FULL)
    try:
        for case in cases:
            records = records_of(case)
            full.reset(case.case_id)
            full.ingest(records)
            view = enforce_budget(full.retrieve(case.query_text, 0), 0, reference=full.reference)
            for r in range(0, repeats + 1):  # repeat 0 is the gate, 1..R the floor
                count("gate" if r == 0 else "floor", case, view.text, r)
            for a in adapters:
                for b in budgets:
                    a.reset(case.case_id)
                    a.ingest(records)
                    got = enforce_budget(a.retrieve(case.query_text, b), b, reference=a.reference)
                    for r in range(1, repeats + 1):
                        count("view", case, got.text, r)
            for _p in products:
                for b in budgets:
                    cut = enforce_budget(view, b)
                    for r in range(1, repeats + 1):
                        count("view", case, cut.text, r)
    finally:
        for a in [full, *adapters]:
            try:
                a.close()
            except Exception:  # noqa: BLE001
                pass

    total = _stage()
    for st in stages.values():
        for k in ("calls", "input_tokens", "output_tokens_likely", "output_tokens_upper"):
            total[k] += st[k]
    for st in (*stages.values(), total):
        st["usd_likely"] = _usd(price, st["input_tokens"], st["output_tokens_likely"])
        st["usd_upper"] = _usd(price, st["input_tokens"], st["output_tokens_upper"])
        st["unpriced"] = price is None
    return {"decider": cfg["id"], "n_cases": len(cases), "adapters": [a.name for a in adapters] + product_names,
            "products": product_names, "memory_side": "not estimated" if products else None,
            "budgets": budgets, "repeats": repeats, "assumed_output_tokens": assumed, "max_tokens": upper,
            "price": price, "unpriced": price is None, "stages": stages, "total": total}


def _money(x: float | None) -> str:
    return "unpriced" if x is None else f"${x:,.4f}"


def format_dry_run(result: dict[str, Any]) -> str:
    cols = ("stage", "calls", "input tok", "out tok (likely)", "out tok (max)", "usd likely", "usd upper")
    lines = [cols]
    for name, st in [*result["stages"].items(), ("total", result["total"])]:
        lines.append((name, f"{st['calls']:,}", f"{st['input_tokens']:,}", f"{st['output_tokens_likely']:,}",
                      f"{st['output_tokens_upper']:,}", _money(st["usd_likely"]), _money(st["usd_upper"])))
    widths = [max(len(r[i]) for r in lines) for i in range(len(cols))]
    body = ["  ".join(x.ljust(w) if i == 0 else x.rjust(w) for i, (x, w) in enumerate(zip(r, widths))).rstrip()
            for r in lines]
    head = (f"dry run: decider {result['decider']}, {result['n_cases']} cases, adapters "
            f"{','.join(result['adapters'])}, budgets {','.join(map(str, result['budgets']))}, "
            f"repeats {result['repeats']}; no model call made")
    out = [head, *body]
    if result["unpriced"]:
        out.append("the decider has no price: dollars are unknown")
    if result.get("products"):
        out.append("memory side: not estimated, run a five-case probe")
    return "\n".join(out)


__all__ = ["dry_run", "format_dry_run"]
