"""Command line: `bench --version`, `bench cases dev`, `bench run (--mock | --decider CONFIG)` and
`bench report DIR`, `bench validity`, `bench dryrun`, `bench proxy serve`, `bench freeze`, `bench stability`
and `bench power`."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from typing import Sequence

from . import __version__
from .cases.sample import dev_cases
from .cases.schema import read_jsonl, write_jsonl


def _cases_dev(args: argparse.Namespace) -> int:
    n = write_jsonl(args.out, dev_cases())
    print(f"wrote {n} development cases to {args.out}")
    return 0


def table_cases(seed: int, n: int = 300):
    """The table cases (D7, docs/freeze.md step 2): one sample in the default mix from a fresh seed, generator
    version 3 for every mechanism, ids "t<seed>-NNNN". The seed is drawn once at the step-2 freeze."""
    from .cases.sample import make_headline_sample

    return make_headline_sample(n, seed, prefix=f"t{seed}", generator_version=3)


def _cases_table(args: argparse.Namespace) -> int:
    if args.seed == 11 or args.seed == 12:
        print("bench cases table: seeds 11 and 12 made the development cases; draw a fresh one", file=sys.stderr)
        return 2
    n = write_jsonl(args.out, table_cases(args.seed, args.n))
    print(f"wrote {n} table cases (seed {args.seed}) to {args.out}")
    return 0


def _csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _adapter_specs(text: str) -> list:
    """A comma list of adapter names and adapter config files. A name ending in .json is loaded:
    {"adapter": name, "config_label": label, "config": {...}} (configs/adapters/)."""
    import json

    out: list = []
    for item in _csv(text):
        if item.endswith(".json"):
            with open(item, encoding="utf-8") as fh:
                out.append(json.load(fh))
        else:
            out.append(item)
    return out


def _adapter_files(text: str) -> dict[str, str]:
    """System id ("mem0/k25") -> the adapter config file it was loaded from, for the protocol checks."""
    import json

    from .engine.freeze import system_id, system_of

    out: dict[str, str] = {}
    for item in _csv(text):
        if item.endswith(".json"):
            with open(item, encoding="utf-8") as fh:
                out[system_id(*system_of(json.load(fh)))] = item
    return out


EXIT_CAP = 3  # the run stopped at its dollar cap; resume with a higher --cap-usd
MAX_PARALLEL = 8


def _parallel_ok(cmd: str, n: int) -> bool:
    if 1 <= n <= MAX_PARALLEL:
        return True
    print(f"bench {cmd}: --parallel must be between 1 and {MAX_PARALLEL} (got {n}); more parallel calls risk "
          f"rate limits and make the cap reservation coarse", file=sys.stderr)
    return False


def _run(args: argparse.Namespace) -> int:
    from .deciders.mock import MockNoisyDecider, MockOracleDecider
    from .engine import ProtocolError, RunConfig, run

    if bool(args.mock) == bool(args.decider):
        print("bench run: pass exactly one of --mock and --decider CONFIG", file=sys.stderr)
        return 2
    if args.decider and args.noisy is not None:
        print("bench run: --noisy goes with --mock", file=sys.stderr)
        return 2
    if not _parallel_ok("run", args.parallel):
        return 2
    if not 1 <= args.ingest_workers <= MAX_PARALLEL:
        print(f"bench run: --ingest-workers must be between 1 and {MAX_PARALLEL} (got {args.ingest_workers})",
              file=sys.stderr)
        return 2
    adapters = _adapter_specs(args.adapters)
    if not _memory_side_ok(args, adapters):
        return 2
    cases = list(read_jsonl(args.cases)) if args.cases else dev_cases()
    if args.n is not None:
        cases = cases[: args.n]
    gateway = None
    if args.decider:
        from .engine.opencode_meter import load_step5_decider
        from .gateway import DEFAULT_PATH, Gateway, UnpricedDecider

        gateway = Gateway(args.cache or DEFAULT_PATH, cap_usd=args.cap_usd, allow_unpriced=args.allow_unpriced)
        try:
            decider = load_step5_decider(args.decider, gateway)
        except (UnpricedDecider, ValueError, OSError) as exc:
            gateway.close()
            print(f"bench run: {exc}", file=sys.stderr)
            return 2
    elif args.noisy is not None:
        decider = MockNoisyDecider(cases, flip_rate=args.noisy, seed=args.noisy_seed)
    else:
        decider = MockOracleDecider(cases)
    try:
        manifest = run(RunConfig(out_dir=args.out, cases=cases, decider=decider, adapters=adapters,
                                 budgets=[int(b) for b in _csv(args.budgets)], repeats=args.repeats,
                                 cap_usd=args.cap_usd, boot_seed=args.boot_seed, boot_reps=args.boot_reps,
                                 gateway=gateway, parallel=args.parallel, memory_side=args.memory_side,
                                 proxy_cache=args.cache, allow_unpriced=args.allow_unpriced,
                                 ingest_workers=args.ingest_workers, protocol=args.protocol,
                                 adapter_files=_adapter_files(args.adapters),
                                 ledger=getattr(args, "ledger", None)))
    except ProtocolError as exc:
        print(f"bench run: --protocol {args.protocol}: {exc}", file=sys.stderr)
        return 2
    finally:
        if gateway is not None:
            gateway.close()
    print(f"run {manifest['run_id']}: {manifest['state']}, grade {manifest['grade']}, {manifest['cases']['n']} cases, "
          f"decider {decider.id}, written to {args.out}")
    reason = (manifest.get("protocol") or {}).get("exploratory_reason")
    if reason:
        print(f"exploratory: {reason}")
    for cell in manifest["incomplete_cells"]:
        print(f"incomplete: {cell['cell']} misses {len(cell['missing_cases'])} case(s)")
    if manifest["state"] == "stopped_at_cap":
        print(f"stopped at the cap: {manifest['error']}; rerun with a higher --cap-usd to resume", file=sys.stderr)
        return EXIT_CAP
    return 0


def _memory_side_ok(args: argparse.Namespace, adapters: list[str]) -> bool:
    """Product adapters need --memory-side; a given config must load and be priced (or --allow-unpriced)."""
    from .engine.run import needs_proxy, spec_name
    from .gateway.proxy import check_priced, load_memory_side

    products = [a for a in adapters if needs_proxy(a)]
    if products and not args.memory_side:
        print(f"bench run: adapters {', '.join(spec_name(p) for p in products)} call models through the product proxy; "
              f"pass --memory-side CONFIG (configs/memory-side/)", file=sys.stderr)
        return False
    if args.memory_side:
        try:
            check_priced(load_memory_side(args.memory_side), args.allow_unpriced)
        except (ValueError, OSError) as exc:
            print(f"bench run: {exc}", file=sys.stderr)
            return False
    return True


QUICKSTART_TIME_LIMIT_S = 600
QUICKSTART_COST_LIMIT_USD = 1.0


def _quickstart(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    from .cases.quickstart import quickstart_cases
    from .deciders.mock import MockOracleDecider
    from .engine import RunConfig, run
    from .engine import files as F

    if bool(args.mock) == bool(args.decider):
        print("bench quickstart: pass exactly one of --mock and --decider CONFIG", file=sys.stderr)
        return 2
    if not _parallel_ok("quickstart", args.parallel):
        return 2
    out = args.out or f"results/runs/quickstart-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    cases = quickstart_cases()
    gateway = None
    if args.decider:
        from .engine.opencode_meter import load_step5_decider
        from .gateway import DEFAULT_PATH, Gateway, UnpricedDecider

        gateway = Gateway(args.cache or DEFAULT_PATH, cap_usd=args.cap_usd, allow_unpriced=args.allow_unpriced)
        try:
            decider = load_step5_decider(args.decider, gateway)
        except (UnpricedDecider, ValueError, OSError) as exc:
            gateway.close()
            print(f"bench quickstart: {exc}", file=sys.stderr)
            return 2
    else:
        decider = MockOracleDecider(cases)
    t0 = time.monotonic()
    try:
        manifest = run(RunConfig(out_dir=out, cases=cases, decider=decider, adapters=["bm25", "recent"],
                                 budgets=[1000], repeats=3, cap_usd=args.cap_usd, gateway=gateway,
                                 parallel=args.parallel))
    finally:
        if gateway is not None:
            gateway.close()
    seconds = time.monotonic() - t0
    spend = F.read_json(Path(out) / F.SPEND)
    total_usd = spend["total_usd"]
    cached = spend.get("cached_calls", 0)
    live = spend["calls"] - cached
    time_pass = seconds <= QUICKSTART_TIME_LIMIT_S
    cost_pass = total_usd <= QUICKSTART_COST_LIMIT_USD
    F.write_json(Path(out) / "quickstart.json", {
        "seconds": round(seconds, 3), "total_usd": total_usd, "live_calls": live, "cached_calls": cached,
        "time_limit_s": QUICKSTART_TIME_LIMIT_S, "cost_limit_usd": QUICKSTART_COST_LIMIT_USD,
        "time_pass": time_pass, "cost_pass": cost_pass, "parallel": args.parallel, "smoke_test": True})
    if manifest["state"] == "stopped_at_cap":
        print(f"stopped at the cap: {manifest['error']}; rerun with a higher --cap-usd to resume", file=sys.stderr)
        return EXIT_CAP
    print("smoke test: 20 cases check the install, time and cost. "
          "The rates below are not a result; use bench run for one.")
    _report(argparse.Namespace(dir=str(out), products=None, smoke=True))
    print(f"time: {seconds:.1f}s (limit {QUICKSTART_TIME_LIMIT_S}s) {'PASS' if time_pass else 'FAIL'}")
    print(f"cost: ${total_usd:.4f} (limit ${QUICKSTART_COST_LIMIT_USD:.2f}) {'PASS' if cost_pass else 'FAIL'}")
    print(f"calls: {live} live, {cached} cached")
    return 0


def _validity(args: argparse.Namespace) -> int:
    from .deciders.mock import MockOracleDecider
    from .engine.validity import run_validity, validity_sample

    if bool(args.mock) == bool(args.decider):
        print("bench validity: pass exactly one of --mock and --decider CONFIG", file=sys.stderr)
        return 2
    if not _parallel_ok("validity", args.parallel):
        return 2
    src_cases = list(read_jsonl(args.cases)) if getattr(args, "cases", None) else dev_cases()
    cases = validity_sample(src_cases, args.n_per_mechanism)
    gateway = None
    if args.decider:
        from .engine.opencode_meter import load_step5_decider
        from .gateway import DEFAULT_PATH, Gateway, UnpricedDecider

        gateway = Gateway(args.cache or DEFAULT_PATH, cap_usd=args.cap_usd, allow_unpriced=args.allow_unpriced)
        try:
            decider = load_step5_decider(args.decider, gateway)
        except (UnpricedDecider, ValueError, OSError) as exc:
            gateway.close()
            print(f"bench validity: {exc}", file=sys.stderr)
            return 2
    else:
        decider = MockOracleDecider(cases)
    try:
        res = run_validity(decider, cases, args.out, gateway=gateway, parallel=args.parallel,
                           ledger=getattr(args, "ledger", None))
    finally:
        if gateway is not None:
            gateway.close()
    acc = res["accuracy"]["overall"]["accuracy"]
    print(f"validity {decider.id}: {res['n']} of {res['n_planned']} cases, accuracy "
          f"{'-' if acc is None else f'{100 * acc:.1f}%'}, gate {'passes' if res['gate']['passes'] else 'fails'}, "
          f"written to {args.out}")
    for mech, v in res["accuracy"]["per_mechanism"].items():
        print(f"  {mech}: {v['correct']}/{v['n']}")
    if not res["complete"]:
        print(f"stopped at the cap: {res['stop_reason']}; rerun with a higher --cap-usd to resume", file=sys.stderr)
        return EXIT_CAP
    return 0


def _dryrun(args: argparse.Namespace) -> int:
    from .engine.dryrun import dry_run, format_dry_run

    cases = list(read_jsonl(args.cases)) if args.cases else dev_cases()
    if args.n is not None:
        cases = cases[: args.n]
    try:
        res = dry_run({"cases": cases, "decider": args.decider, "adapters": _adapter_specs(args.adapters),
                       "budgets": [int(b) for b in _csv(args.budgets)], "repeats": args.repeats})
    except (ValueError, OSError) as exc:
        print(f"bench dryrun: {exc}", file=sys.stderr)
        return 2
    print(format_dry_run(res))
    return 0


def _serve_wait(proxy, scope) -> None:
    """Block until Ctrl-C. A seam: tests replace it."""
    import threading

    threading.Event().wait()


def _proxy_serve(args: argparse.Namespace) -> int:
    from .engine.opencode_meter import needs_zen_opener, zen_urllib_opener
    from .gateway import DEFAULT_PATH, ProductProxy, load_memory_side

    try:
        cfg = load_memory_side(args.memory_side)
        opener = zen_urllib_opener if needs_zen_opener(cfg) else None
        proxy = ProductProxy(cfg, run_dir=args.log, cache_path=args.cache or DEFAULT_PATH, cap_usd=args.cap_usd,
                             allow_unpriced=args.allow_unpriced, port=args.port, opener=opener)
    except (ValueError, OSError) as exc:
        print(f"bench proxy serve: {exc}", file=sys.stderr)
        return 2
    try:
        proxy.start()
    except OSError as exc:
        proxy.stop()
        print(f"bench proxy serve: cannot bind 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    try:
        scope = proxy.scope("dev", "default", "dev")
        print(f"product proxy for memory side {cfg['id']}: chat {cfg['chat']['model']}, embeddings "
              f"{cfg['embeddings']['model']}, cap {args.cap_usd:.4f} USD")
        print(f"base_url {scope.base_url}")
        print(f"api_key {scope.api_key} (a dummy; the proxy reads the upstream key itself)")
        print("Ctrl-C stops the proxy", flush=True)
        try:
            _serve_wait(proxy, scope)
        except KeyboardInterrupt:
            pass
    finally:
        proxy.stop()
    u = scope.usage()
    print(f"stopped: {u.model_calls} chat and {u.embed_calls} embedding calls, {proxy.spent_usd():.6f} USD live"
          + ("; refused a call at the cap" if proxy.cap_reached else ""))
    return 0


def _pct(stat: dict | None) -> str:
    if not stat or stat.get("value") is None:
        return "-"
    out = f"{100 * stat['value']:.1f}%"
    ci = stat.get("ci95")
    if ci:
        out += f" [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"
    return out


def _report(args: argparse.Namespace) -> int:
    from .engine import rebuild_summary

    summary = rebuild_summary(args.dir)
    print(f"grade {summary.get('grade', 'exploratory')}")
    cols = ("cell", "wrong commit", "floor", "headline", "abstention", "right action")
    for did, d in summary["deciders"].items():
        v = d["validity"]
        acc = v["overall"]["accuracy"]
        print(f"decider {did}")
        head = (f"n_cases {d['n_cases']}  n_gated {d['n_gated']}  gate accuracy "
                f"{'-' if acc is None else f'{100 * acc:.1f}%'}")
        if not getattr(args, "smoke", False):
            head += f"  validity {'passes' if v['passes'] else 'fails'}"
        print(head)
        floor_wc = d["floor"]["wrong_commit"] if d["floor"] else None
        lines = [cols]
        for name, c in d["cells"].items():
            lines.append((name, _pct(c["wrong_commit"]), _pct(floor_wc), _pct(c["headline"]),
                          _pct(c["abstention"]), _pct(c["right_action"])))
        widths = [max(len(row[i]) for row in lines) for i in range(len(cols))]
        for row in lines:
            print("  ".join(x.ljust(w) for x, w in zip(row, widths)).rstrip())
        print()
    if args.products is not None:
        _print_separation(args.dir, _csv(args.products))
    return 0


def _print_separation(run_dir: str, systems: list[str]) -> None:
    from pathlib import Path

    from .engine import files as F
    from .scoring import separation

    d = Path(run_dir)
    rows = F.read_metrics(d / F.METRICS)
    boot = (F.read_json(d / F.MANIFEST).get("bootstrap") or {}) if (d / F.MANIFEST).exists() else {}
    products = sorted({(r["system"], r["config_label"], r["budget"]) for r in rows
                       if r["kind"] == "view" and r["system"] in systems},
                      key=lambda c: (systems.index(c[0]), c[1], c[2]))
    print(f"separation (CHARTER section 4.6), products: {', '.join(systems) or '-'}")
    if not products:
        print("  no view cells of these systems in the run")
        return
    res = separation(rows, products, seed=boot.get("seed", 11), reps=boot.get("reps", 1000))
    rule = res["rule"]
    print(f"  alpha {rule['alpha']}  correction {rule['correction']}  seed {rule['seed']}  reps {rule['reps']}")
    for did, dd in res["deciders"].items():
        print(f"decider {did}")
        lines = [("from floor", "headline", "p", "separates")]
        for name, x in dd["from_floor"].items():
            lines.append((name, "-", "-", "-") if x is None else
                         (name, _pct(x), "-" if x["p"] is None else f"{x['p']:.4f}", "yes" if x["separates"] else "no"))
        lines.append(("pair", "difference", "holm p", "separates"))
        for name, x in dd["pairs"].items():
            hp = "-" if x["holm_p"] is None else f"{x['holm_p']:.4f}"
            lines.append((name, _pct(x), hp, f"yes ({x['direction']})" if x["separates"] else "no"))
        widths = [max(len(r[i]) for r in lines) for i in range(4)]
        for r in lines:
            print("  " + "  ".join(x.ljust(w) for x, w in zip(r, widths)).rstrip())
    lb = res["leaderboard"]
    print(f"leaderboard: {'-' if lb is None else ('yes' if lb else 'no')}  verdict: {res['verdict']}")
    print()


def _freeze(args: argparse.Namespace) -> int:
    """bench freeze --step N | --check (docs/freeze.md section 3)."""
    from .engine.freeze import EXIT_MISMATCH, FreezeError, check_protocol, freeze_step

    if args.check:
        try:
            bad = check_protocol(args.protocol)
        except (OSError, ValueError) as exc:
            print(f"bench freeze --check: {exc}", file=sys.stderr)
            return 2
        for m in bad:
            print(m.line())
        if bad:
            print(f"{len(bad)} mismatch(es) against {args.protocol}", file=sys.stderr)
            return EXIT_MISMATCH
        print(f"all hashes match {args.protocol}")
        return 0
    try:
        out = freeze_step(args.step, args.spec, args.out)
    except (FreezeError, OSError, ValueError) as exc:
        print(f"bench freeze --step {args.step}: refused: {exc}", file=sys.stderr)
        return 2
    st = out["steps"][str(args.step)]
    print(f"froze step {args.step} into {args.out}: {len(st['files'])} files, step_sha256 {st['step_sha256']}, "
          f"protocol_sha256 {out['protocol_sha256']}, commit {st['git_commit']}")
    return 0


def _run_rows(cmd: str, run_dir: str) -> list[dict] | None:
    from pathlib import Path

    from .engine import files as F

    p = Path(run_dir) / F.METRICS
    if not p.exists():
        print(f"bench {cmd}: {p} does not exist", file=sys.stderr)
        return None
    return F.read_metrics(p)


def _table(lines: list[tuple], indent: str = "  ") -> None:
    widths = [max(len(r[i]) for r in lines) for i in range(len(lines[0]))]
    for r in lines:
        print(indent + "  ".join(x.ljust(w) for x, w in zip(r, widths)).rstrip())


def _stability(args: argparse.Namespace) -> int:
    """bench stability RUN: repeat stability per decider and system (docs/freeze.md section 5)."""
    from pathlib import Path

    from .engine import files as F
    from .scoring.stability import repeat_stability

    rows = _run_rows("stability", args.dir)
    if rows is None:
        return 2
    res = repeat_stability(rows)
    F.write_json(Path(args.dir) / F.STABILITY, res)
    for did, d in res["deciders"].items():
        print(f"decider {did}  n_cases {d['n_cases']}  n_gated {d['n_gated']}")
        lines = [("system", "gated", "identical", "share", "later differ")]
        for name, x in d["systems"].items():
            lines.append((name, str(x["gated_cases"]), str(x["identical_cases"]), _share(x["identical_share"]),
                          f"{x['later_differ']}/{x['later_repeats']} ({_share(x['later_differ_share'])})"))
        _table(lines)
    print(f"written to {Path(args.dir) / F.STABILITY}")
    return 0


def _share(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _pts(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.2f}"


def _power(args: argparse.Namespace) -> int:
    """bench power RUN: SE and minimum detectable difference, normal approximation (docs/freeze.md section 6)."""
    from pathlib import Path

    from .engine import files as F
    from .scoring.power import power_note

    rows = _run_rows("power", args.dir)
    if rows is None:
        return 2
    try:
        res = power_note(rows, pairs=args.pairs, power=args.power)
    except ValueError as exc:
        print(f"bench power: {exc}", file=sys.stderr)
        return 2
    F.write_json(Path(args.dir) / F.POWER, res)
    print(f"power note, normal approximation (not a bootstrap): power {res['power']}, alpha {res['alpha']}, "
          f"Holm worst case alpha {res['alpha']}/{res['pairs_k']}; values in points")
    for did, d in res["deciders"].items():
        print(f"decider {did}  n_gated {d['n_gated']}")
        lines = [("headline", "n", "mean", "se", "mde", "mde holm")]
        for name, x in d["systems"].items():
            lines.append((name, "-", "-", "-", "-", "-") if x is None else
                         (name, str(x["n"]), _pts(x["mean"]), _pts(x["se"]), _pts(x["mde"]), _pts(x["mde_holm"])))
        lines.append(("pair difference", "n", "mean", "se", "mde", "mde holm"))
        for x in d["pairs"].values():
            lines.append((f"{x['a']} - {x['b']}", str(x["n"]), _pts(x["mean"]), _pts(x["se"]), _pts(x["mde"]), _pts(x["mde_holm"])))
        _table(lines)
    print(f"written to {Path(args.dir) / F.POWER}")
    return 0


def _hub_build(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .hub.build import build

    root = Path(args.root)
    out = Path(args.out) if args.out else root / "hub" / "dist"
    try:
        res = build(root, out, built_at=args.built_at)
    except FileNotFoundError as exc:
        print(f"bench hub build: {exc}", file=sys.stderr)
        return 2
    for name, size in res["bytes"].items():
        print(f"wrote {Path(res['out']) / name} ({size} bytes)")
    return 0


def _submit_pack(args: argparse.Namespace) -> int:
    """bench submit pack RUN --out DIR (docs/submission.md section 2)."""
    from .submit.pack import PackError, pack

    try:
        dest = pack(args.run, args.out, slug=args.slug, exploratory=args.exploratory)
    except (PackError, OSError, ValueError) as exc:
        print(f"bench submit pack: refused: {exc}", file=sys.stderr)
        return 2
    print(f"packed {args.run} into {dest}; fill submitter and organisation in submission.json, then run "
          f"bench verify {dest}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    """bench verify DIR (docs/submission.md section 3). Exit 0 when every check passes, 5 otherwise."""
    from .submit.verify import EXIT_FAIL, verify

    res = verify(args.dir, protocol=args.protocol, cases=args.cases, runs=args.runs)
    for f in res.failures:
        print(f.line())
    print(res.verdict())
    return 0 if res.ok else EXIT_FAIL


def _verdict(args: argparse.Namespace) -> int:
    """Pool the table's runs and apply the frozen separation rule (src/memory_bench/engine/pool.py)."""
    import json

    from pathlib import Path

    from .engine import files as F
    from .engine.pool import PoolError, verdict

    try:
        res = verdict(args.runs, seed=args.boot_seed, reps=args.boot_reps, require_table=not args.exploratory)
    except PoolError as exc:
        print(f"bench verdict: refused: {exc}", file=sys.stderr)
        return 2
    if args.out:
        F.write_json(Path(args.out), res)
    sep = res["separation"]
    print(f"pool: {len(res['pool']['runs'])} runs, grade {res['pool']['grade']}, "
          f"protocol {str(res['pool']['protocol_sha256'])[:8]}, deciders {', '.join(res['pool']['deciders'])}")
    sv = res["pool"].get("shared_views")
    print(f"shared views: {'-' if sv is None else ('yes' if sv else 'no')} "
          f"({res['pool'].get('shared_view_cells', 0)} cells checked, {res['pool'].get('differing_view_cells', 0)} differ)")
    for did, dd in sep["deciders"].items():
        print(f"decider {did}")
        for name, x in dd["from_floor"].items():
            print(f"  from floor  {name:28s} " + ("-" if x is None or x.get("value") is None else
                  f"{x['value']*100:+.1f} pts [{x['ci95'][0]*100:.1f}, {x['ci95'][1]*100:.1f}]  "
                  f"{'separates' if x['separates'] else 'does not separate'}"))
        for name, x in dd["pairs"].items():
            hp = "-" if x["holm_p"] is None else f"{x['holm_p']:.4f}"
            print(f"  pair        {name:40s} holm p {hp}  " + (f"separates ({x['direction']})" if x["separates"] else "no"))
    print(f"verdict: {sep['verdict']}" + (f"  pairs: {json.dumps(sep.get('separating_pairs') or [])}" if sep.get("separating_pairs") else ""))
    return 0


