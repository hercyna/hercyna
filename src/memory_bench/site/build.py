"""bench site build: read maintainer runs and verified submissions, write three static pages.

The site never computes a rate. Rates come from each run's summary.json, the rank verdict and the pair
differences from scoring.separation, dollars per case from spend.json over the case count. Method and submit
prose comes from site/copy/*.md; the landing lines are constants below (site copy is written in-house).

index.html is the Observatory (Varma's prototype 06, 2026-11-20): a left rail, the hook and the stat strip,
the board beside its 2D frontier and run audit, the verdict with its why-no-rank explainer, the How it works
studio with the stage stepper, one case anatomy, then method and submit. Every decider's rows, its frontier
and every audit are printed statically, so the page reads without script.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import figures, mdlite
from .script import HEAD_JS, JS
from .style import CSS, FONT_LINK

from ..scoring.metrics import cell_name

# Board order after the products, and never compared by the rule. `empty` (Day 14) is the pure-withholding reference.
BASELINES = ("full", "bm25", "recent", "empty")

BRAND = "Hercyna"
COPY = {"method.html": "method.md", "submit.html": "submit.md", "about.html": "about.md", "why": "why.md",
        "limits": "limits.md"}
E = html.escape


# ---------------------------------------------------------------- verify hook

def _verified(d: Path, root: Path) -> tuple[bool, str]:
    """(passes verify, grade). Defaults are resolved against the repo root, not the working directory."""
    try:
        from memory_bench.submit.verify import verify
    except ImportError:
        return False, ""
    # The table cases are the label check's default only for a table-grade submission (docs/submission.md
    # section 3, check 4). An exploratory submission brings its own cases, so passing them would hide it.
    man = _json(d / "manifest.json") or {}
    cases = root / "data" / "table" / "cases.jsonl"
    use = cases if man.get("grade") == "table" and cases.is_file() else None
    res = verify(d, root / "protocol" / "v1.json", use, runs=root / "results" / "runs")
    return bool(res.ok), str(res.grade or "")


# ---------------------------------------------------------------- readers

def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sources(root: Path, include_exploratory: bool) -> list[dict]:
    """Maintainer runs first (by folder name), then submissions (by path). Maintainer runs are the working
    `results/runs/` and the released, tracked `results/table/<version>/` (the text-free files of the table runs,
    so a fresh clone builds the same board); a run present in both is read once, from `results/runs/`.
    Exploratory runs show only with `include_exploratory`, except those released into `results/table/<version>/`,
    which are public and labelled exploratory."""
    out: list[dict] = []
    seen: set[str] = set()
    table_dirs = sorted((root / "results" / "table").glob("*/*/"))
    released = set(table_dirs)
    run_dirs = sorted((root / "results" / "runs").glob("*/")) + table_dirs
    for d in run_dirs:
        if d.name in seen:
            continue
        man, summ = _json(d / "manifest.json"), _json(d / "summary.json")
        if not man or not summ or man.get("state") != "complete":
            continue
        grade = man.get("grade")
        if grade == "table":
            label = "maintainer run"
        elif grade == "exploratory" and (include_exploratory or d in released):
            # an exploratory run released into results/table/<version>/ (the empty baseline) is public, labelled
            label = "exploratory"
        else:
            continue
        seen.add(d.name)
        out.append({"dir": d, "man": man, "sum": summ, "label": label, "sub": None, "id": d.name})
    for p in sorted((root / "submissions").rglob("submission.json")):
        d = p.parent
        sub, man, summ = _json(p), _json(d / "manifest.json"), _json(d / "summary.json")
        if not sub or not man or not summ:
            continue
        ok, grade = _verified(d, root)
        if not ok:
            continue
        if grade == "exploratory":
            # a verified outside submission runs on its own keys, so it is exploratory in v1; it is shown, labelled
            label = "exploratory"
        else:
            label = "maintainer-reproduced" if sub.get("reproduced") else "self-reported"
        out.append({"dir": d, "man": man, "sum": summ, "label": label, "sub": sub,
                    "id": str(d.relative_to(root))})
    return out


MEMORY_COST: dict[str, float] = {}  # system -> memory-side dollars per case, from the run that paid the ingest


def _memory_costs(sources: list[dict]) -> dict[str, float]:
    """Products reuse stored views across deciders, so only the ingesting run meters the memory side. Read each
    product's memory-side dollars per case from the run that paid them (the largest such figure)."""
    out: dict[str, float] = {}
    for src in sources:
        sp = _json(src["dir"] / "spend.json") or {}
        n = (src["man"].get("cases") or {}).get("n")
        for system, ad in (sp.get("by_adapter") or {}).items():
            mem = ((ad or {}).get("memory_side") or {}).get("usd") or 0.0
            if n and mem:
                out[system] = max(out.get(system, 0.0), mem / n)
    return out


def _usd_per_case(src: dict, system: str) -> tuple[float, tuple[float, float] | None] | None:
    """(dollars per case, (memory side, decider) per case or None). A product's cost is its memory-side model
    calls plus the decider's view calls, as metered in this run (cached calls cost nothing)."""
    sp = _json(src["dir"] / "spend.json") or {}
    n = (src["man"].get("cases") or {}).get("n")
    if not n:
        return None
    ad = (sp.get("by_adapter") or {}).get(system)
    if ad is None:
        usd = sp.get("total_usd")
        return None if usd is None else (usd / n, None)
    dec = ad.get("usd") or 0.0
    mem = (ad.get("memory_side") or {}).get("usd") or 0.0  # a product's own model calls (docs/run-files.md)
    mem_pc = mem / n if mem else MEMORY_COST.get(system, 0.0)  # reused views: the ingest was paid in another run
    return (dec / n + mem_pc, (mem_pc, dec / n) if mem_pc else None)


def _entries(sources: list[dict]) -> dict[str, dict]:
    """decider -> {"floor": ..., "rows": {key: row}}. The first source holding a system shows it."""
    deciders: dict[str, dict] = {}
    for src in sources:
        for did, dd in sorted((src["sum"].get("deciders") or {}).items()):
            slot = deciders.setdefault(did, {"floor": None, "floor_src": None, "rows": {}, "base": src})
            if slot["floor"] is None and dd.get("floor"):
                slot["floor"], slot["floor_src"] = dd["floor"], src
            for cname, cell in sorted((dd.get("cells") or {}).items()):
                key = (cell.get("system"), cell.get("config_label"), cell.get("budget"))
                row = slot["rows"].get(key)
                if row is None:
                    slot["rows"][key] = {"key": key, "cell": cell, "src": src, "also": []}
                elif src["sub"] is not None:
                    row["also"].append(src)
    return deciders


# ---------------------------------------------------------------- the rank rule

def _ident(src: dict) -> tuple:
    return ((src["man"].get("cases") or {}).get("sha256"),
            (src["man"].get("protocol") or {}).get("protocol_sha256"))


def _gate(rows: list[dict], did: str) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows if r["decider"] == did and r["kind"] == "gate")


def _comparable(base: dict, base_rows: list[dict], other: dict, did: str) -> bool:
    """Pool two runs for one decider only on the same case set and protocol and identical gate rows."""
    from ..engine import files as F

    if _ident(base) != _ident(other):
        return False
    return _gate(base_rows, did) == _gate(F.read_metrics(other["dir"] / F.METRICS), did)


