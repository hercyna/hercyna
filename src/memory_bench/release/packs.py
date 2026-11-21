"""bench vendor pack: one folder per product, so each vendor can check and rerun its own numbers.

A pack holds RESULTS.md (facts only), config/, RERUN.md, REBUTTAL.md, views/ and MANIFEST.json. Every number is
read from the run files (summary.json, health.json, view-audit.json) and the frozen verdict file; this module
formats and copies, it never computes a rate. The views hold table-case text: packs are for the vendor only and
must be written outside the repository (the writer refuses an output folder inside the repo root).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

from ..engine import files as F

BASELINES = ("full", "bm25", "recent", "empty")
NAMES = {"mem0": "Mem0", "langmem": "LangMem", "graphiti": "Graphiti"}
EXTRA = {"mem0": "mem0", "langmem": "langmem", "graphiti": "graphiti"}
DEFAULT_CASES = Path("data") / "table" / "cases.jsonl"
VIEWS_NOTE = ("These views hold synthetic table-case text. They are for the vendor only and are never released "
              "publicly.\n")


class PackError(RuntimeError):
    """The runs or the output folder cannot make packs."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- reading the runs

def collect(run_dirs: Sequence[str | Path]) -> dict[str, list[dict]]:
    """product system name -> its entries, one per (run, adapter): the run dir, manifest, adapter entry,
    summary, health and view audit. Baseline adapters are not products."""
    out: dict[str, list[dict]] = {}
    for d in map(Path, run_dirs):
        man = _json(d / "manifest.json")
        if man.get("state") != "complete":
            raise PackError(f"{d.name}: state {man.get('state')!r}, not complete")
        if len(man.get("deciders") or []) != 1:
            raise PackError(f"{d.name}: expected one decider per run")
        summ = _json(d / "summary.json")
        health = _json(d / "health.json") if (d / "health.json").is_file() else {}
        audit = _json(d / "view-audit.json") if (d / "view-audit.json").is_file() else {}
        for ad in man.get("adapters") or []:
            if ad["name"] in BASELINES:
                continue
            out.setdefault(ad["name"], []).append(
                {"dir": d, "man": man, "adapter": ad, "summary": summ, "health": health, "audit": audit,
                 "decider": man["deciders"][0]["id"]})
    for name, entries in out.items():
        seen: set[tuple] = set()
        for e in entries:
            k = (e["decider"], e["adapter"]["config_label"])
            if k in seen:
                raise PackError(f"{name}: two runs for decider {e['decider']} and config {k[1]}")
            seen.add(k)
        if len({e["adapter"]["config_label"] for e in entries}) > 1:
            raise PackError(f"{name}: runs use more than one config label")
        if len({e["adapter"]["product_version"] for e in entries}) > 1:
            raise PackError(f"{name}: runs use more than one product version")
    return out


# ---------------------------------------------------------------- formatting

def _pct(v: float) -> str:
    return f"{v * 100:.1f}"


def _pts(x: dict) -> str:
    lo, hi = x["ci95"]
    return f"{x['value'] * 100:+.1f} [{lo * 100:+.1f}, {hi * 100:+.1f}]"


def _rate(x: dict) -> str:
    lo, hi = x["ci95"]
    return f"{_pct(x['value'])} [{_pct(lo)}, {_pct(hi)}]"


def _display(system: str) -> str:
    return NAMES.get(system, system)


def _cell(summary: dict, decider: str, system: str, label: str) -> tuple[str, dict]:
    cells = summary["deciders"][decider]["cells"]
    for key, c in cells.items():
        if c["system"] == system and c["config_label"] == label:
            return key, c
    raise PackError(f"summary has no cell for {system}/{label} under {decider}")


def _health_adapter(health: dict, system: str, label: str) -> dict | None:
    for a in health.get("adapters") or []:
        if a["name"] == system and a["config_label"] == label:
            return a
    return None


def _audit_system(audit: dict, system: str, label: str) -> dict | None:
    return (audit.get("systems") or {}).get(f"{system}/{label}")