def _vendor_pack(args: argparse.Namespace) -> int:
    from .release.packs import PackError, write_packs

    try:
        packs = write_packs(args.runs, args.out, verdict_path=args.verdict, root=args.root)
    except (PackError, OSError, ValueError, KeyError) as exc:
        print(f"bench vendor pack: refused: {exc}", file=sys.stderr)
        return 2
    for system, path in packs.items():
        print(f"{system}: {path}")
    return 0


def _health_rebuild(args: argparse.Namespace) -> int:
    """bench health rebuild RUN: health.json from every attempt in timings.jsonl (src/memory_bench/engine/health.py)."""
    from pathlib import Path

    from .engine.health import rebuild_health

    run = Path(args.run)
    if not (run / "manifest.json").is_file():
        print(f"bench health rebuild: {run} has no manifest.json", file=sys.stderr)
        return 2
    rep = rebuild_health(run)
    for a in rep["adapters"]:
        w = a["product_warnings"]
        by = ", ".join(f"{k} {v}" for k, v in w["by_logger"].items()) or "-"
        att = a.get("attempts")
        print(f"{a['name']}/{a['config_label']:10s} cases {a['cases_done']}  errors {a['errors']}  "
              f"ingest {a['ingest_seconds_mean']}s  retrieve {a['retrieve_seconds_mean']}s  "
              f"warnings {w['total']} ({by})"
              + (f"  items {att['items']}, views {att['stored_views']}" if att else ""))
    print(f"wrote {run / 'health.json'} from {rep['rebuilt']['timing_rows']} timing rows")
    return 0