def _no(verdict: str) -> dict:
    return {"leaderboard": None, "verdict": verdict, "pairs": [], "by_decider": {}, "alpha": None}


def _rule_l(deciders: dict[str, dict]) -> dict:
    """Pool each decider's rows (base source whole, product view rows of the other sources) and run separation.
    Keeps each decider's pair results (difference, interval, Holm p, direction) for the explainer."""
    from ..engine import files as F
    from ..scoring import separation

    products = sorted({k for d in deciders.values() for k, r in d["rows"].items()
                       if k[0] not in BASELINES and r["src"]["label"] != "exploratory"},
                      key=lambda k: (str(k[0]), str(k[1]), k[2] or 0))
    if not products:
        return _no("no products")
    rows: list[dict] = []
    for did, slot in sorted(deciders.items()):
        base = slot["base"]
        base_rows = [r for r in F.read_metrics(base["dir"] / F.METRICS) if r["decider"] == did]
        rows += base_rows
        for key, row in sorted(slot["rows"].items(), key=lambda kv: str(kv[0])):
            if row["src"] is base or key[0] in BASELINES or key not in products:
                continue
            if not _comparable(base, base_rows, row["src"], did):
                return _no("runs not comparable")
            rows += [r for r in F.read_metrics(row["src"]["dir"] / F.METRICS)
                     if r["decider"] == did and r["kind"] == "view"
                     and (r["system"], r["config_label"], r["budget"]) == key]
    boot = deciders[sorted(deciders)[0]]["base"]["man"].get("bootstrap") or {}
    try:
        res = separation(rows, products, seed=boot.get("seed", 11), reps=boot.get("reps", 1000))
    except (ValueError, KeyError, TypeError):
        return _no("not computable")
    by = {did: {"pairs": dd.get("pairs") or {}, "missing": dd.get("missing_products") or []}
          for did, dd in sorted((res.get("deciders") or {}).items())}
    return {"leaderboard": res["leaderboard"], "verdict": res["verdict"],
            "pairs": [(p["pair"], p["direction"]) for p in res["separating_pairs"]],
            "by_decider": by, "alpha": (res.get("rule") or {}).get("alpha")}


# ---------------------------------------------------------------- presentation data

LABELS = (("maintainer run", "Maintainer run"), ("self-reported", "Self-reported"),
          ("exploratory", "Exploratory"), ("maintainer-reproduced", "Maintainer-reproduced"))
HOOK = ("Which memory makes your agent", "act wrong?")
SUBLINE = ("Hercyna scores agent memory products by the action an agent acts on after reading what they return. "
           "Wrong action and withheld action are measured side by side, each above the decider's own repeat "
           "noise on the full history.")
JOB_CMD = "uv run bench quickstart --mock"
JOB_NOTE = ("No key, no cost: runs the 20-case check with the rule oracle. "
            "Drop --mock and pass --decider to score for real.")
EMPTY = "No table run yet. The first full run prints here."
SUBMIT_JOBS = ("bench submit pack <run> --out submissions", "bench verify submissions/<system>/<date>-<slug>")
SUBMIT_COMMANDS = ("bench submit pack RUN --out DIR [--slug S]", "bench verify DIR")
STEPS = (
    ("History", "A dated account history, 100 or 400 records. A rule oracle knows the right action."),
    ("Memory", "The memory product ingests the history in order and returns a view of at most {budget}."),
    ("View", "The agent sees only that view, never the full history and never the oracle."),
    ("Decider", "Two models from two vendors pick one of five actions. Each first passes a validity gate."),
    ("Action", "Right, withheld or wrong. The floor is the decider's own noise on the full history."),
)


def _iv(s: dict | None) -> str | None:
    ci = (s or {}).get("ci95")
    return f"[{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if ci else None


def _rate(s: dict | None) -> dict | None:
    """A share from the summary, as printed text, bar width and interval. No arithmetic beyond scaling."""
    if not s or s.get("value") is None:
        return None
    v = s["value"] * 100
    return {"t": f"{v:.1f}%", "w": round(v, 2), "iv": _iv(s)}


def _pts(s: dict | None) -> dict | None:
    """A difference in percentage points, with its sign, interval and raw points for the drawings."""
    if not s or s.get("value") is None:
        return None
    ci = s.get("ci95")
    return {"t": f"{s['value'] * 100:+.1f} pts", "iv": _iv(s), "v": round(s["value"] * 100, 3),
            "lo": round(ci[0] * 100, 3) if ci else None, "hi": round(ci[1] * 100, 3) if ci else None}


def _slug(x: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in x.lower()).strip("-") or "d"


def _version(src: dict, system: str, config: Any) -> str | None:
    for a in src["man"].get("adapters") or []:
        if a.get("name") == system and a.get("config_label") == config:
            return a.get("product_version")
    return None


def _model(slot: dict, did: str) -> str | None:
    return next((d.get("model") for d in slot["base"]["man"].get("deciders") or [] if d.get("id") == did), None)


CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs" / "adapters"


def _per_write(system: str, config: Any) -> int | None:
    """Records per write of a product config: the digits of its label (k25, c25, e25), else the `records_per_*`
    value of the matching adapter config file. Never assumed."""
    import re as _re

    m = _re.fullmatch(r"[A-Za-z_-]*?(\d+)", str(config or ""))
    if m:
        return int(m.group(1))
    if CONFIGS_DIR.is_dir():
        for p in sorted(CONFIGS_DIR.glob("*.json")):
            c = _json(p) or {}
            if c.get("adapter") == system and c.get("config_label") == config:
                for k, v in sorted((c.get("config") or {}).items()):
                    if k.startswith("records_per_") and isinstance(v, int):
                        return v
    return None


def _config_line(system: str, config: Any, budget: Any) -> str:
    if system in BASELINES:
        return f"{config} · {budget} tokens"
    n = _per_write(system, config)
    return f"bench config · {n} records per write" if n is not None else f"bench config · {config}"


def _row_data(r: dict, did: str) -> dict:
    """One row's printed values. Right, withheld and wrong are the cell's right_action, abstention and
    wrong_commit shares (one denominator: gated decisions); the site derives none of them."""
    cell, (system, config, budget) = r["cell"], r["key"]
    view = (cell.get("view") or {}).get("mean_tokens")
    usd = _usd_per_case(r["src"], system)
    n = (((r["src"]["sum"] or {}).get("deciders") or {}).get(did) or {}).get("n_cases") if r["src"].get("sum") else None
    also = []
    for s in r["also"]:
        who = (s["sub"] or {}).get("submitter")
        also.append(s["label"] + (f" · {who}" if who else ""))
    return {
        "system": str(system), "config": _config_line(system, config, budget), "budget": budget,
        "label": r["src"]["label"], "base": system in BASELINES, "n": n, "also": also, "run": r["src"]["id"],
        "right": _rate(cell.get("right_action")), "held": _rate(cell.get("abstention")),
        "wrong": _rate(cell.get("wrong_commit")),
        "ex": _pts(cell.get("headline")), "ha": _pts(cell.get("abstention_above_floor")),
        "view": None if view is None else f"{view:,.0f}",
        "usd": None if usd is None else f"${usd[0]:.3f}" + (f" (memory ${usd[1][0]:.3f}, decider ${usd[1][1]:.3f})" if usd[1] else ""),
        "ver": _version(r["src"], system, config),
    }