def _verdict_lines(verdict: dict, cell_keys: dict[str, str], product: str) -> list[str]:
    """Frozen verdict lines that involve this product. cell_keys: decider -> cell key (for example mem0/k25@1000)."""
    lines: list[str] = []
    sep = verdict["separation"]
    lines.append(f"- Verdict file result: {sep.get('verdict')}.")
    for did in sorted(sep["deciders"]):
        dd = sep["deciders"][did]
        key = cell_keys.get(did)
        if key is None:
            continue
        x = dd["from_floor"].get(key)
        if x:
            lines.append(f"- {did}, {_display(product)} vs floor: p {x['p']:.4f}, "
                         f"{'separates' if x['separates'] else 'does not separate'}.")
    for did in sorted(sep["deciders"]):
        key = cell_keys.get(did)
        if key is None:
            continue
        for pair, x in sep["deciders"][did]["pairs"].items():
            a, b = [s.strip() for s in pair.split("|")]
            if key not in (a, b):
                continue
            names = " vs ".join(_display(s.split("/")[0]) for s in (a, b))
            low = ("none lower" if not x.get("direction") else
                   f"{_display((a if x['direction'] == 'a_lower' else b).split('/')[0])} lower")
            hp = "-" if x.get("holm_p") is None else f"{x['holm_p']:.4f}"
            lines.append(f"- {did}, {names}: Holm p {hp}, {low}, "
                         f"{'separates' if x['separates'] else 'does not separate'}.")
    return lines


# ---------------------------------------------------------------- pack files

def results_md(system: str, entries: list[dict], verdict: dict | None) -> str:
    entries = sorted(entries, key=lambda e: e["decider"])
    ad0 = entries[0]["adapter"]
    label, version = ad0["config_label"], ad0["product_version"]
    man0 = entries[0]["man"]
    o: list[str] = []
    o.append(f"# {_display(system)} {version}: results")
    o.append("")
    o.append(f"Config label: {label}. Product version: {version}. Cases: {man0['cases']['n']}. "
             f"Protocol: {(man0.get('protocol') or {}).get('version')}, "
             f"sha256 {(man0.get('protocol') or {}).get('protocol_sha256')}. "
             f"Memory side: {(man0.get('memory_side') or {}).get('id')}. Repeats: {man0.get('repeats')}.")
    o.append("")
    o.append("## Per decider")
    o.append("")
    o.append("Excess wrong action and withheld above floor are percentage points above the decider floor. "
             "Right action and wrong share of commits are percent. Intervals are 95 percent.")
    o.append("")
    o.append("| Decider | Excess wrong action | Withheld above floor | Right action | Wrong share of commits "
             "| Cases | Gated cases | Product version | Config |")
    o.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    cell_keys: dict[str, str] = {}
    for e in entries:
        key, c = _cell(e["summary"], e["decider"], system, label)
        cell_keys[e["decider"]] = key
        dsum = e["summary"]["deciders"][e["decider"]]
        o.append(f"| {e['decider']} | {_pts(c['headline'])} | {_pts(c['abstention_above_floor'])} "
                 f"| {_rate(c['right_action'])} | {_rate(c['false_closure_rate'])} | {dsum['n_cases']} "
                 f"| {dsum['n_gated']} | {e['adapter']['product_version']} | {e['adapter']['config_label']} |")
    o.append("")
    o.append("## Decider floors")
    o.append("")
    o.append("The floor is the decider reading the full record. Rates are percent.")
    o.append("")
    o.append("| Decider | Wrong action | Withheld | Right action | Gate correct |")
    o.append("| --- | --- | --- | --- | --- |")
    for e in entries:
        dsum = e["summary"]["deciders"][e["decider"]]
        fl = dsum["floor"]
        gate = dsum["validity"]["overall"]
        o.append(f"| {e['decider']} | {_rate(fl['wrong_commit'])} | {_rate(fl['abstention'])} "
                 f"| {_rate(fl['right_action'])} | {gate['correct']} of {gate['n']} |")
    o.append("")
    o.append("## Frozen verdict")
    o.append("")
    if verdict is None:
        o.append("- No verdict file was passed (`--verdict`).")
    else:
        pool = verdict.get("pool") or {}
        o.append(f"- Verdict file: pooled {len(pool.get('runs') or [])} runs, protocol "
                 f"{pool.get('protocol_sha256')}.")
        o.extend(_verdict_lines(verdict, cell_keys, system))
    o.append("")
    o.append("## Health")
    o.append("")
    o.append("Product warnings by logger, from `health.json`.")
    o.append("")
    for e in entries:
        ha = _health_adapter(e["health"], system, label)
        o.append(f"- {e['decider']} (run {e['dir'].name}):" + ("" if ha else " no health entry."))
        if ha:
            pw = ha.get("product_warnings") or {}
            o.append(f"  - Warnings total: {pw.get('total', 0)}. Adapter errors: {ha.get('errors')}. "
                     f"Cases done: {ha.get('cases_done')}.")
            if ha.get("ingest_seconds_mean") is None:
                o.append("  - No ingest time recorded in this run.")
            for lg, n in sorted((pw.get("by_logger") or {}).items()):
                o.append(f"  - {lg}: {n}")
    o.append("")
    o.append("## View audit")
    o.append("")
    o.append("From `view-audit.json`.")
    o.append("")
    for e in entries:
        au = _audit_system(e["audit"], system, label)
        if au:
            tail = ""
        elif e["audit"]:
            tail = " no audit entry for this product in view-audit.json."
        else:
            tail = " no view-audit.json in this run."
        o.append(f"- {e['decider']} (run {e['dir'].name}):" + tail)
        if au:
            o.append(f"  - Views checked: {au['views_checked']}. Views with a failed call: "
                     f"{au['views_with_failed_call']}. Failed calls in the run: {au['failed_calls']}.")
    o.append("")
    o.append("## Source")
    o.append("")
    for e in entries:
        o.append(f"- {e['decider']}: run {e['dir'].name}, manifest run_id {e['man'].get('run_id')}, "
                 f"cases sha256 {e['man']['cases']['sha256']}.")
    o.append("")
    return "\n".join(o)