def _audit_views(args: argparse.Namespace) -> int:
    """bench audit views RUN: stored views against failed memory-side calls (src/memory_bench/engine/audit.py)."""
    from .engine.audit import AuditError, audit_views

    try:
        res = audit_views(args.run, margin=args.margin)
    except AuditError as exc:
        print(f"bench audit views: {exc}", file=sys.stderr)
        return 2
    print(f"views checked {res['views_checked']}, with a failed call inside the window {res['views_with_failed_call']}, "
          f"failed calls {res['failed_calls']} {res['failed_calls_by_status']}, "
          f"cases with failed calls and no view {res['cases_failed_without_view']}")
    for name, s in res["systems"].items():
        print(f"  {name}: views {s['views_checked']}, flagged {s['views_with_failed_call']}, "
              f"calls in windows {s['failed_calls_in_window']}, failed {s['failed_calls']} {s['failed_calls_by_status']}")
    return 0


def _sensitivity(args: argparse.Namespace) -> int:
    """bench sensitivity RUNS --seeds ... : the separation rule per bootstrap seed. Exploratory."""
    from pathlib import Path

    from .engine import files as F
    from .engine.pool import PoolError
    from .engine.sensitivity import sensitivity, table

    try:
        seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    except ValueError:
        print("bench sensitivity: --seeds takes a comma list of integers", file=sys.stderr)
        return 2
    try:
        res = sensitivity(args.runs, seeds, reps=args.boot_reps, require_table=not args.exploratory)
    except PoolError as exc:
        print(f"bench sensitivity: refused: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else (Path("results") / "verdicts" / f"{args.name}-sensitivity.json"
                                           if args.name else None)
    for line in table(res):
        print(line)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        F.write_json(out, res)
        print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="A decision-scored bench for agent memory products")
    p.add_argument("--version", action="version", version=f"bench {__version__}")
    sub = p.add_subparsers(dest="command")
    cases = sub.add_parser("cases", help="case sets")
    cs = cases.add_subparsers(dest="cases_command", required=True)
    d = cs.add_parser("dev", help="regenerate the 300 development cases")
    d.add_argument("--out", required=True, help="path of the cases.jsonl to write")
    d.set_defaults(func=_cases_dev)
    t = cs.add_parser("table", help="generate the table cases from the seed drawn at the step-2 freeze")
    t.add_argument("--seed", type=int, required=True, help="the table seed (never 11 or 12)")
    t.add_argument("--n", type=int, default=300)
    t.add_argument("--out", required=True, help="path of the cases.jsonl to write")
    t.set_defaults(func=_cases_table)

    r = sub.add_parser("run", help="run gate, floor and view decisions into a run directory")
    r.add_argument("--mock", action="store_true", help="use the mock oracle decider (no network)")
    r.add_argument("--decider", default=None, metavar="CONFIG", help="decider config JSON (configs/deciders/)")
    r.add_argument("--cache", default=None, metavar="PATH", help="call cache (default results/cache/calls.sqlite)")
    r.add_argument("--allow-unpriced", action="store_true", help="allow a decider whose price is null")
    r.add_argument("--noisy", type=float, default=None, metavar="RATE", help="mock-noisy with this flip rate")
    r.add_argument("--noisy-seed", type=int, default=0)
    r.add_argument("--n", type=int, default=None, help="first N cases (default: all)")
    r.add_argument("--cases", default=None, help="cases.jsonl (default: the development cases)")
    r.add_argument("--adapters", default="bm25,recent", help="comma list of names or adapter config .json files, without full")
    r.add_argument("--budgets", default="1000", help="comma list of token budgets")
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--cap-usd", type=float, default=0.0, help="dollar cap for live calls over the whole run folder")
    r.add_argument("--boot-seed", type=int, default=11)
    r.add_argument("--boot-reps", type=int, default=1000)
    r.add_argument("--parallel", type=int, default=1, metavar="N",
                   help=f"decision calls in flight at once (1 to {MAX_PARALLEL}); result files do not change")
    r.add_argument("--memory-side", default=None, metavar="CONFIG",
                   help="memory-side config JSON (configs/memory-side/) for product adapters")
    r.add_argument("--ingest-workers", type=int, default=1, metavar="N",
                   help=f"product-adapter cases ingested at once (1 to {MAX_PARALLEL}); result files do not change")
    r.add_argument("--protocol", default=None, metavar="PATH",
                   help="protocol/v1.json: check the frozen protocol first; table grade when every check passes")
    r.add_argument("--ledger", default=None, choices=("main", "sonnet"),
                   help="optional two-ledger tag stamped into spend.json (Round 5 A1)")
    r.add_argument("--out", required=True, help="run directory (resumes when it already holds this run)")
    r.set_defaults(func=_run)

    qs = sub.add_parser("quickstart", help="run the 20-case quickstart (bm25 and recent, budget 1000, 3 repeats)")
    qs.add_argument("--mock", action="store_true", help="use the mock oracle decider (no network)")
    qs.add_argument("--decider", default=None, metavar="CONFIG", help="decider config JSON (configs/deciders/)")
    qs.add_argument("--cache", default=None, metavar="PATH", help="call cache (default results/cache/calls.sqlite)")
    qs.add_argument("--allow-unpriced", action="store_true", help="allow a decider whose price is null")
    qs.add_argument("--cap-usd", type=float, default=1.0, help="dollar cap for live calls (default 1.0)")
    qs.add_argument("--parallel", type=int, default=4, metavar="N", help=f"calls in flight (1 to {MAX_PARALLEL})")
    qs.add_argument("--out", default=None, help="run directory (default results/runs/quickstart-<UTC time>)")
    qs.set_defaults(func=_quickstart)

    v = sub.add_parser("validity", help="gate decisions on the full history for a sample of the dev cases")
    v.add_argument("--mock", action="store_true", help="use the mock oracle decider (no network)")
    v.add_argument("--decider", default=None, metavar="CONFIG", help="decider config JSON (configs/deciders/)")
    v.add_argument("--cases", default=None, help="cases.jsonl (default: v1 account_review dev cases)")
    v.add_argument("--n-per-mechanism", type=int, default=10)
    v.add_argument("--cache", default=None, metavar="PATH", help="call cache (default results/cache/calls.sqlite)")
    v.add_argument("--allow-unpriced", action="store_true", help="allow a decider whose price is null")
    v.add_argument("--cap-usd", type=float, default=0.0, help="dollar cap for live calls over the whole folder")
    v.add_argument("--parallel", type=int, default=1, metavar="N",
                   help=f"gate calls in flight at once (1 to {MAX_PARALLEL})")
    v.add_argument("--ledger", default=None, choices=("main", "sonnet"),
                   help="optional two-ledger tag stamped into spend.json (Round 5 A1)")
    v.add_argument("--out", required=True, help="output directory (rerun in the same folder reuses the cache)")
    v.set_defaults(func=_validity)

    dr = sub.add_parser("dryrun", help="count the prompts, tokens and dollars of a run without any model call")
    dr.add_argument("--decider", required=True, metavar="CONFIG", help="decider config JSON (configs/deciders/)")
    dr.add_argument("--n", type=int, default=None, help="first N cases (default: all)")
    dr.add_argument("--cases", default=None, help="cases.jsonl (default: the development cases)")
    dr.add_argument("--adapters", default="bm25,recent", help="comma list of names or adapter config .json files, without full")
    dr.add_argument("--budgets", default="1000", help="comma list of token budgets")
    dr.add_argument("--repeats", type=int, default=3)
    dr.set_defaults(func=_dryrun)

    px = sub.add_parser("proxy", help="the product proxy for memory-side model calls")
    pxs = px.add_subparsers(dest="proxy_command", required=True)
    ps = pxs.add_parser("serve", help="run the proxy alone for adapter development; prints a base_url")
    ps.add_argument("--memory-side", required=True, metavar="CONFIG", help="memory-side config JSON")
    ps.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a free one)")
    ps.add_argument("--cap-usd", type=float, default=0.0, help="dollar cap for live memory-side calls")
    ps.add_argument("--log", default=None, metavar="DIR", help="write memory_calls.jsonl into DIR")
    ps.add_argument("--cache", default=None, metavar="PATH", help="call cache (default results/cache/calls.sqlite)")
    ps.add_argument("--allow-unpriced", action="store_true", help="allow an upstream whose price is null")
    ps.set_defaults(func=_proxy_serve)

    rep = sub.add_parser("report", help="rebuild summary.json and print a table")
    rep.add_argument("dir", help="run directory")
    rep.add_argument("--products", default=None, metavar="NAMES",
                     help="comma list of systems treated as products: also print the separation rule")
    rep.set_defaults(func=_report)

    fz = sub.add_parser("freeze", help="freeze one protocol step, or check every frozen hash")
    fg = fz.add_mutually_exclusive_group(required=True)
    fg.add_argument("--step", type=int, metavar="N", help="freeze step N of the spec into the protocol file")
    fg.add_argument("--check", action="store_true", help="recompute every frozen hash; exit 4 on a mismatch")
    fz.add_argument("--spec", default="protocol/v1.spec.json", help="spec (default protocol/v1.spec.json)")
    fz.add_argument("--out", default="protocol/v1.json", help="protocol file to write (default protocol/v1.json)")
    fz.add_argument("--protocol", default="protocol/v1.json", help="protocol file to check (default protocol/v1.json)")
    fz.set_defaults(func=_freeze)

    st = sub.add_parser("stability", help="repeat stability per decider and system; writes stability.json")
    st.add_argument("dir", help="run directory")
    st.set_defaults(func=_stability)

    pw = sub.add_parser("power", help="SE and minimum detectable difference (normal approximation); writes power.json")
    pw.add_argument("dir", help="run directory")
    pw.add_argument("--pairs", type=int, default=3, metavar="K", help="product pairs for the Holm worst case (default 3)")
    pw.add_argument("--power", type=float, default=0.8, help="target power (default 0.8)")
    pw.set_defaults(func=_power)

    if importlib.util.find_spec("memory_bench.hub") is not None:  # the maintainer status page is not in the public export
        hub = sub.add_parser("hub", help="maintainer status page")
        hs = hub.add_subparsers(dest="hub_command", required=True)
        hb = hs.add_parser("build", help="write data.json, index.html and artifact.html")
        hb.add_argument("--root", default=".", help="repo root (default: current directory)")
        hb.add_argument("--out", default=None, help="output directory (default: <root>/hub/dist)")
        hb.add_argument("--built-at", default=None, help="ISO time to stamp (for tests)")
        hb.set_defaults(func=_hub_build)

    sb = sub.add_parser("submit", help="submission folders (docs/submission.md)")
    sbs = sb.add_subparsers(dest="submit_command", required=True)
    sp = sbs.add_parser("pack", help="copy a complete run's released files into DIR/<system>/<date>-<slug>/")
    sp.add_argument("run", help="run directory")
    sp.add_argument("--out", required=True, help="submissions root (for example submissions)")
    sp.add_argument("--slug", default=None, help="short lowercase slug (default: the config label)")
    sp.add_argument("--exploratory", action="store_true", help="allow a run that is not table-grade")
    sp.set_defaults(func=_submit_pack)

    vf = sub.add_parser("verify", help="the seven submission checks; exit 0 when all pass, 5 otherwise")
    vf.add_argument("dir", help="submission folder")
    vf.add_argument("--protocol", default=None, help="protocol file (default protocol/v1.json)")
    vf.add_argument("--cases", default=None,
                    help="case file for the label check (default data/table/cases.jsonl for a table-grade run)")
    vf.add_argument("--runs", default=None, help="maintainer runs for `reproduced` (default results/runs)")
    vf.set_defaults(func=_verify)

    vd = sub.add_parser("verdict", help="pool the table runs and apply the frozen separation rule (Day 13)")
    vd.add_argument("runs", nargs="+", help="run directories to pool")
    vd.add_argument("--out", default=None, help="write the verdict JSON here")
    vd.add_argument("--boot-seed", type=int, default=11)
    vd.add_argument("--boot-reps", type=int, default=1000)
    vd.add_argument("--exploratory", action="store_true", help="allow exploratory runs (never for the table)")
    vd.set_defaults(func=_verdict)

    se = sub.add_parser("sensitivity", help="EXPLORATORY: the separation rule over several bootstrap seeds")
    se.add_argument("runs", nargs="+", help="run directories to pool")
    se.add_argument("--seeds", default="11,12,13,14,15", help="comma list of bootstrap seeds")
    se.add_argument("--boot-reps", type=int, default=10000)
    se.add_argument("--name", default=None, help="write results/verdicts/<name>-sensitivity.json")
    se.add_argument("--out", default=None, help="write the JSON here instead")
    se.add_argument("--exploratory", action="store_true", help="allow exploratory runs in the pool")
    se.set_defaults(func=_sensitivity)

    he = sub.add_parser("health", help="run health")
    hes = he.add_subparsers(dest="health_command", required=True)
    hr = hes.add_parser("rebuild", help="recompute health.json from every attempt in timings.jsonl")
    hr.add_argument("run", help="run directory")
    hr.set_defaults(func=_health_rebuild)

    au = sub.add_parser("audit", help="run audits")
    aus = au.add_subparsers(dest="audit_command", required=True)
    av = aus.add_parser("views", help="stored product views against failed memory-side calls; writes view-audit.json")
    av.add_argument("run", help="run directory")
    av.add_argument("--margin", type=float, default=5.0, help="seconds added before each ingest window (default 5)")
    av.set_defaults(func=_audit_views)

    ve = sub.add_parser("vendor", help="vendor packs (Day 15)")
    ves = ve.add_subparsers(dest="vendor_command", required=True)
    vp = ves.add_parser("pack", help="write one pack folder per product found in the runs")
    vp.add_argument("--runs", nargs="+", required=True, help="run directories (product and prime runs)")
    vp.add_argument("--out", required=True, help="output folder, outside the repository (packs hold private views)")
    vp.add_argument("--verdict", default=None, help="frozen verdict JSON (results/verdicts/day13-table.json)")
    vp.add_argument("--root", default=None, help="repository root for configs (default: this checkout)")
    vp.set_defaults(func=_vendor_pack)

    from .site.cli import add_parser as _add_site
    _add_site(sub)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