def _tiers(verdict: dict | None) -> dict[str, int]:
    """Rank per product cell from the separation verdict, not from raw values: a product's rank is 1 plus the
    number of products that separate below it on every decider (competition ranking). Products that did not
    separate share a rank. Baselines are never ranked (the rule compares products only).

    Not rendered since Day 14 (owner decision on review B1): the board prints no rank, because a lower wrong-action
    rate can come from withholding. Kept for the rule's record and its test."""
    lower: dict[str, set] = {}
    for pair, direction in (verdict or {}).get("pairs") or []:
        a, b = pair.split(" | ")
        lo, hi = (a, b) if direction == "a_lower" else (b, a)
        lower.setdefault(hi, set()).add(lo)
    return {name: 1 + len(lo) for name, lo in lower.items()}


def _order(rows: list[dict]) -> list[dict]:
    """The fixed order: products A to Z, then the baselines in BASELINES order. No rank."""
    def fixed(r: dict) -> tuple:
        s, c, b = r["key"]
        return (1, BASELINES.index(s), "", 0) if s in BASELINES else (0, 0, f"{s}/{c}", b or 0)
    return sorted(rows, key=fixed)


def _board_data(deciders: dict[str, dict]) -> list[dict]:
    out = []
    for did, slot in sorted(deciders.items()):
        out.append({"id": did, "model": _model(slot, did),
                    "rows": [_row_data(r, did) for r in _order(list(slot["rows"].values()))]})
    return out


def _proto_values(proto: dict) -> dict:
    vals: dict[str, Any] = {}
    for step in sorted((proto.get("steps") or {})):
        vals.update(proto["steps"][step].get("values") or {})
    return vals


# ---------------------------------------------------------------- small parts

COPY_ICON = ('<svg class="i-copy" viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" '
             'stroke-width="1.4"><rect x="4.5" y="4.5" width="8" height="8" rx="1"/>'
             '<path d="M9.5 2.5 V1.5 H1.5 V9.5 H2.5"/></svg>'
             '<svg class="i-ok" viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" '
             'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 7.5 L5.5 11 L12 3"/></svg>')
THEME_ICON = ('<svg viewBox="0 0 14 14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.4">'
              '<circle cx="7" cy="7" r="5.5"/><path d="M7 1.5 A5.5 5.5 0 0 1 7 12.5 Z" fill="currentColor"/></svg>')
CHEVRON = ('<svg viewBox="0 0 12 12" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6">'
           '<path d="M4 2 L8 6 L4 10"/></svg>')


def _copy_btn(cmd: str, label: str = "Copy", cls: str = "btn solid") -> str:
    return (f'<button class="{cls} copy js-only" type="button" data-copy="{E(cmd)}" aria-label="Copy command: {E(cmd)}">'
            f'{COPY_ICON}<span class="cl">{E(label)}</span></button>')


def _job(cmd: str) -> str:
    return f'<p class="cli-cmd"><code>{E(cmd)}</code>{_copy_btn(cmd)}</p>'


def _pct_t(s: dict | None) -> str:
    r = _rate(s)
    return "n/a" if not r else r["t"] + (f' {r["iv"]}' if r["iv"] else "")


# ---------------------------------------------------------------- board

def _bar(row: dict) -> str:
    parts = (("right", "r", row["right"]), ("withheld", "h", row["held"]), ("wrong", "w", row["wrong"]))
    segs = "".join(f'<i class="p-{c}" style="width:{p["w"]}%"></i>' for _, c, p in parts if p)
    nums = "".join(f'<span class="{c}">{E(p["t"]) if p else "n/a"}</span>' for name, c, p in parts)
    aria = ", ".join(f'{name} {p["t"] if p else "n/a"}' for name, _, p in parts)
    return (f'<div class="bar" role="img" aria-label="Share of decisions: {E(aria)}">{segs}</div>'
            f'<div class="bar-nums">{nums}</div>')


def _num(p: dict | None, key: str) -> str:
    if not p:
        return f'<div class="num" data-k="{key}"><b>n/a</b></div>'
    return (f'<div class="num" data-k="{key}"><b>{E(p["t"])}</b>'
            + (f'<small>{E(p["iv"])}</small>' if p["iv"] else "") + "</div>")


def _band(row: dict, d: int, i: int) -> str:
    """One board row. The system cell carries the row label; a no-script line repeats the audit facts."""
    lab = row["label"]
    meta = (f'<span>{E(row["config"])}</span><span class="lab{" x" if lab == "exploratory" else ""}">{E(lab)}</span>'
            + "".join(f"<span>{E(a)}</span>" for a in row["also"]))
    n = "n/a" if row["n"] is None else E(str(row["n"]))
    nojs = " · ".join(x for x in (
        f'view {E(row["view"])} tokens' if row["view"] else None,
        f'{E(row["usd"])} per case' if row["usd"] else None,
        f'version {E(row["ver"])}' if row["ver"] else None,
        f'run {E(row["run"])}') if x)
    return (f'<tr class="row" data-d="{d}" data-r="{i}" data-base="{1 if row["base"] else 0}" data-label="{E(lab)}">'
            f'<td class="c-s"><div class="sys-t"><span class="sys">{E(row["system"])}</span></div>'
            f'<div class="sys-m">{meta}</div><div class="nojs-meta nojs-only">{nojs}</div></td>'
            f'<td class="c-b">{_bar(row)}</td>'
            f'<td class="c-e r">{_num(row["ex"], "Excess wrong action")}</td>'
            f'<td class="c-h r">{_num(row["ha"], "Withheld above floor")}</td>'
            f'<td class="c-n r"><div class="num" data-k="Cases"><b>{n}</b></div></td></tr>')


def _audit(row: dict, i: int, on: bool) -> str:
    def iv(p: dict | None) -> str:
        return "n/a" if not p else p["t"] + (f' {p["iv"]}' if p["iv"] else "")

    split = " · ".join(f'{name} {p["t"] if p else "n/a"}' for name, p in
                       (("right", row["right"]), ("withheld", row["held"]), ("wrong", row["wrong"])))
    items = [("Excess wrong action", iv(row["ex"])), ("Withheld above floor", iv(row["ha"])),
             ("Decisions", split),
             ("View tokens", f'{row["view"]} of {row["budget"]}' if row["view"] and row["budget"] else row["view"]),
             ("Cost per case", row["usd"]), ("Version", row["ver"]),
             ("Cases", None if row["n"] is None else str(row["n"])), ("Run", row["run"]),
             ("Also shown by", "; ".join(row["also"]) or None)]
    dl = "".join(f"<div><dt>{k}</dt><dd>{E(str(v))}</dd></div>" for k, v in items if v)
    lab = row["label"]
    return (f'<div class="audit{" on" if on else ""}" data-r="{i}">'
            f'<div class="a-head"><b>{E(row["system"])}</b><span class="lab{" x" if lab == "exploratory" else ""}">'
            f'{E(lab)}</span></div>{figures.whisker_svg(row["ex"], row["ha"])}<dl class="dl-tight">{dl}</dl></div>')