def find_adapter_config(root: Path, system: str, label: str) -> Path:
    for p in sorted((root / "configs" / "adapters").glob("*.json")):
        try:
            d = _json(p)
        except ValueError:
            continue
        if d.get("adapter") == system and d.get("config_label") == label:
            return p
    raise PackError(f"no adapter config for {system}/{label} in configs/adapters")


def _cases_path(root: Path, man: dict) -> str | None:
    p = (man.get("cases") or {}).get("path")
    if p:
        return p
    d = root / DEFAULT_CASES
    if d.is_file() and _sha(d) == man["cases"]["sha256"]:
        return str(DEFAULT_CASES)
    return None


def rerun_md(root: Path, system: str, entries: list[dict], run_dirs: Sequence[Path]) -> str:
    entries = sorted(entries, key=lambda e: e["decider"])
    label = entries[0]["adapter"]["config_label"]
    acfg = find_adapter_config(root, system, label)
    rel_acfg = acfg.relative_to(root).as_posix()
    extra = EXTRA.get(system, system)
    env = f".venv-{system}"
    o: list[str] = []
    o.append(f"# {_display(system)}: rerun")
    o.append("")
    o.append("Run from the root of the benchmark repository at the commit that holds `protocol/v1.json`. "
             "`bench freeze --check` must pass first.")
    o.append("")
    o.append("## Environment")
    o.append("")
    o.append("```")
    o.append(f"UV_PROJECT_ENVIRONMENT={env} uv sync --extra {extra}")
    o.append("```")
    o.append("")
    o.append(f"The product extras conflict, so this product has its own environment, `{env}`. Run `bench` from it "
             f"(`{env}/bin/bench`).")
    if system == "graphiti":
        o.append("")
        o.append("Graphiti needs FalkorDB on 127.0.0.1:6390 (see `src/memory_bench/adapters/graphiti.py`):")
        o.append("")
        o.append("```")
        o.append("docker run -d --name bench-falkordb -p 127.0.0.1:6390:6379 falkordb/falkordb:6.0.2")
        o.append("```")
    o.append("")
    o.append("## Cells")
    o.append("")
    o.append("One command per decider, on the same 300 table cases, budget and repeats as the released row.")
    o.append("")
    o.append("Your rerun is exploratory. The frozen decider and memory-side configs read the maintainer's own keys, "
             "so you copy them and point them at your keys (`configs/deciders/openai-compat.example.json` shows the "
             "shape). A changed config cannot pass the protocol's hash check, so the commands below do not pass "
             "`--protocol`. That is expected: the comparison with the released row is still like for like.")
    o.append("")
    for e in entries:
        man = e["man"]
        dec = man["deciders"][0]["id"]
        adapters = [a for a in man["adapters"] if a["name"] != "full"]
        specs = []
        for a in adapters:
            if a["name"] == system:
                specs.append(rel_acfg)
            else:
                specs.append(a["name"])
        cases = _cases_path(root, man)
        cases_arg = cases if cases else f"<cases file with sha256 {man['cases']['sha256']}>"
        ms = man.get("memory_side") or {}
        budgets = ",".join(str(b) for b in man["budgets"])
        cmd = [f"{env}/bin/bench run",
               f"--decider <your copy of configs/deciders/{dec}.json>",
               f"--cases {cases_arg}",
               f"--adapters {','.join(specs)}",
               f"--budgets {budgets}",
               f"--repeats {man['repeats']}"]
        if ms.get("id"):
            cmd.append(f"--memory-side <your copy of configs/memory-side/{ms['id']}.json>")
        cmd.append(f"--boot-seed {man['bootstrap']['seed']} --boot-reps {man['bootstrap']['reps']}")
        cmd.append("--cap-usd <your cap>")
        cmd.append(f"--out results/runs/{e['dir'].name}-rerun")
        o.append(f"{dec}, from run {e['dir'].name}:")
        o.append("")
        o.append("```")
        o.append(" \\\n  ".join(cmd))
        o.append("```")
        o.append("")
    o.append("A run folder reuses the lines of its own `private/views.jsonl` that match the config, the memory side and "
             "the product version (`docs/view-stage.md`, 2.1). `views/` in this pack holds one file per run; "
             "copying one to `<out>/private/views.jsonl` before a run skips that ingest.")
    o.append("")
    o.append("`--parallel` and `--ingest-workers` are not recorded in the manifests; set them for your machine.")
    o.append("")
    o.append("## Compare")
    o.append("")
    o.append("Print your run's table and compare it with `RESULTS.md`:")
    o.append("")
    o.append("```")
    for e in entries:
        o.append(f"{env}/bin/bench report results/runs/{e['dir'].name}-rerun")
    o.append("```")
    o.append("")
    o.append("Your gate and floor calls are fresh, so your rows cannot be pooled with the released runs under the "
             "frozen rule. Compare the excess wrong action, withheld above floor and right action, with their intervals. "
             "The released runs are in `results/table/v1/` of the repository.")
    o.append("")
    return "\n".join(o)