def _panel(i: int, dec: dict) -> str:
    sid = _slug(dec["id"])
    model = f' <span>· {E(dec["model"])}</span>' if dec["model"] else ""
    rows = "".join(_band(r, i, j) for j, r in enumerate(dec["rows"]))
    audits = "".join(_audit(r, j, j == 0) for j, r in enumerate(dec["rows"]))
    plotted = sum(1 for r in dec["rows"] if r["ex"] and r["ha"])
    note = ("" if plotted == len(dec["rows"]) else
            f'<p class="lens-note">{len(dec["rows"]) - plotted} row(s) lack a value on one axis and are not drawn.</p>')
    return (f'<div class="dpanel" id="d-{sid}"><h3 class="dhead nojs-only">{E(dec["id"])}{model}</h3>'
            '<div class="obs-split"><div class="board-col"><table class="tbl"><thead><tr><th scope="col">System</th>'
            '<th scope="col">Right · withheld · wrong, share of decisions</th>'
            '<th scope="col" class="r">Excess wrong action</th><th scope="col" class="r">Withheld above floor</th>'
            f'<th scope="col" class="r">Cases</th></tr></thead><tbody>{rows}'
            '<tr class="none js-only" hidden><td class="empty" colspan="5">No rows match this filter.</td></tr>'
            '</tbody></table></div>'
            f'<aside class="lens" aria-label="Frontier and run audit for {E(dec["id"])}">'
            f'<div class="lens-sec"><h3>Frontier <span>{E(dec["id"])}</span></h3>{figures.frontier_svg(dec["rows"], dec["id"])}'
            f'{note}</div><div class="lens-sec"><h3>Run audit <span>95% intervals</span></h3>{audits}</div></aside>'
            '</div></div>')


def _tabs(decs: list[dict]) -> str:
    tabs = "".join(
        f'<button class="tab" type="button" role="tab" id="tab-{_slug(d["id"])}" aria-controls="d-{_slug(d["id"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{E(d["id"])}<span>'
        + (f'{E(d["model"])} · ' if d["model"] else "") + f'{len(d["rows"])} rows</span></button>'
        for i, d in enumerate(decs))
    return (f'<div class="tab-rail js-only"><div class="tablist" role="tablist" aria-label="Decider">{tabs}</div>'
            '<label class="tab-tools"><input type="checkbox" id="f-base" checked>Baselines</label></div>')


LEGEND = ('<div class="legend-line"><div class="leg-grp"><span class="leg"><i class="sw r"></i>Right action</span>'
          '<span class="leg"><i class="sw h"></i>Withheld (REQUEST_INFO)</span>'
          '<span class="leg"><i class="sw w"></i>Wrong action</span></div>{order}</div>')


# ---------------------------------------------------------------- verdict and why no rank

REASONS = {
    "no table run": "no table run yet",
    "no products": "the board holds baselines only, so there is no product pair to compare",
    "needs two deciders": "product rows exist for one decider only; a rank needs two",
    "no deciders": "no decider has rows yet",
    "runs not comparable": "the rows come from runs that do not share one case set, protocol and gate",
    "not computable": "the comparison could not be computed from these files",
    "null": "no product pair separates on both deciders in the same direction",
}
WHY = {
    "no table run": "No run has been verified for this board yet. The first full run prints here, with its "
                    "verdict.",
    "no products": "The rank rule compares memory products with each other. Baselines show the scale but are "
                   "never ranked. Product rows appear once a run that includes them is verified.",
    "needs two deciders": "A difference that only one model sees could be that model's habit, not the memory. "
                          "So a rank needs product rows on both frozen deciders.",
    "no deciders": "A rank needs product rows on both frozen deciders.",
    "runs not comparable": "Rows are pooled for the comparison only when their runs share the same case set, "
                           "the same protocol and identical gate rows. These do not, so the comparison is not run. "
                           "Each row still reads correctly against its own run's floor.",
    "not computable": "The separation test could not run on these files. The rows still read against their "
                      "floors.",
    "null": "The test ran on every product pair. None separates on both deciders in the same direction, so the "
            "board publishes the null. The pair results are below.",
}


def _verdict_key(v: dict | None) -> str:
    if v is None:
        return "no table run"
    if v["leaderboard"] is False:
        return "null"
    return v["verdict"]


def _rule_text(vals: dict, alpha: Any, leaderboard: bool = False) -> str:
    sep = vals.get("separation") or {}
    a = alpha if alpha is not None else sep.get("alpha")
    test = sep.get("test")
    decs = vals.get("deciders") or []
    who = f"both frozen deciders ({', '.join(map(str, decs))})" if len(decs) == 2 else "every frozen decider"
    t = f"Protocol {E(str(vals.get('_version') or 'v1'))} prints a rank only when at least one product pair separates on {E(who)}, in the same direction."
    if test or a is not None:
        t += " The test: " + ", ".join(E(str(x)) for x in (test, f"alpha {a}" if a is not None else None) if x) + "."
    t += " Withheld action is shown beside it, so a product cannot look safe by not acting."
    if leaderboard:
        t += (" In v1 the rule was met. After a pre-release review the board prints no rank numbers, because a "
              "product can lower its wrong action by withholding. This departs from the preregistration, which "
              "planned a printed rank. The rule's result is reported as it came out.")
    return t


def _pairs_table(v: dict) -> str:
    out = []
    for did, dd in sorted((v.get("by_decider") or {}).items()):
        rows = []
        for key, p in sorted((dd.get("pairs") or {}).items()):
            diff = _pts(p)
            hp = p.get("holm_p")
            direction = p.get("direction")
            a, _, b = key.partition(" | ")
            lower = a if direction == "a_lower" else b if direction == "b_lower" else "neither"
            rows.append(f'<tr><td>{E(key)}</td><td class="r">{E(diff["t"]) if diff else "n/a"}'
                        f'{(" " + E(diff["iv"])) if diff and diff["iv"] else ""}</td>'
                        f'<td class="r">{"n/a" if hp is None else f"{hp:.3f}"}</td><td>{E(lower)}</td>'
                        f'<td>{"<span class=yes>separates</span>" if p.get("separates") else "no"}</td></tr>')
        if not rows:
            continue
        out.append(f'<h3>{E(did)}</h3><table><thead><tr><th>Pair a | b</th><th class="r">Wrong action, a minus b</th>'
                   '<th class="r">Holm p</th><th>Lower wrong action</th><th>Result</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table>')
    return "".join(out)


NAMES = {"mem0": "Mem0", "langmem": "LangMem", "graphiti": "Graphiti"}


def _pair_words(pair: str, direction: str) -> str:
    """'graphiti/e25@1000 | langmem/c25@1000', 'a_lower' -> 'Graphiti below LangMem on wrong action'."""
    a, b = (NAMES.get(x.split("/")[0], x.split("/")[0]) for x in pair.split(" | "))
    lo, hi = (a, b) if direction == "a_lower" else (b, a)
    return f"{lo} below {hi} on wrong action"


def _verdict_html(v: dict | None, vals: dict | None = None) -> str:
    """The verdict callout. It opens into the why-no-rank explainer, which states the actual reason."""
    vals = vals or {}
    alpha = (v or {}).get("alpha")
    if v and v["leaderboard"]:
        pairs = "; ".join(_pair_words(p, d) for p, d in v["pairs"])
        body = (f'<p>{_rule_text(vals, alpha, True)}</p><p><strong>These pairs separate on both deciders in the same '
                f'direction:</strong> {E(pairs)}.</p>' + _pairs_table(v))
        return (f'<details class="verdict ok" id="why" data-verdict="leaderboard"><summary><strong>Frozen verdict: leaderboard</strong>'
                f'<span class="v-t">– {E(pairs)}</span>'
                f'<span class="v-more">What the frozen rule found{CHEVRON}</span></summary>'
                f'<div class="why">{body}</div></details>')
    key = _verdict_key(v)
    reason = REASONS.get(key, (v or {}).get("verdict") or key)
    body = f'<p>{_rule_text(vals, alpha)}</p><p><strong>Here:</strong> {E(WHY.get(key, reason))}</p>'
    if v:
        body += _pairs_table(v)
    return (f'<details class="verdict stop" id="why" data-verdict="{E(key)}"><summary><strong>No rank</strong>'
            f'<span class="v-t">– {E(reason[:1].upper() + reason[1:])}.</span>'
            f'<span class="v-more">Why no rank is published{CHEVRON}</span></summary>'
            f'<div class="why">{body}</div></details>')


ORDER = ("Order: fixed. Products A to Z, then baselines. No rank numbers: a product can lower its wrong action "
         "by withholding, so read wrong action and withheld action together.")


def _board(sources: list[dict], deciders: dict[str, dict], verdict: dict | None, vals: dict) -> str:
    head = ('<section class="sec" id="board" aria-labelledby="board-h"><div class="sec-head">'
            '<h2 id="board-h">The board</h2><span class="sub">Hover or select a row to find it in the frontier '
            'and the run audit.</span></div>')
    if not deciders:
        return (head + LEGEND.format(order="") + '<table class="tbl"><tbody><tr><td class="empty">'
                f'{E(EMPTY)}</td></tr></tbody></table>{_verdict_html(None, vals)}</section>')
    decs = _board_data(deciders)
    return (head + _tabs(decs) + LEGEND.format(order=f"<span>{ORDER}</span>")
            + "".join(_panel(i, d) for i, d in enumerate(decs))
            + f'<p class="order nojs-only">{ORDER}</p>' + _verdict_html(verdict, vals)
            + _findings_html(deciders, verdict) + "</section>")


# ---------------------------------------------------------------- what the table shows

DISPLAY = {"mem0": "Mem0", "langmem": "LangMem", "graphiti": "Graphiti", "empty": "Empty"}


def _measures(cell: dict) -> dict[str, float | None]:
    """The three printed measures of one cell, as read from its summary: excess wrong action and withheld above
    floor (differences), right action (a share)."""
    def v(k: str) -> float | None:
        x = (cell.get(k) or {}).get("value")
        return None if x is None else float(x)
    return {"ex": v("headline"), "ha": v("abstention_above_floor"), "right": v("right_action")}


def _names(keys: list[tuple]) -> dict[tuple, str]:
    systems = [k[0] for k in keys]
    return {k: DISPLAY.get(k[0], str(k[0])) + (f"/{k[1]}" if systems.count(k[0]) > 1 else "") for k in keys}


def _on(dids: list[str]) -> str:
    return "both deciders" if len(dids) == 2 else (f"decider {dids[0]}" if len(dids) == 1 else f"all {len(dids)} deciders")


def _figures(name: str, m: dict) -> str:
    def pts(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:+.1f} pts"
    return (f"{name} excess wrong action {pts(m['ex'])}, withheld above floor {pts(m['ha'])}, right action "
            + ("n/a" if m["right"] is None else f"{m['right'] * 100:.1f}%"))


def _rule_on_pair(verdict: dict | None, a: tuple, b: tuple, names: dict) -> str:
    """The frozen rule's wrong-action test for this pair, per decider (Holm p), so a point-estimate finding never
    reads as a tested one."""
    by = (verdict or {}).get("by_decider") or {}
    want = {cell_name(*a), cell_name(*b)}
    parts = []
    for did, dd in sorted(by.items()):
        for key, x in (dd.get("pairs") or {}).items():
            if set(key.split(" | ")) == want and x and x.get("holm_p") is not None:
                parts.append(f"{'separates' if x.get('separates') else 'does not separate'} on {did} "
                             f"(Holm p {x['holm_p']:.3f})")
    return (" On wrong action the frozen rule " + "; ".join(parts) + ".") if parts else ""


def _findings(deciders: dict[str, dict], verdict: dict | None) -> list[dict]:
    """Findings generated from the cells: dominance, the trade-offs behind the separating pairs, and the empty
    reference. Each finding is a sentence plus the point estimates it rests on, per decider."""
    dids = sorted(deciders)
    if not dids:
        return []
    cells: dict[tuple, dict[str, dict]] = {}
    for did in dids:
        for key, row in deciders[did]["rows"].items():
            cells.setdefault(key, {})[did] = _measures(row["cell"])
    labels = {key: row["src"]["label"] for did in dids for key, row in deciders[did]["rows"].items()}
    prods = sorted(k for k, by in cells.items() if k[0] not in BASELINES and len(by) == len(dids)
                   and labels.get(k) != "exploratory"
                   and all(None not in m.values() for m in by.values()))
    names = _names(prods)
    on = _on(dids)
    out: list[dict] = []

    def evidence(*keys: tuple) -> list[str]:
        return [f"{did}: " + "; ".join(_figures(names.get(k) or DISPLAY.get(k[0], str(k[0])), cells[k][did])
                                       for k in keys) + "." for did in dids]

    dominated: set[tuple] = set()
    for a in prods:
        for b in prods:
            if a == b:
                continue
            if all(cells[a][d]["ex"] < cells[b][d]["ex"] and cells[a][d]["ha"] < cells[b][d]["ha"]
                   and cells[a][d]["right"] > cells[b][d]["right"] for d in dids):
                dominated.add((a, b))
                out.append({"kind": "dominance",
                            "text": f"{names[a]} is below {names[b]} on wrong action and withheld action. It is "
                                    f"above {names[b]} on right action. This holds on {on}, on point estimates."
                                    + _rule_on_pair(verdict, a, b, names),
                            "evidence": evidence(a, b)})

    by_name = {cell_name(*k): k for k in prods}
    for pair, direction in (verdict or {}).get("pairs") or []:
        x, _, y = pair.partition(" | ")
        lo, hi = (x, y) if direction == "a_lower" else (y, x)
        a, b = by_name.get(lo), by_name.get(hi)
        if a is None or b is None or (a, b) in dominated:
            continue

        def phrase(measure: str, more: str, less: str) -> tuple[str, bool]:
            up = [d for d in dids if cells[a][d][measure] > cells[b][d][measure]]
            down = [d for d in dids if cells[a][d][measure] < cells[b][d][measure]]
            if len(up) == len(dids):
                return more, measure == "ha"
            if len(down) == len(dids):
                return less, measure == "right"
            parts = [f"{more} on {', '.join(up)}" if up else None, f"{less} on {', '.join(down)}" if down else None]
            same = [d for d in dids if d not in up and d not in down]
            parts.append(f"level on {', '.join(same)}" if same else None)
            return " and ".join(p for p in parts if p), bool(up if measure == "ha" else down)

        w, w_bad = phrase("ha", "withholds more", "withholds less")
        r, r_bad = phrase("right", "is right more often", "is right less often")
        link = "but" if (w_bad or r_bad) else "while it"
        out.append({"kind": "trade-off",
                    "text": f"{names[a]} has lower wrong action than {names[b]} on {on}, {link} {w} and {r}.",
                    "evidence": evidence(a, b)})

    empty = [k for k in cells if k[0] == "empty"]
    for k in sorted(empty):
        lab = " It is an exploratory row." if labels.get(k) == "exploratory" else ""
        out.append({"kind": "empty",
                    "text": "The empty memory returns no record. Its row is the reference for pure withholding." + lab,
                    "evidence": [f"{did}: " + _figures("Empty", cells[k][did]) + "." for did in dids if did in cells[k]]})
    return out