def rebuttal_md(system: str) -> str:
    return "\n".join([
        f"# {_display(system)}: how to challenge or improve the row",
        "",
        "- Rerun the cells with `RERUN.md` and compare your files with `RESULTS.md`.",
        "- Report a difference as an issue with your run folder (`bench submit pack`), your commit and your environment.",
        "- Send a vendor-tuned config by pull request (`docs/submission.md`). The maintainer runs it on the table "
        "cases and publishes it as its own row, labelled exploratory.",
        "- The tuned row does not replace the row in this pack.",
        "- The numbers in this pack are what the public table shows on the release date.",
        "- The rows are a bench configuration: one shared memory-side model and embedder, set records per write. "
        "They are not vendor defaults.",
        "",
    ])


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _filter_views(src: Path, dst: Path, system: str) -> int:
    n = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, encoding="utf-8") as fi, open(dst, "w", encoding="utf-8") as fo:
        for line in fi:
            if not line.strip():
                continue
            if json.loads(line).get("system") == system:
                fo.write(line if line.endswith("\n") else line + "\n")
                n += 1
    return n


def _manifest(pack: Path, system: str, version: str) -> dict:
    files = []
    for p in sorted(pack.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.json":
            files.append({"path": p.relative_to(pack).as_posix(), "bytes": p.stat().st_size, "sha256": _sha(p)})
    return {"schema": "memory_bench.vendor_pack/1", "product": system, "product_version": version, "files": files}


def write_packs(run_dirs: Sequence[str | Path], out: str | Path, *, verdict_path: str | Path | None = None,
                root: str | Path | None = None) -> dict[str, Path]:
    """Write one pack per product found in the runs. Returns product -> pack folder."""
    root = Path(root) if root else repo_root()
    out = Path(out).resolve()
    run_dirs = [Path(d) for d in run_dirs]
    if root.resolve() == out or root.resolve() in out.parents:
        raise PackError(f"{out} is inside the repository; packs hold private views and go outside it")
    by_product = collect(run_dirs)
    if not by_product:
        raise PackError("no product system in the runs")
    verdict = _json(Path(verdict_path)) if verdict_path else None
    for system in by_product:
        if (out / system).exists() and any((out / system).iterdir()):
            raise PackError(f"{out / system} already exists; nothing is overwritten")
    packs: dict[str, Path] = {}
    for system, entries in sorted(by_product.items()):
        entries = sorted(entries, key=lambda e: e["decider"])
        ad0 = entries[0]["adapter"]
        pack = out / system
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "RESULTS.md").write_text(results_md(system, entries, verdict), encoding="utf-8")
        acfg = find_adapter_config(root, system, ad0["config_label"])
        _copy(acfg, pack / "config" / acfg.name)
        ms_id = (entries[0]["man"].get("memory_side") or {}).get("id")
        if ms_id:
            _copy(root / "configs" / "memory-side" / f"{ms_id}.json", pack / "config" / f"{ms_id}.json")
        proto = root / ((entries[0]["man"].get("protocol") or {}).get("path") or "protocol/v1.json")
        _copy(proto, pack / "config" / "v1.json")
        (pack / "RERUN.md").write_text(rerun_md(root, system, entries, run_dirs), encoding="utf-8")
        (pack / "REBUTTAL.md").write_text(rebuttal_md(system), encoding="utf-8")
        for e in entries:
            vsrc = e["dir"] / "private" / "views.jsonl"
            if not vsrc.is_file():
                raise PackError(f"{e['dir'].name}: no private/views.jsonl")
            _filter_views(vsrc, pack / "views" / f"{e['dir'].name}.jsonl", system)
        (pack / "views" / "NOTE.md").write_text(VIEWS_NOTE, encoding="utf-8")
        F.write_json(pack / "MANIFEST.json", _manifest(pack, system, ad0["product_version"]))
        packs[system] = pack
    readme = ["# Vendor packs", "",
              "One folder per product. Each holds RESULTS.md, config/, RERUN.md, REBUTTAL.md, views/ and MANIFEST.json.",
              "The views hold table-case text and are for the vendor only.", ""]
    for system, entries in sorted(by_product.items()):
        ad = entries[0]["adapter"]
        readme.append(f"- [{_display(system)}]({system}/RESULTS.md): version {ad['product_version']}, "
                      f"config {ad['config_label']}, deciders {', '.join(sorted(e['decider'] for e in entries))}")
    readme.append("")
    (out / "README.md").write_text("\n".join(readme), encoding="utf-8")
    return packs


__all__ = ["write_packs", "collect", "results_md", "rerun_md", "rebuttal_md", "PackError"]