def _findings_html(deciders: dict[str, dict], verdict: dict | None) -> str:
    items = _findings(deciders, verdict)
    if not items:
        return ""
    lis = "".join(f'<li data-kind="{E(f["kind"])}"><p>{E(f["text"])}</p>'
                  + "".join(f'<p class="mono">{E(x)}</p>' for x in f["evidence"]) + "</li>" for f in items)
    return ('<div class="findings" id="shows" aria-labelledby="shows-h"><h3 id="shows-h">What the table shows</h3>'
            '<p>Point estimates from each decider\'s summary. Excess wrong action and withheld action are above '
            'the decider\'s floor.</p>'
            f'<ul>{lis}</ul></div>')


# ---------------------------------------------------------------- hero and stat strip

def _hero(vals: dict) -> str:
    decs = vals.get("deciders") or []
    meta = [
        (f"{len(decs)} deciders" if decs else None, " · ".join(map(str, decs))),
        (f'{vals["n_table_cases"]} cases' if vals.get("n_table_cases") is not None else None,
         "synthetic table" + (f' · seed {vals["table_seed"]}' if vals.get("table_seed") is not None else "")),
        (f'{vals["ci_level"] * 100:g}% intervals' if vals.get("ci_level") is not None else None,
         f'paired bootstrap, {vals["boot_reps"]} resamples' if vals.get("boot_reps") is not None else "paired bootstrap"),
    ]
    dl = "".join(f"<div><dt>{E(k)}</dt><dd>{E(v)}</dd></div>" for v, k in meta if v)
    return ('<section class="hero"><div>'
            f'<h1>{E(HOOK[0])} <em>{E(HOOK[1])}</em></h1><p class="hero-lead">{E(SUBLINE)}</p></div>'
            f'<div class="hero-right">' + (f'<dl class="hero-meta">{dl}</dl>' if dl else "")
            + '<div class="cli-box"><p class="cli-top"><span>Run it on your machine</span><span>20 cases · no key</span></p>'
            f'{_job(JOB_CMD)}<p class="cli-note">{E(JOB_NOTE)}</p></div></div></section>')


def _pairs_line(verdict: dict) -> str:
    """The separating pairs as one line: "Graphiti and Mem0 below LangMem on wrong action"."""
    below: dict[str, list[str]] = {}
    for pair, direction in verdict.get("pairs") or []:
        a, _, b = pair.partition(" | ")
        lo, hi = (a, b) if direction == "a_lower" else (b, a)
        below.setdefault(hi, []).append(lo)

    def nm(c: str) -> str:
        s = c.split("/")[0]
        return DISPLAY.get(s, s)
    parts = []
    for hi in sorted(below, key=nm):
        los = sorted({nm(x) for x in below[hi]})
        who = los[0] if len(los) == 1 else ", ".join(los[:-1]) + " and " + los[-1]
        parts.append(f"{who} below {nm(hi)}")
    return "; ".join(parts) + " on wrong action" if parts else "Two deciders agree on at least one pair."


def _kpis(deciders: dict[str, dict], verdict: dict | None, vals: dict) -> str:
    ranked = bool(verdict and verdict["leaderboard"])
    key = _verdict_key(verdict)
    if ranked:
        status = ('<span class="v teal">Rule met: ' + str(len(verdict["pairs"])) + ' pairs separate</span>'
                  f'<small>{E(_pairs_line(verdict))}</small>')
    else:
        status = ('<span class="v stop">No rank shown</span>'
                  f'<small>{E(REASONS.get(key, key).capitalize() + ".")}</small>')
    items = [("Frozen rule result", status)]
    for did, slot in sorted(deciders.items()):
        fl = slot["floor"] or {}
        items.append((f"Floor · {did}", f'<span class="v teal">{E(_pct_t(fl.get("wrong_commit")))}</span>'
                      f'<small>wrong action on the full history; withheld {E(_pct_t(fl.get("abstention")))}</small>'))
    budgets = vals.get("budgets")
    if budgets:
        b = ", ".join(f"{x:,}" for x in budgets)
        rep = vals.get("repeats")
        items.append(("Memory budget", f'<span class="v">{E(b)} tokens</span><small>'
                      + (f"{E(str(rep))} repeats per case; " if rep is not None else "")
                      + "blind adapters</small>"))
    body = "".join(f"<div><dt>{E(k)}</dt><dd>{v}</dd></div>" for k, v in items)
    return f'<dl class="kpis" style="--n:{len(items)}">{body}</dl>'


# ---------------------------------------------------------------- how it works, case anatomy, method

MEDIA = (("how-light.mp4", "how-light.jpg", "light"), ("how-dark.mp4", "how-dark.jpg", "dark"))


def _how(deciders: dict[str, dict], vals: dict, media: set[str]) -> str:
    budgets = vals.get("budgets") or []
    budget = budgets[0] if len(budgets) == 1 else None
    btxt = f"{budget:,} bench tokens" if budget is not None else "the frozen budget"
    names = list(vals.get("deciders") or sorted(deciders))
    dec_lines = []
    for did in names[:2]:
        fl = ((deciders.get(did) or {}).get("floor") or {}).get("wrong_commit")
        dec_lines.append((did, f"floor {_rate(fl)['t']} wrong" if _rate(fl) else "passes the gate first"))
    stages = "".join(
        f'<li class="stage{" on" if k == 0 else ""}" data-s="{k}" data-cap="{E(text.format(budget=btxt))}">'
        f'<h3><span>{k + 1:02d}</span>{E(name.upper())}</h3><p>{E(text.format(budget=btxt))}</p></li>'
        for k, (name, text) in enumerate(STEPS))
    reel_ok = all(v in media and p in media for v, p, _ in MEDIA)
    tabs = ('<div class="studio-bar js-only"><div class="studio-tabs" role="tablist" aria-label="How it works view">'
            '<button class="btn" type="button" role="tab" aria-selected="true" aria-controls="st-step">Stage stepper</button>'
            + ('<button class="btn" type="button" role="tab" aria-selected="false" aria-controls="st-reel">Motion reel</button>'
               if reel_ok else "")
            + '</div><div class="studio-ctl"><button class="btn" type="button" id="st-play">Pause</button>'
            '<button class="btn" type="button" id="st-next">Next stage</button></div></div>')
    step = (f'<div class="studio-stage" id="st-step" role="tabpanel"><div class="flow-wrap">'
            f'{figures.flow_svg(dec_lines, f"{budget:,}" if budget is not None else None)}</div>'
            '</div>')
    reel = ""
    if reel_ok:
        vids = "".join(f'<video class="v-{t}" controls muted playsinline preload="none" poster="media/{p}" '
                       f'aria-label="Motion reel of the five stages, {t} artwork"><source src="media/{v}" type="video/mp4">'
                       f'</video><img class="i-{t}" src="media/{p}" alt="Still of the five stages: records narrow '
                       f'through the memory view to two deciders." loading="lazy" decoding="async" width="1376" height="768">'
                       for v, p, t in MEDIA)
        reel = (f'<div class="studio-stage reel" id="st-reel" role="tabpanel" hidden>{vids}'
                + _reel_note() + '</div>')
    return ('<section class="sec" id="how" aria-labelledby="how-h"><div class="sec-head"><h2 id="how-h">How it works</h2>'
            '<span class="sub">One case moves through five stages. The stepper advances one stage at a time.</span></div>'
            f'<div class="studio" id="studio">{tabs}{step}{reel}<ol class="stage-strip">{stages}</ol></div></section>')


def _dev_case() -> tuple[Any, str, int] | None:
    """The first M3 case of the 300 public development cases (never a table case), its oracle action on the
    history without the decisive records, and the count of development cases."""
    try:
        from ..cases.generator import oracle
        from ..cases.sample import dev_cases
    except ImportError:
        return None
    cases = dev_cases()
    case = next((c for c in cases if c.mechanism == "M3"), cases[0] if cases else None)
    if case is None:
        return None
    reduced = oracle([e for e in case.events if e.id not in set(case.d_star)], case.query_period)
    return case, reduced, len(cases)


def _anatomy() -> str:
    got = _dev_case()
    if not got:
        return ""
    case, reduced, n = got
    return ('<section class="sec" id="anatomy" aria-labelledby="ana-h"><div class="sec-head"><h2 id="ana-h">Case anatomy</h2>'
            f'<span class="sub">Synthetic development case {E(case.case_id)}. One record decides it.</span></div>'
            f'{figures.anatomy_html(case, reduced, n)}</section>')


def _frozen_items(proto: dict) -> list[tuple[str, Any]]:
    """The frozen protocol values as printed pairs. Every value is read from protocol/v1.json."""
    vals = _proto_values(proto)
    sep = vals.get("separation") or {}
    gate = vals.get("validity_gate") or {}

    def pct(x: Any) -> str:
        return f"{x * 100:g} percent"

    def has(*xs: Any) -> bool:
        return all(x is not None for x in xs)

    sha = proto.get("protocol_sha256")
    items = [
        ("Deciders", ", ".join(map(str, vals["deciders"])) if vals.get("deciders") else None),
        ("Budget", ", ".join(f"{b} tokens" for b in vals["budgets"]) if vals.get("budgets") else None),
        ("Repeats", vals.get("repeats")),
        ("Table cases", vals.get("n_table_cases")),
        ("Bootstrap", f'{vals["boot_reps"]} resamples, seed {vals["boot_seed"]}'
         if has(vals.get("boot_reps"), vals.get("boot_seed")) else None),
        ("Interval", pct(vals["ci_level"]) if vals.get("ci_level") is not None else None),
        ("Validity gate", f'{pct(gate["overall"])} overall, {pct(gate["per_mechanism"])} per pattern'
         if has(gate.get("overall"), gate.get("per_mechanism")) else None),
        ("Separation", (f'{sep["test"]}, alpha {sep["alpha"]}' if has(sep.get("test"), sep.get("alpha")) else None)),
        ("Systems", ", ".join(map(str, vals["systems"])) if vals.get("systems") else None),
        ("Table seed", vals.get("table_seed")),
        ("Protocol", f'{proto.get("version") or "v1"} {sha[:8]}' if sha else None),
    ]
    return [(k, v) for k, v in items if v is not None]


def _frozen_block(root: Path) -> str:
    proto = _json(root / "protocol" / "v1.json")
    if not proto:
        return ""
    rows = "".join(f"<div><dt>{k}</dt><dd>{E(str(v))}</dd></div>" for k, v in _frozen_items(proto))
    return f'<h2>Frozen values</h2><dl class="params">{rows}</dl>'


def _method_submit(proto: dict) -> str:
    rows = "".join(f"<div><dt>{k}</dt><dd>{E(str(v))}</dd></div>" for k, v in _frozen_items(proto)
                   if k in ("Deciders", "Budget", "Repeats", "Bootstrap", "Separation", "Systems", "Protocol"))
    labels = ", ".join(k for k, _ in LABELS)
    return ('<section class="sec" id="protocol" aria-labelledby="proto-h"><div class="sec-head">'
            '<h2 id="proto-h">Method and submissions</h2><span class="sub">Every number on this page traces to a '
            'verified summary file and the frozen protocol.</span></div><div class="two-col">'
            '<div><h3>Frozen before the table run</h3><p>Metric definitions, deciders, adapter settings, the '
            'separation rule and the table seed were hashed before any table run. All v1 cases are synthetic.</p>'
            + (f'<dl class="dl-tight">{rows}</dl>' if rows else "")
            + '<p style="margin-top:14px"><a class="more" href="method.html">Read the method</a></p></div>'
            '<div><h3>Submit your memory</h3><p>Write the four-method blind adapter (reset, ingest, view, close), '
            'run the bench, verify the files and open a pull request. A vendor-tuned configuration is run by the maintainer and '
            'published as its own row, labelled exploratory.</p>' + "".join(_job(c) for c in SUBMIT_JOBS)
            + f'<p class="mono" style="font-size:12.5px">Row labels: {E(labels)}.</p>'
            '<p><a class="more" href="submit.html">How to submit a run</a></p></div></div></section>')


# ---------------------------------------------------------------- pages

def _copy_block(root: Path, name: str) -> str:
    p = root / "site" / "copy" / name
    try:
        return mdlite.render(p.read_text(encoding="utf-8"))
    except OSError:
        return f'<div class="pending" role="note"><strong>copy pending</strong><code>site/copy/{E(name)}</code></div>'


BRAND_DIR = Path(__file__).resolve().parents[3] / "site" / "brand"
MEDIA_DIR = Path(__file__).resolve().parents[3] / "site" / "media"


def _brand_assets() -> tuple[str, str]:
    """(inline mark for the rail, favicon link). The mark and icon are hand-drawn SVGs in site/brand/
    (Varma's concepts 1 and 2, 2026-11-19); missing files give a text-only header."""
    import base64
    import re as _re

    mark, icon = BRAND_DIR / "hercyna-mark.svg", BRAND_DIR / "hercyna-icon.svg"
    m = ""
    if mark.exists():
        svg = _re.sub(r"<title[^>]*>.*?</title>", "", mark.read_text(encoding="utf-8"), flags=_re.S)
        svg = svg.replace(' role="img" aria-labelledby="t"', ' aria-hidden="true" focusable="false"')
        m = svg.replace("<svg ", '<svg class="mark" ', 1).strip()
    f = ""
    if icon.exists():
        f = ('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,'
             + base64.b64encode(icon.read_bytes()).decode("ascii") + '">')
    return m, f


def _page(title: str, current: str, main: str, proto: dict, built_at: str) -> str:
    doc_title = f"{BRAND} – {title}"
    sha = proto.get("protocol_sha256")
    ver = str(proto.get("version") or "v1")
    seed = _proto_values(proto).get("table_seed")
    pre = "" if current == "index.html" else "index.html"
    links = ((f"{pre}#board", "Board", False), (f"{pre}#how", "How it works", False),
             (f"{pre}#anatomy", "Case anatomy", False),
             ("method.html", "Method", current == "method.html"), ("submit.html", "Submit", current == "submit.html"),
             ("why.html", "Why Hercyna", current == "why.html"), ("about.html", "About", current == "about.html"))
    nav = "".join(f'<a href="{h}"' + (' aria-current="page"' if cur else "") + f">{label}</a>"
                  for h, label, cur in links)
    mark, favicon = _brand_assets()
    hash8 = f" · {E(sha[:8])}" if sha else ""
    rail = (f'<aside class="rail"><a class="rail-brand" href="index.html">{mark}<span><b>{BRAND}</b>'
            f'<small>Protocol {E(ver)}{hash8}</small></span></a><nav class="rail-nav" aria-label="Site">{nav}</nav>'
            '<div class="rail-foot"><span>All v1 cases are synthetic.</span>'
            f'<span>built {E(built_at[:10])}</span></div></aside>')
    ribbon = ('<header class="ribbon"><div class="ribbon-left">'
              f'<span class="chip"><i></i>Protocol {E(ver)} frozen</span>'
              + (f'<span class="hash">hash {E(sha[:8])}</span>' if sha else "")
              + (f'<span class="seed">table seed {E(str(seed))}</span>' if seed is not None else "")
              + '</div><div class="ribbon-right">'
              + _copy_btn(JOB_CMD, "Copy quickstart", "btn solid q-copy")
              + f'<button class="btn js-only" type="button" id="theme" aria-pressed="false">{THEME_ICON}'
              '<span class="tl">Dark</span></button></div></header>')
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{E(doc_title)}</title><meta name=\"color-scheme\" content=\"light dark\">"
            "<meta name=\"description\" content=\"Hercyna scores agent memory products by the decisions an agent "
            "makes from what they return.\">"
            f"<script>{HEAD_JS}</script>{favicon}{FONT_LINK}<style>{CSS}</style></head><body>"
            '<a class="skip" href="#main">Skip to content</a><div class="shell">' + rail
            + '<div class="main-col">' + ribbon + f'<main id="main" class="wrap">{main}'
            + _footer(proto, built_at) + "</main></div></div>"
            f'<p class="vh" id="live" aria-live="polite"></p><script>{JS}</script></body></html>\n')


def _footer(proto: dict, built_at: str) -> str:
    sha = proto.get("protocol_sha256")
    ver = str(proto.get("version") or "v1")
    return ('<footer class="foot"><span>Hercyna · named for the gorge where Lethe (forgetting) and Mnemosyne '
            '(memory) flow side by side. All v1 cases are synthetic.</span>'
            f'<span>protocol {E(ver)}' + (f" {E(sha[:8])}" if sha else "") + f' · built {E(built_at[:10])}</span>'
            '<nav aria-label="Footer"><a href="method.html">Method</a><a href="submit.html">Submit</a></nav></footer>')


def _doc(title: str, prose: str, aside: str) -> str:
    """Prose in a 70ch column; the printed block (frozen values or commands) in the right column."""
    return (f'<div class="doc"><h1>{E(title)}</h1><div class="doc-g"><div class="col">{prose}</div>'
            f'<aside class="aside">{aside}</aside></div></div>')


def _why_aside() -> str:
    mark, _ = _brand_assets()
    return '<div class="why-mark" aria-hidden="true">' + mark + '</div>'


def _why(root: Path) -> str:
    """The name: the myth of the twin springs and why it fits (site/copy/why.md, written in house)."""
    mark, _ = _brand_assets()
    return ('<section class="sec" id="why" aria-labelledby="why-h"><div class="sec-head"><h2 id="why-h">Why Hercyna</h2>'
            '</div><div class="why-g"><div class="why-mark" aria-hidden="true">' + mark + '</div><div class="col">'
            + _copy_block(root, COPY["why"])
            + '<p><a class="more" href="about.html">Who builds Hercyna</a></p></div></div></section>')


ABOUT_LINKS = (
    ("The Notebook", "https://notebook.beer", "notebook.beer"),
    ("World Model Atlas", "https://world-model.notebook.beer", "world-model.notebook.beer"),
    ("Think and Reason", "https://thinkandreason.org", "thinkandreason.org"),
    ("ORCID", "https://orcid.org/0009-0008-2191-6575", "0009-0008-2191-6575"),
    ("OpenReview", "https://openreview.net/profile?id=~FNU_Ratnavarma1", "~FNU_Ratnavarma1"),
    ("LinkedIn", "https://www.linkedin.com/in/ratnavarma", "in/ratnavarma"),
)


def _about_aside() -> str:
    img = ('<img class="portrait" src="media/portrait.jpg" width="320" height="320" alt="FNU Ratnavarma">'
           if (MEDIA_DIR / "portrait.jpg").is_file() else "")
    rows = "".join(f'<div><dt>{E(k)}</dt><dd><a href="{E(u)}" rel="noopener">{E(t)}</a></dd></div>'
                   for k, u, t in ABOUT_LINKS)
    return img + f'<h2>Elsewhere</h2><dl class="params">{rows}</dl>'


def _reel_note() -> str:
    """The reel's caption comes with the media (site/media/reel-note.txt), so it ships only where the reel does."""
    p = MEDIA_DIR / "reel-note.txt"
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f'<p class="reel-note">{E(text)}</p>' if text else ""


def _media(out: Path) -> dict[str, int]:
    """Copy site/media into the output (the posters and reels, each carrying its copyright mark)."""
    if not MEDIA_DIR.is_dir():
        return {}
    dest = out / "media"
    sizes = {}
    for p in sorted(MEDIA_DIR.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest / p.name)
            sizes[f"media/{p.name}"] = p.stat().st_size
    return sizes


def build(root: Path, out: Path, *, include_exploratory: bool = False, built_at: str | None = None) -> dict:
    root, out = Path(root), Path(out)
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    proto = _json(root / "protocol" / "v1.json") or {}
    vals = dict(_proto_values(proto), _version=proto.get("version") or "v1")
    sources = _sources(root, include_exploratory)
    MEMORY_COST.clear()
    MEMORY_COST.update(_memory_costs(sources))
    deciders = _entries(sources)
    verdict = _rule_l(deciders) if deciders else None
    media = {p.name for p in MEDIA_DIR.iterdir()} if MEDIA_DIR.is_dir() else set()
    index = (_hero(vals) + _kpis(deciders, verdict, vals) + _board(sources, deciders, verdict, vals)
             + _how(deciders, vals, media) + _anatomy() + _method_submit(proto))
    submit_cmds = '<h2>Commands</h2>' + "".join(_job(c) for c in SUBMIT_COMMANDS)
    pages = {
        "index.html": _page("Board", "index.html", index, proto, built_at),
        "method.html": _page("Method", "method.html",
                             _doc("Method", _copy_block(root, COPY["method.html"]) + _copy_block(root, COPY["limits"]),
                                  _frozen_block(root)), proto, built_at),
        "submit.html": _page("Submit", "submit.html",
                             _doc("Submit", _copy_block(root, COPY["submit.html"]), submit_cmds), proto, built_at),
        "why.html": _page("Why Hercyna", "why.html",
                          _doc("Why Hercyna", _copy_block(root, COPY["why"])
                               + '<p><a class="more" href="about.html">Who builds Hercyna</a></p>', _why_aside()),
                          proto, built_at),
        "about.html": _page("About", "about.html",
                            _doc("About", _copy_block(root, COPY["about.html"]), _about_aside()), proto, built_at),
    }
    out.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, text in pages.items():
        data = text.encode("utf-8")
        (out / name).write_bytes(data)
        sizes[name] = len(data)
    sizes.update(_media(out))
    return {"out": str(out), "bytes": sizes, "sources": [s["id"] for s in sources]}
