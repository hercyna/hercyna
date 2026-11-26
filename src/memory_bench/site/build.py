"""bench site build: read maintainer runs and verified submissions, write three static pages.

The site never computes a rate. Rates come from each run's summary.json, the rank verdict and the pair
differences from scoring.separation, dollars per case from spend.json over the case count. Method and submit
prose comes from site/copy/*.md; the landing lines are constants below (site copy is written in-house).

index.html is the Observatory layout (2026-11-20): a left rail, the hook and the stat strip,
the board beside its 2D frontier and run audit, the verdict with its why-no-rank explainer, the How it works
studio with the stage stepper, one case anatomy, then method and submit. Every decider's rows, its frontier
and every audit are printed statically, so the page reads without script.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import terms as _terms_mod
from . import assets_gen, figures, mdlite
from .script import HEAD_JS, JS
from .style import CSS, FONT_LINK

from ..scoring.metrics import cell_name

# Board order after the products, and never compared by the rule. `empty` (Day 14) is the pure-withholding reference.
BASELINES = (
    "full",
    "bm25",
    "recent",
    "empty",
    "dense_raw",
    "hybrid_bm25_dense",
    "always_act",
    "always_request_info",
)

BRAND = "Hercyna"
COPY = {"method.html": "method.md", "submit.html": "submit.md", "about.html": "about.md", "why": "why.md",
        "limits": "limits.md"}
E = html.escape


# ---------------------------------------------------------------- verify hook

MAX_EXCUSED_SHARE = 0.05


def _over_excused_limit(ingest_errors: int, n_cases: int) -> bool:
    return n_cases > 0 and ingest_errors * 20 > n_cases


def _verified(d: Path, root: Path) -> tuple[bool, str, bool]:
    """(passes verify, grade, over_excused_limit). Defaults are resolved against the repo root."""
    try:
        from memory_bench.submit.verify import verify
    except ImportError:
        return False, "", False
    # The table cases are the label check's default only for a table-grade submission (docs/submission.md
    # section 3, check 4). An exploratory submission brings its own cases, so passing them would hide it.
    man = _json(d / "manifest.json") or {}
    cases = root / "data" / "table" / "cases.jsonl"
    use = cases if man.get("grade") == "table" and cases.is_file() else None
    res = verify(d, root / "protocol" / "v1.json", use, runs=root / "results" / "runs")
    return bool(res.ok), str(res.grade or ""), bool(res.excused_over_limit)


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
        ok, grade, _over = _verified(d, root)
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
JOB_CMD = "uv run bench quickstart --mock --accept-terms"
JOB_NOTE = ("No key, no cost: runs the 20-case check with the rule oracle. "
            "Drop --mock and pass --decider to score for real.")
EMPTY = "No table run yet. The first full run prints here."
SUBMIT_JOBS = (
    "bench submit pack <run> --out submissions --accept-terms",
    "bench verify submissions/<system>/<date>-<slug> --accept-terms",
)
SUBMIT_COMMANDS = (
    "bench submit pack RUN --out DIR [--slug S] --accept-terms",
    "bench verify DIR --accept-terms",
)
BIBTEX_ENTRY = (
    "@software{ratnavarma2026hercyna,\n"
    "  author = {Ratnavarma, FNU},\n"
    "  title = {Hercyna: Decision-Scored Agent Memory Benchmark},\n"
    "  version = {2.0.0-rc1},\n"
    "  year = {2026},\n"
    "  url = {https://github.com/hercyna/hercyna},\n"
    "  note = {Experimental results. See TERMS.md.}\n"
    "}"
)


def _valid_https_url(raw: str | None) -> str | None:
    """Validate an optional external HTTPS endpoint (newsletter action or analytics script); return None if unsafe."""
    s = (raw or "").strip()
    if not s:
        return None
    if not s.startswith("https://"):
        return None
    if any(ch in s for ch in ('"', "'", "<", ">", " ", "\t", "\r", "\n")):
        return None
    return s


def _valid_site_url(raw: str | None) -> str:
    s = (raw or "").strip().rstrip("/")
    if (s.startswith("https://") or s.startswith("http://")) and not any(
        ch in s for ch in ('"', "'", "<", ">", " ", "\t", "\r", "\n")
    ):
        return s
    return "https://hercyna.org"
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
        "pack_href": f'packs/v1/{_slug(str(r["src"]["id"]))}/summary.json',
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
        out.append({"id": did, "model": _model(slot, did), "floor": slot.get("floor"),
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


def _copy_btn(cmd: str, label: str = "Copy", cls: str = "btn solid", *, gate_id: str | None = None) -> str:
    gate = f' data-terms-target="{E(gate_id)}"' if gate_id else ""
    return (f'<button class="{cls} copy js-only" type="button" data-copy="{E(cmd)}"{gate} aria-label="{E(label)}: {E(cmd)}">'
            f'{COPY_ICON}<span class="cl">{E(label)}</span></button>')


def _job(cmd: str, *, gate_id: str | None = None) -> str:
    return f'<p class="cli-cmd"><code>{E(cmd)}</code>{_copy_btn(cmd, gate_id=gate_id)}</p>'


def _render_terms_point(point: str) -> str:
    """Strip leading list numbering and convert simple markdown links to safe HTML anchors."""
    clean = re.sub(r"^[1-6]\.\s+", "", point.strip())
    out: list[str] = []
    pos = 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", clean):
        out.append(E(clean[pos:m.start()]))
        label, href = m.group(1), m.group(2)
        if href == ".github/ISSUE_TEMPLATE/correction_request.md":
            href = "https://github.com/hercyna/hercyna/issues/new?template=correction_request.md"
        extra = ' rel="noopener"' if href.startswith("https://") else ""
        out.append(f'<a href="{E(href)}"{extra}>{E(label)}</a>')
        pos = m.end()
    out.append(E(clean[pos:]))
    return "".join(out)


def _terms_gate_box(root: Path | None, gate_id: str, heading: str = "Experimental disclaimer") -> str:
    """Single-source disclaimer + required checkbox gating CLI copy actions (read from TERMS.md)."""
    ver, points = _terms_mod.load_terms_spec(root)
    lis = "".join(f"<li>{_render_terms_point(pt)}</li>" for pt in points)
    return (
        f'<div class="terms-gate" data-terms-ver="{E(ver)}">'
        f'<h3>{E(heading)}</h3>'
        f'<ol class="terms-list">{lis}</ol>'
        f'<label class="terms-check" for="{E(gate_id)}">'
        f'<input type="checkbox" class="terms-ack" id="{E(gate_id)}">'
        '<span>I have read the <a href="terms.html">terms</a> and <a href="legal.html">legal notice</a>.</span>'
        '</label></div>'
    )


def _disclaimer_bar(root: Path | None) -> str:
    ver, points = _terms_mod.load_terms_spec(root)
    lead = re.sub(r"^[1-6]\.\s+", "", points[0]) if points else "Hercyna is an experimental research benchmark."
    return (
        f'<div class="disc-bar" role="note" data-terms-ver="{E(ver)}">'
        f'<span><strong>Experimental benchmark:</strong> {E(lead)} Results describe fixed synthetic cases only.</span>'
        '<span class="disc-links">'
        '<a href="terms.html">Terms</a>'
        '<a href="legal.html">Legal</a>'
        '<a href="privacy.html">Privacy</a>'
        '<a href="corrections.html">Corrections</a>'
        '</span></div>'
    )


def _pct_t(s: dict | None) -> str:
    r = _rate(s)
    return "n/a" if not r else r["t"] + (f' {r["iv"]}' if r["iv"] else "")


# ---------------------------------------------------------------- board

def _bar(row: dict) -> str:
    parts = (("right", "r", row["right"]), ("withheld", "h", row["held"]), ("wrong", "w", row["wrong"]))
    x = 0.0
    rects: list[str] = []
    for _, c, p in parts:
        if not p:
            continue
        w = float(p["w"])
        if w <= 0:
            continue
        if x > 0:
            rects.append(f'<line class="bar-sep" x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="11"/>')
        rects.append(f'<rect class="p-{c}" x="{x:.2f}" y="0" width="{w:.2f}" height="11"/>')
        x += w
    nums = "".join(f'<span class="{c}">{E(p["t"]) if p else "n/a"}</span>' for name, c, p in parts)
    aria = ", ".join(f'{name} {p["t"] if p else "n/a"}' for name, _, p in parts)
    return (f'<svg class="bar-svg" viewBox="0 0 100 11" preserveAspectRatio="none" role="img" '
            f'aria-label="Share of decisions: {E(aria)}">{"".join(rects)}</svg>'
            f'<div class="bar-nums">{nums}</div>')


def _num(p: dict | None, key: str, *, href: str | None = None, protocol: str = "v1") -> str:
    if not p:
        return f'<div class="num" data-protocol="{E(protocol)}" data-k="{key}"><b>n/a</b></div>'
    inner = f'<b>{E(p["t"])}</b>' + (f'<small>{E(p["iv"])}</small>' if p["iv"] else "")
    if href:
        inner = f'<a class="pack-link" href="{E(href)}">{inner}</a>'
    return f'<div class="num" data-protocol="{E(protocol)}" data-k="{key}">{inner}</div>'


def _band(row: dict, d: int, i: int) -> str:
    """One board row. The system cell carries the row label; a no-script line repeats the audit facts."""
    lab = row["label"]
    href = row.get("pack_href")
    meta = (f'<span>{E(row["config"])}</span><span class="lab{" x" if lab == "exploratory" else ""}">{E(lab)}</span>'
            + "".join(f"<span>{E(a)}</span>" for a in row["also"]))
    n = "n/a" if row["n"] is None else E(str(row["n"]))
    n_html = (
        f'<b>{n}</b>'
        + (f'<small><a class="pack-link" href="{E(href)}">pack</a></small>'
           if (href and row["n"] is not None) else "")
    )
    nojs = " · ".join(x for x in (
        f'view {E(row["view"])} tokens' if row["view"] else None,
        f'{E(row["usd"])} per case' if row["usd"] else None,
        f'version {E(row["ver"])}' if row["ver"] else None,
        f'run {E(row["run"])}') if x)
    return (f'<tr class="row" data-d="{d}" data-r="{i}" data-base="{1 if row["base"] else 0}" data-label="{E(lab)}">'
            f'<td class="c-s"><div class="sys-t"><span class="sys">{E(row["system"])}</span></div>'
            f'<div class="sys-m">{meta}</div><div class="nojs-meta nojs-only">{nojs}</div></td>'
            f'<td class="c-b">{_bar(row)}</td>'
            f'<td class="c-e r">{_num(row["ex"], "Excess wrong action", href=href)}</td>'
            f'<td class="c-h r">{_num(row["ha"], "Withheld above floor", href=href)}</td>'
            f'<td class="c-n r"><div class="num" data-protocol="v1" data-k="Cases">{n_html}</div></td></tr>')


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
            f'{E(lab)}</span></div>{figures.whisker_svg(row["ex"], row["ha"])}<dl class="dl-tight" data-protocol="v1">{dl}</dl></div>')


def _panel(i: int, dec: dict) -> str:
    sid = _slug(dec["id"])
    model = f' <span>· {E(dec["model"])}</span>' if dec["model"] else ""
    rows = "".join(_band(r, i, j) for j, r in enumerate(dec["rows"]))
    audits = "".join(_audit(r, j, j == 0) for j, r in enumerate(dec["rows"]))
    plotted = sum(1 for r in dec["rows"] if r["ex"] and r["ha"])
    note = ("" if plotted == len(dec["rows"]) else
            f'<p class="lens-note">{len(dec["rows"]) - plotted} row(s) lack a value on one axis and are not drawn.</p>')
    fl = dec.get("floor") or {}
    fl_row = ""
    if fl:
        fl_row = (f'<tr class="tbl-floor-row"><th scope="colgroup" colspan="5">'
                  f'<b>Floor · {E(dec["id"])}</b> (full history): '
                  f'wrong action <b>{E(_pct_t(fl.get("wrong_commit")))}</b> · '
                  f'withheld action <b>{E(_pct_t(fl.get("abstention")))}</b> · '
                  f'right action <b>{E(_pct_t(fl.get("right_action")))}</b></th></tr>')
    return (f'<div class="dpanel" id="d-{sid}"><h3 class="dhead nojs-only">{E(dec["id"])}{model}</h3>'
            f'<div class="obs-split"><div class="board-col"><table class="tbl" data-protocol="v1"><thead>{fl_row}<tr class="tbl-cols"><th scope="col">System</th>'
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
        out.append(f'<h3>{E(did)}</h3><table><thead><tr><th scope="col">Pair a | b</th><th scope="col" class="r">Wrong action, a minus b</th>'
                   '<th scope="col" class="r">Holm p</th><th scope="col">Lower wrong action</th><th scope="col">Result</th></tr></thead>'
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
        return (f'<details class="verdict ok" id="why" data-protocol="v1" data-verdict="leaderboard"><summary><strong>Frozen verdict: leaderboard</strong>'
                f'<span class="v-t">– {E(pairs)}</span>'
                f'<span class="v-more">What the frozen rule found{CHEVRON}</span></summary>'
                f'<div class="why">{body}</div></details>')
    key = _verdict_key(v)
    reason = REASONS.get(key, (v or {}).get("verdict") or key)
    body = f'<p>{_rule_text(vals, alpha)}</p><p><strong>Here:</strong> {E(WHY.get(key, reason))}</p>'
    if v:
        body += _pairs_table(v)
    return (f'<details class="verdict stop" id="why" data-protocol="v1" data-verdict="{E(key)}"><summary><strong>No rank</strong>'
            f'<span class="v-t">– {E(reason[:1].upper() + reason[1:])}.</span>'
            f'<span class="v-more">Why no rank is published{CHEVRON}</span></summary>'
            f'<div class="why">{body}</div></details>')


ORDER = ("Order: fixed. Products A to Z, then baselines. No rank numbers: a product can lower its wrong action "
         "by withholding, so read wrong action and withheld action together.")


def _board(sources: list[dict], deciders: dict[str, dict], verdict: dict | None, vals: dict) -> str:
    head = ('<section class="sec" id="board" aria-labelledby="board-h"><div class="sec-head">'
            '<h2 id="board-h">The board</h2><span class="sub"><span class="hover-only">Hover or select</span>'
            '<span class="touch-only">Select</span> a row to find it in the frontier '
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
    return ('<div class="findings" id="shows" data-protocol="v1" aria-labelledby="shows-h"><h3 id="shows-h">What the table shows</h3>'
            '<p>Point estimates from each decider\'s summary. Excess wrong action and withheld action are above '
            'the decider\'s floor.</p>'
            f'<ul>{lis}</ul></div>')


# ---------------------------------------------------------------- hero and stat strip

def _hero(vals: dict, *, has_v2_chat: bool = False, root: Path | None = None) -> str:
    decs = vals.get("deciders") or []
    meta = [
        (f"{len(decs)} deciders" if decs else None, " · ".join(map(str, decs))),
        (f'{vals["n_table_cases"]} cases' if vals.get("n_table_cases") is not None else None,
         "synthetic table" + (f' · seed {vals["table_seed"]}' if vals.get("table_seed") is not None else "")),
        (f'{vals["ci_level"] * 100:g}% intervals' if vals.get("ci_level") is not None else None,
         f'paired bootstrap, {vals["boot_reps"]} resamples' if vals.get("boot_reps") is not None else "paired bootstrap"),
    ]
    dl = "".join(f"<div><dt>{E(k)}</dt><dd>{E(v)}</dd></div>" for v, k in meta if v)
    finding = (
        "Across 5 gate-passing deciders and 300 chat cases, Graphiti makes fewer wrong actions than Mem0 mainly by withholding when facts are missing."
        if has_v2_chat
        else "Wrong action, withheld action, and right action are measured side by side above each decider's full-history noise floor."
    )
    table_href = "chat.html#chat-board" if has_v2_chat else "#board"
    return ('<section class="hero"><div>'
            f'<h1>{E(HOOK[0])} <em>{E(HOOK[1])}</em></h1>'
            f'<p class="hero-lead">{E(SUBLINE)}</p>'
            f'<p class="hero-finding">{E(finding)}</p>'
            f'<div class="hero-cta"><a class="btn solid" href="{table_href}">See the table</a>'
            '<a class="btn" href="submit.html">Submit your memory</a></div></div>'
            f'<div class="hero-right">' + (f'<dl class="hero-meta" data-protocol="v1">{dl}</dl>' if dl else "")
            + '<div class="cli-box"><p class="cli-top"><span>Run it on your machine</span><span>20 cases · no key</span></p>'
            + _terms_gate_box(root, "terms-hero", "Terms notice (tick before copy)")
            + f'{_job(JOB_CMD, gate_id="terms-hero")}<p class="cli-note">{E(JOB_NOTE)}</p></div></div></section>')


def _hero_v2(loaded: dict, proto_v2: dict | None, *, root: Path | None = None) -> str:
    v2_vals = _proto_values(proto_v2 or {})
    decs = sorted((loaded.get("deciders") or {}).keys())
    seed = v2_vals.get("table_seed", 2026112302)
    n_cases = v2_vals.get("n_table_cases", 300)
    boot_reps = v2_vals.get("boot_reps", 1000)
    meta = [
        (f"{len(decs)} deciders", " · ".join(decs)),
        (f"{n_cases} cases", f"synthetic chat · seed {seed}"),
        ("95% intervals", f"paired bootstrap, {boot_reps} resamples"),
    ]
    dl = "".join(f"<div><dt>{E(k)}</dt><dd>{E(v)}</dd></div>" for v, k in meta)
    finding = (
        "Across 5 gate-passing deciders and 300 chat cases, Graphiti makes fewer wrong actions than Mem0 "
        "mainly by withholding when facts are missing."
    )
    return (
        '<section class="hero"><div>'
        f'<h1>{E(HOOK[0])} <em>{E(HOOK[1])}</em></h1>'
        f'<p class="hero-lead">{E(SUBLINE)}</p>'
        f'<p class="hero-finding">{E(finding)}</p>'
        '<div class="hero-cta"><a class="btn solid" href="chat.html#chat-board">See the table</a>'
        '<a class="btn" href="submit.html">Submit your memory</a></div></div>'
        f'<div class="hero-right"><dl class="hero-meta" data-protocol="v2">{dl}</dl>'
        '<div class="cli-box"><p class="cli-top"><span>Run it on your machine</span><span>20 cases · no key</span></p>'
        + _terms_gate_box(root, "terms-hero", "Terms notice (tick before copy)")
        + f'{_job(JOB_CMD, gate_id="terms-hero")}<p class="cli-note">{E(JOB_NOTE)}</p></div></div></section>'
    )


def _coming_next_section() -> str:
    """Brief scope preview for the next held track (zero dates, zero numbers)."""
    return (
        '<section class="sec" id="coming-next" aria-labelledby="coming-h">'
        '<div class="coming-next">'
        '<div><span class="lab x">in preparation</span>'
        '<h2 id="coming-h">Coding agents (Mode A), in preparation</h2>'
        '<p>Same five-choice blind-adapter evaluation across multi-file repository tasks and bounded tool sweeps, '
        'partitioned strictly from multi-session chat runs.</p></div>'
        '<a class="more" href="coding.html">View Coding design</a>'
        '</div></section>'
    )


def _explainer_section(media: set[str]) -> str:
    """29-second walkthrough video slot with poster, lazy video element, captions track, and full transcript."""
    src_html = '<source src="media/explainer.mp4" type="video/mp4">' if "explainer.mp4" in media else ""
    clip_note = (
        "29-second walkthrough video, captions, and transcript."
        if "explainer.mp4" in media
        else "Poster, synchronized WebVTT captions, and transcript are live; the 29-second video clip is held for the maintainer upload."
    )
    return (
        '<section class="sec" id="explainer" aria-labelledby="exp-h" data-protocol="v2">'
        '<div class="sec-head"><h2 id="exp-h">29-second walkthrough</h2>'
        f'<span class="sub">{E(clip_note)}</span></div>'
        '<div class="explainer-box">'
        '<video class="explainer-media" controls muted playsinline preload="none" poster="media/explainer-poster.svg" '
        'width="1280" height="720" aria-label="29-second walkthrough of the Hercyna evaluation flow">'
        f'{src_html}<track kind="captions" src="media/explainer.vtt" srclang="en" label="English" default>'
        '<img src="media/explainer-poster.svg" alt="Poster diagram: 55 to 190 dated turns pass through memory and a 1,000-token view to five deciders." width="1280" height="720">'
        '</video>'
        '<p class="reel-note">Poster diagram and caption file prepared for the walkthrough; '
        'caption track at <a href="media/explainer.vtt">media/explainer.vtt</a>.</p>'
        '<details class="explainer-transcript"><summary>Read full 29-second transcript</summary>'
        '<ol class="terms-list">'
        '<li><strong>00:00 to 00:05:</strong> Hercyna tests agent memory products by the action an agent chooses after reading a truncated view.</li>'
        '<li><strong>00:05 to 00:11:</strong> Each multi-session chat case starts with 55 to 190 dated turns across simulated weeks.</li>'
        '<li><strong>00:11 to 00:17:</strong> Every memory adapter ingests the history in order and returns at most 1,000 view tokens.</li>'
        '<li><strong>00:17 to 00:23:</strong> Five gate-passing decider models read only that view and select one of five discrete actions.</li>'
        '<li><strong>00:23 to 00:29:</strong> Every row reports wrong action, withheld action, right action, floor noise, and left-out ingest errors together.</li>'
        '</ol></details></div></section>'
    )


def _v2_kpis(loaded: dict) -> str:
    decs = loaded.get("deciders") or {}
    sep = loaded.get("separation") or {}
    pairs = sep.get("separating_pairs") or []
    if sep.get("leaderboard") and pairs:
        pair_txt = "; ".join(_pair_words(str(p["pair"]), str(p["direction"])) for p in pairs)
        status = (
            f'<span class="v teal">Rule met: {len(pairs)} pair{"s" if len(pairs) != 1 else ""} separate</span>'
            f'<small>{E(pair_txt)} (exploratory)</small>'
        )
    else:
        status = '<span class="v stop">No rank shown</span><small>No product pair separates across all gate-passers.</small>'

    fl_wr = []
    gated_counts = []
    for slot in decs.values():
        w = (((slot.get("floor") or {}).get("wrong_commit") or {}).get("value"))
        if isinstance(w, (int, float)):
            fl_wr.append(100.0 * float(w))
        if isinstance(slot.get("n_gated"), int):
            gated_counts.append(slot["n_gated"])
    fl_range = f"{min(fl_wr):.1f}% to {max(fl_wr):.1f}%" if fl_wr else "n/a"
    g_range = f"{min(gated_counts)} to {max(gated_counts)} gated" if gated_counts else "gated"

    items = [
        ("Frozen v2 rule", status),
        (
            f"{len(decs)} gate-passing deciders",
            f'<span class="v teal">{E(fl_range)}</span>'
            f'<small>full-history floor wrong action ({E(g_range)} of 300)</small>',
        ),
        (
            "Chat Track &amp; budget",
            '<span class="v">300 cases · 1,000 tokens</span>'
            '<small>synthetic chat_agent · 3 repeats · blind adapters</small>',
        ),
        (
            "Ingest errors left out",
            '<span class="v">Mem0 2 · Graphiti 13</span>'
            '<small>excluded from decision rates; 5.0% hard cap</small>',
        ),
    ]
    body = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in items)
    return f'<dl class="kpis" data-n="{len(items)}" data-protocol="v2">{body}</dl>'


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
    return f'<dl class="kpis" data-n="{len(items)}" data-protocol="v1">{body}</dl>'


# ---------------------------------------------------------------- how it works, case anatomy, method

MEDIA = (("how-light.mp4", "how-light.jpg", "light"), ("how-dark.mp4", "how-dark.jpg", "dark"))


def _how_v2(loaded: dict, proto_v2: dict | None, media: set[str]) -> str:
    """Five-stage How it works studio populated strictly from v2 chat deciders (no v1 grok-4-20-r)."""
    deciders = loaded.get("deciders") or {}
    names = sorted(deciders)
    btxt = "1,000 bench tokens"
    dec_lines = []
    for did in names[:2]:
        fl = ((deciders.get(did) or {}).get("floor") or {}).get("wrong_commit")
        r = _rate(fl)
        dec_lines.append((did, f"floor {r['t']} wrong" if r else "passes the gate first"))
    stages = "".join(
        f'<div class="stage{" on" if k == 0 else ""}" data-s="{k}" data-cap="{E(text.format(budget=btxt))}">'
        f'<h3><span>{k + 1:02d}</span>{E(name.upper())}</h3><p>{E(text.format(budget=btxt))}</p></div>'
        for k, (name, text) in enumerate(STEPS))
    reel_ok = all(v in media and p in media for v, p, _ in MEDIA)
    tabs = ('<div class="studio-bar js-only"><div class="studio-tabs" role="tablist" aria-label="How it works view">'
            '<button class="btn" type="button" role="tab" aria-selected="true" aria-controls="st-step">Stage stepper</button>'
            + ('<button class="btn" type="button" role="tab" aria-selected="false" aria-controls="st-reel">Motion reel</button>'
               if reel_ok else "")
            + '</div><div class="studio-ctl"><button class="btn" type="button" id="st-play">Pause</button>'
            '<button class="btn" type="button" id="st-next">Next stage</button></div></div>')
    step = (f'<div class="studio-stage" id="st-step" role="tabpanel"><div class="flow-wrap">'
            f'{figures.flow_svg(dec_lines, "1,000")}</div></div>')
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
            '<span class="sub">One multi-session chat case moves through five stages. The stepper advances one stage at a time.</span></div>'
            f'<div class="studio" id="studio" data-protocol="v2">{tabs}{step}{reel}'
            f'<div class="stage-strip" role="group" aria-label="Five evaluation stages">{stages}</div></div></section>')


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
        f'<div class="stage{" on" if k == 0 else ""}" data-s="{k}" data-cap="{E(text.format(budget=btxt))}">'
        f'<h3><span>{k + 1:02d}</span>{E(name.upper())}</h3><p>{E(text.format(budget=btxt))}</p></div>'
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
            f'<div class="studio" id="studio" data-protocol="v1">{tabs}{step}{reel}<div class="stage-strip" role="group" aria-label="Five evaluation stages">{stages}</div></div></section>')


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


def _frozen_items(proto: dict, *, decider_ids: list[str] | None = None) -> list[tuple[str, Any]]:
    """The frozen protocol values as printed pairs. Every value is read from the active protocol JSON."""
    vals = _proto_values(proto)
    sep = vals.get("separation") or {}
    gate = vals.get("validity_gate") or {}

    def pct(x: Any) -> str:
        return f"{x * 100:g} percent"

    def has(*xs: Any) -> bool:
        return all(x is not None for x in xs)

    sha = proto.get("protocol_sha256")
    ver = str(proto.get("version") or "v1")
    dec_list = decider_ids if decider_ids is not None else vals.get("deciders")
    items = [
        ("Deciders", ", ".join(map(str, dec_list)) if dec_list else None),
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
        ("Protocol", f'{ver} {sha[:8]}' if sha else None),
    ]
    return [(k, v) for k, v in items if v is not None]


def _frozen_block(root: Path, *, use_v2: bool = False, decider_ids: list[str] | None = None) -> str:
    proto = _json(root / "protocol" / ("v2.json" if use_v2 else "v1.json")) or _json(root / "protocol" / "v1.json")
    if not proto:
        return ""
    ver = str(proto.get("version") or ("v2" if use_v2 else "v1"))
    rows = "".join(
        f"<div><dt>{k}</dt><dd>{E(str(v))}</dd></div>"
        for k, v in _frozen_items(proto, decider_ids=decider_ids)
    )
    return f'<h2>Frozen values</h2><dl class="params" data-protocol="{E(ver)}">{rows}</dl>'


def _method_submit(proto: dict, *, protocol: str = "v1", decider_ids: list[str] | None = None) -> str:
    rows = "".join(
        f"<div><dt>{k}</dt><dd>{E(str(v))}</dd></div>"
        for k, v in _frozen_items(proto, decider_ids=decider_ids)
        if k in ("Deciders", "Budget", "Repeats", "Bootstrap", "Separation", "Systems", "Protocol")
    )
    labels = ", ".join(k for k, _ in LABELS)
    synth_note = "All v2 chat cases are synthetic." if protocol == "v2" else "All v1 cases are synthetic."
    return ('<section class="sec" id="protocol" aria-labelledby="proto-h"><div class="sec-head">'
            '<h2 id="proto-h">Method and submissions</h2><span class="sub">Every number on this page traces to a '
            'verified summary file and the frozen protocol.</span></div><div class="two-col">'
            '<div><h3>Frozen before the table run</h3><p>Metric definitions, deciders, adapter settings, the '
            f'separation rule and the table seed were hashed before any table run. {synth_note}</p>'
            + (f'<dl class="dl-tight" data-protocol="{E(protocol)}">{rows}</dl>' if rows else "")
            + '<p class="mt-14"><a class="more" href="method.html">Read the method</a></p></div>'
            '<div><h3>Submit your memory</h3><p>Write the four-method blind adapter (reset, ingest, view, close), '
            'run the bench, verify the files and open a pull request. A vendor-tuned configuration is run by the maintainer and '
            'published as its own row, labelled exploratory.</p>' + "".join(_job(c) for c in SUBMIT_JOBS)
            + f'<p class="mono row-labels">Row labels: {E(labels)}.</p>'
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
    (concepts 1 and 2, 2026-11-19); missing files give a text-only header."""
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


HATCH_DEFS = (
    '<svg class="vh" aria-hidden="true" focusable="false"><defs>'
    '<pattern id="hercyna-hatch" width="4" height="4" patternTransform="rotate(135)" patternUnits="userSpaceOnUse">'
    '<rect width="4" height="4" fill="var(--slate)"/>'
    '<line x1="0" y1="0" x2="0" y2="4" stroke="var(--slate-hatch)" stroke-width="1.5"/>'
    '</pattern></defs></svg>'
)


def _font_link(pfx: str = "") -> str:
    return (
        f'<link rel="preload" href="{pfx}fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>'
        f'<link rel="preload" href="{pfx}fonts/ibm-plex-sans-regular-latin1.woff2" as="font" type="font/woff2" crossorigin>'
    )


def _updates_panel(newsletter_action: str | None) -> str:
    if newsletter_action:
        email_col = (
            '<div class="upd-col"><h3>Email updates</h3>'
            '<p>Double opt-in handled by the external list provider. No tracking cookies; zero emails stored on Hercyna.</p>'
            f'<form class="upd-form" action="{E(newsletter_action)}" method="post">'
            '<label class="vh" for="upd-email">Email address</label>'
            '<input id="upd-email" type="email" name="email" autocomplete="email" placeholder="you@example.org" required>'
            '<div class="hp-field" aria-hidden="true">'
            '<label for="upd-hp">Leave blank</label>'
            '<input id="upd-hp" class="hp-field" type="text" name="website" tabindex="-1" autocomplete="off">'
            '</div>'
            '<label class="consent-lbl">'
            '<input type="checkbox" name="consent" value="1" required>'
            '<span>Send me release updates (double opt-in). See <a href="privacy.html">Privacy</a>.</span>'
            '</label>'
            '<button class="btn solid" type="submit">Subscribe</button>'
            '<p class="upd-status" aria-live="polite"></p>'
            '</form></div>'
        )
    else:
        email_col = (
            '<div class="upd-col"><h3>Email updates</h3>'
            '<p><strong>Email coming soon.</strong> External double-opt-in signups open once the mailing endpoint is set; '
            'zero emails are stored on Hercyna (<a href="privacy.html">Privacy</a>).</p></div>'
        )
    feed_col = (
        '<div class="upd-col"><h3>Atom feed</h3>'
        '<p>Subscribe in any feed reader with zero accounts or scripts.</p>'
        '<p><a class="more" href="feed.xml">Subscribe via feed.xml</a> · '
        '<a href="changelog.html">Release notes</a></p></div>'
    )
    gh_col = (
        '<div class="upd-col"><h3>GitHub releases</h3>'
        '<p>Follow protocol updates, new table releases, and verified corrections on GitHub.</p>'
        '<p><a class="more" href="https://github.com/hercyna/hercyna/releases" rel="noopener">Watch releases</a></p></div>'
    )
    return (
        '<details class="updates-box" id="updates">'
        '<summary><span>Get updates: email, Atom feed, or GitHub releases</span>'
        '<span class="updates-sub">Cookie-free · no third-party scripts</span></summary>'
        f'<div class="updates-grid">{email_col}{feed_col}{gh_col}</div>'
        '</details>'
    )


def _page(
    title: str,
    current: str,
    main: str,
    proto: dict,
    built_at: str,
    *,
    proto_v2: dict | None = None,
    proto_tag: str | None = None,
    show_seed: bool | None = None,
    chip_note: str | None = None,
    asset_refs: dict[str, str] | None = None,
    site_url: str = "https://hercyna.org",
    canonical_path: str | None = None,
    og_image: str = "og/default.png",
    description: str | None = None,
    newsletter_action: str | None = None,
    analytics_src: str | None = None,
    root: Path | None = None,
) -> str:
    doc_title = f"{BRAND} – {title}"
    sha = proto.get("protocol_sha256")
    ver = str(proto.get("version") or "v1")
    sha2 = (proto_v2 or {}).get("protocol_sha256")
    active_v2 = (proto_tag == "v2") if proto_tag is not None else bool(proto_v2)
    page_proto = "v2" if active_v2 else ver
    top_sha = (sha2 if active_v2 else sha) or sha
    active_proto_dict = proto_v2 if (active_v2 and proto_v2) else proto
    seed = _proto_values(active_proto_dict).get("table_seed")
    if show_seed is None:
        show_seed = current in ("index.html", "v1.html", "chat.html")
    pre = "" if current == "index.html" else "index.html"
    links = ((f"{pre}#board", "Board", False),
             ("coding.html", "Coding", current == "coding.html"),
             ("chat.html", "Chat", current == "chat.html" or current.startswith("product-")),
             (f"{pre}#how", "How it works", False),
             (f"{pre}#anatomy", "Case anatomy", False),
             ("method.html", "Method", current == "method.html"),
             ("method.html#limits", "Limits", False),
             ("submit.html", "Submit", current == "submit.html"),
             ("corrections.html", "Corrections", current == "corrections.html"),
             ("why.html", "Why Hercyna", current == "why.html"),
             ("about.html", "About", current == "about.html"))
    # on the home page Board is marked from the top; the script then follows the section in view
    nav = "".join(f'<a href="{h}"' + (' aria-current="page"' if cur else "")
                  + (' class="on"' if current == "index.html" and h == "#board" else "") + f">{label}</a>"
                  for h, label, cur in links)
    mark, _ = _brand_assets()
    hash8 = f" · {E(top_sha[:8])}" if top_sha else ""
    synth_foot = "All v2 chat cases are synthetic." if active_v2 else "All v1 cases are synthetic."
    rail = (f'<aside class="rail"><a class="rail-brand" href="index.html">{mark}<span><b>{BRAND}</b>'
            f'<small>Protocol {E(page_proto)}{hash8}</small></span></a><nav class="rail-nav" aria-label="Site">{nav}</nav>'
            f'<div class="rail-foot"><span>{synth_foot}</span>'
            f'<span>v2.0.0-rc1 · built {E(built_at[:10])}</span></div></aside>')
    chip_label = chip_note or f"Protocol {page_proto}{hash8}"
    ver_switch = (
        '<span class="ver-switch" role="navigation" aria-label="Protocol version switcher">'
        f'<a href="index.html"{" aria-current=\"page\" class=\"active\"" if page_proto == "v2" else ""}>v2</a>'
        f'<a href="v1.html"{" aria-current=\"page\" class=\"active\"" if page_proto != "v2" else ""}>v1 archive</a>'
        '</span>'
    )
    ribbon = ('<header class="ribbon"><div class="ribbon-left">'
              f'<span class="chip"><i></i>{E(chip_label)}</span>'
              + ver_switch
              + '<span class="hash">frozen</span>'
              + (f'<span class="seed">table seed {E(str(seed))}</span>' if (show_seed and seed is not None) else "")
              + '</div><div class="ribbon-right">'
              + '<button class="btn js-only" type="button" data-open-updates>Get updates</button>'
              + _copy_btn(JOB_CMD, "Copy quickstart", "btn solid q-copy", gate_id="terms-hero" if current in ("index.html", "v1.html") else None)
              + f'<button class="btn js-only" type="button" id="theme" aria-pressed="false">{THEME_ICON}'
              '<span class="tl">Dark</span></button></div></header>')

    refs = asset_refs or {"head": "assets/head.js", "css": "assets/site.css", "app": "assets/app.js"}
    base = site_url.rstrip("/")
    canon_rel = canonical_path if canonical_path is not None else ("" if current == "index.html" else current)
    canon_url = f"{base}/{canon_rel.lstrip('/')}" if canon_rel else f"{base}/"
    desc = description or (
        "Hercyna scores agent memory products by the decisions an agent makes from what they return. "
        "Experimental research benchmark provided as is."
    )
    og_abs = f"{base}/{og_image.lstrip('/')}"
    analytics_tag = f'<script defer src="{E(analytics_src)}"></script>' if analytics_src else ""
    head_tags = (
        f'<link rel="canonical" href="{E(canon_url)}">'
        '<meta name="theme-color" content="#0b666d">'
        '<link rel="icon" href="favicon.ico" sizes="any">'
        '<link rel="icon" type="image/svg+xml" href="favicon.svg">'
        '<link rel="icon" type="image/png" sizes="32x32" href="favicon-32x32.png">'
        '<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">'
        '<link rel="alternate" type="application/atom+xml" href="feed.xml" title="Hercyna Releases">'
        '<meta property="og:type" content="website">'
        '<meta property="og:site_name" content="Hercyna">'
        f'<meta property="og:title" content="{E(doc_title)}">'
        f'<meta property="og:description" content="{E(desc)}">'
        f'<meta property="og:url" content="{E(canon_url)}">'
        f'<meta property="og:image" content="{E(og_abs)}">'
        '<meta property="og:image:width" content="1200">'
        '<meta property="og:image:height" content="630">'
        '<meta name="twitter:card" content="summary_large_image">'
        f'<meta name="twitter:title" content="{E(doc_title)}">'
        f'<meta name="twitter:description" content="{E(desc)}">'
        f'<meta name="twitter:image" content="{E(og_abs)}">'
        f'<script src="{E(refs["head"])}"></script>'
        f'{_font_link()}'
        f'<link rel="stylesheet" href="{E(refs["css"])}">'
    )
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{E(doc_title)}</title><meta name=\"color-scheme\" content=\"light dark\">"
            f'<meta name="description" content="{E(desc)}">'
            f"{head_tags}</head>"
            f'<body data-page-protocol="{E(page_proto)}">{HATCH_DEFS}'
            '<a class="skip" href="#main">Skip to content</a><div class="shell">' + rail
            + '<div class="main-col">' + ribbon + _disclaimer_bar(root)
            + f'<main id="main" class="wrap">{main}'
            + _updates_panel(newsletter_action)
            + _footer(proto, built_at, current, proto_v2=proto_v2, proto_tag=page_proto)
            + "</main></div></div>"
            f'<p class="vh" id="live" aria-live="polite"></p>'
            f'<script defer src="{E(refs["app"])}"></script>{analytics_tag}</body></html>\n')


def _compact_css(css: str) -> str:
    """Strip CSS block comments and blank-line indentation while preserving selectors verbatim."""
    import re as _re

    out = _re.sub(r"/\*.*?\*/", "", css, flags=_re.S)
    return "\n".join(line.strip() for line in out.splitlines() if line.strip())


def _compact_js(js: str) -> str:
    """Strip JS block comments and redundant indentation safely."""
    import re as _re

    out = _re.sub(r"/\*.*?\*/", "", js, flags=_re.S)
    return "\n".join(line.strip() for line in out.splitlines() if line.strip())


FOOT_LINKS = (("index.html#board", "Board", "index.html"), ("coding.html", "Coding", "coding.html"),
              ("chat.html", "Chat", "chat.html"), ("v1.html", "v1 archive", "v1.html"),
              ("method.html", "Method", "method.html"), ("method.html#limits", "Limits", ""),
              ("submit.html", "Submit", "submit.html"), ("corrections.html", "Corrections", "corrections.html"),
              ("changelog.html", "Changelog", "changelog.html"), ("terms.html", "Terms", "terms.html"),
              ("privacy.html", "Privacy", "privacy.html"), ("legal.html", "Legal", "legal.html"),
              ("why.html", "Why Hercyna", "why.html"), ("about.html", "About", "about.html"))


def _footer(
    proto: dict,
    built_at: str,
    current: str = "",
    *,
    proto_v2: dict | None = None,
    proto_tag: str | None = None,
) -> str:
    sha = proto.get("protocol_sha256")
    ver = str(proto.get("version") or "v1")
    sha2 = (proto_v2 or {}).get("protocol_sha256")
    active_v2 = (proto_tag == "v2") if proto_tag is not None else bool(proto_v2)
    if active_v2 and proto_v2:
        proto_str = (
            f'<a href="packs/v2/protocol-v2.json">protocol v2 {E(sha2[:8])}</a>'
            if sha2 else "protocol v2"
        )
        synth_note = "All v2 chat cases are synthetic."
    else:
        proto_str = (
            f'<a href="packs/v1/protocol-v1.json">protocol {E(ver)} {E(sha[:8])}</a>'
            if sha else f"protocol {E(ver)}"
        )
        synth_note = "All v1 cases are synthetic."
    return (f'<footer class="foot"><span>Hercyna v2.0.0-rc1 · {synth_note}</span>'
            f'<span>{proto_str} · <a href="legal.html">Apache-2.0 / CC BY 4.0</a>'
            f' · <a href="method.html#citation">Cite</a> · built {E(built_at[:10])} · '
            '<button class="btn-mini js-only" type="button" data-open-updates>Get updates</button></span>'
            '<nav aria-label="Footer">'
            + "".join(f'<a href="{h}"' + (' aria-current="page"' if page and page == current else "") + f">{label}</a>"
                      for h, label, page in FOOT_LINKS)
            + '</nav></footer>')


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
    return img + f'<h2>Elsewhere</h2><dl class="params elsewhere">{rows}</dl>'


def _reel_note() -> str:
    """The reel's caption comes with the media (site/media/reel-note.txt), so it ships only where the reel does."""
    p = MEDIA_DIR / "reel-note.txt"
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f'<p class="reel-note">{E(text)}</p>' if text else ""


FONT_DIR = BRAND_DIR / "fonts"


def _fonts(out: Path) -> dict[str, int]:
    """Copy the site fonts and their licence files into <out>/fonts, so a page needs no font service."""
    if not FONT_DIR.is_dir():
        return {}
    dest = out / "fonts"
    sizes = {}
    for p in sorted(FONT_DIR.iterdir()):
        if p.is_file() and not p.name.startswith(".") and p.suffix in (".woff2", ".txt"):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest / p.name)
            sizes[f"fonts/{p.name}"] = p.stat().st_size
    return sizes


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


def _v2_track_page(
    title: str,
    subtitle: str,
    track_id: str,
    cards: list[tuple[str, str, list[str]]],
    instruments: list[tuple[str, str]],
) -> str:
    """Preview scaffold for Phase 2 tracks before frozen v2 table runs exist (zero fabricated numbers)."""
    card_html = "".join(
        '<article class="v2-card">'
        f'<h3>{E(head)}</h3><p>{E(desc)}</p>'
        + ("<ul>" + "".join(f"<li>{E(item)}</li>" for item in items) + "</ul>" if items else "")
        + "</article>"
        for head, desc, items in cards
    )
    inst_html = "".join(
        '<div class="v2-card">'
        f'<h3>{E(head)}</h3><p>{E(desc)}</p>'
        '<p class="mono row-labels"><span class="lab x">exploratory</span> Coding runs held; awaiting cap and '
        f'verified v2 table run files under <code>results/table/v2/{E(track_id)}/</code>.</p></div>'
        for head, desc in instruments
    )
    return (
        '<div class="doc">'
        f'<h1>{E(title)}</h1>'
        '<div class="pending" role="note"><strong>exploratory preview</strong>'
        '<span>Protocol v2 track scaffold. Coding Agent Mode A and Mode B runs are held pending cap approval; '
        'zero coding run numbers are published. Frozen v1 account-review tables live in '
        '<a href="v1.html">v1 archive</a>.</span></div>'
        '<section class="sec" aria-labelledby="v2-modes-h"><div class="sec-head">'
        f'<h2 id="v2-modes-h">Evaluation design</h2><span class="sub">{E(subtitle)}</span></div>'
        f'<div class="v2-grid">{card_html}</div></section>'
        '<section class="sec" aria-labelledby="v2-inst-h"><div class="sec-head">'
        '<h2 id="v2-inst-h">Registered instruments</h2>'
        '<span class="sub">Fixed row order (products A to Z, then baselines). No single-score order without separation.</span></div>'
        f'<div class="v2-grid">{inst_html}</div></section>'
        '<p class="mt-14"><a class="more" href="index.html#board">Return to the v2 Board</a></p>'
        '</div>'
    )


def _coding_page() -> str:
    return _v2_track_page(
        "Coding Agent Track",
        "Blind-Adapter Mode and End-to-End Harness Mode stay strictly partitioned and never pool.",
        "coding_agent",
        [
            (
                "Blind-Adapter Mode (Mode A)",
                "Zero tool calls allowed. Measures pure memory retrieval and decision quality against the full-history floor.",
                [
                    "Budgets: 1,000 and 4,000 view tokens",
                    "Baselines: full floor, bm25, dense_raw, hybrid_bm25_dense, recent, empty",
                    "Validity gate required for every decider and shared memory-side extractor",
                ],
            ),
            (
                "End-to-End Harness Mode (Mode A & Mode B)",
                "Bounded repository tools referenced strictly to the end-to-end full-history floor, with Mode B offline verification.",
                [
                    "Execution isolated behind metered proxy; verification runs offline with read-only workspace",
                    "Causal failure taxonomy records which memory invariant broke without exposing text",
                    "Memory-ROI and Tool-Call Tax compare memory layers against empty plus tools",
                ],
            ),
        ],
        [
            (
                "Memory-ROI and Tool-Call Tax",
                "Paired bootstrap differences in tool calls, wall time, API dollars, and local Apple Silicon GPU-seconds.",
            ),
            (
                "Open-Weight and Local Matrix",
                "Separate filters for gate-passing cloud harnesses, local Mac Studio MLX/Ollama runtimes, and quarantined exploratory probes.",
            ),
        ],
    )


def _chat_dev_anatomy(root: Path, *, section_id: str = "chat-anatomy", heading_id: str = "chat-ana-h") -> str:
    """Render one synthetic H1 development case from data/v2/chat_agent/dev_cases.jsonl (never table cases)."""
    dev_path = root / "data" / "v2" / "chat_agent" / "dev_cases.jsonl"
    if not dev_path.is_file():
        return (
            '<div class="pending" role="note"><strong>dev split not built</strong>'
            '<span>Generate synthetic development cases at <code>data/v2/chat_agent/dev_cases.jsonl</code>.</span></div>'
        )
    try:
        from ..cases.schema import read_jsonl
        from ..tracks.chat_agent import ChatAgentTrack

        cases = list(read_jsonl(dev_path))
    except Exception:
        return ""
    case = next((c for c in cases if c.mechanism == "H1" and len(c.d_star) == 2), cases[0] if cases else None)
    if case is None:
        return ""
    track = ChatAgentTrack()
    all_ids = {e.id for e in case.events}
    without_override = track.oracle(case, visible_ids=all_ids - {case.d_star[-1]})
    ev_by_id = {e.id: e for e in case.events}
    t_old = ev_by_id.get(case.d_star[0])
    t_new = ev_by_id.get(case.d_star[-1])
    meta = case.metadata or {}
    rec_html = ""
    if t_old is not None:
        rec_html += (
            f'<div class="rec key"><b>{E(t_old.id)}</b> {E(t_old.date)} · preference set<br>'
            f'{E(getattr(t_old, "text", ""))}'
            '<span class="why-k">Earlier standing preference on this workflow</span></div>'
        )
    if t_new is not None:
        rec_html += (
            f'<div class="rec key"><b>{E(t_new.id)}</b> {E(t_new.date)} · preference override<br>'
            f'{E(getattr(t_new, "text", ""))}'
            '<span class="why-k">Supersedes the earlier turn (lag '
            f'{int(meta.get("override_lag_events", 0))} turns, {int(meta.get("override_lag_days", 0))} days)</span></div>'
            f'<div class="rec drop"><span class="drop-t"><b>{E(t_new.id)}</b> omitted under a tight view budget</span>'
            '<span class="why-k">Only the stale preference survives into the view</span></div>'
        )
    return (
        f'<section class="sec" id="{E(section_id)}" aria-labelledby="{E(heading_id)}" data-protocol="v2"><div class="sec-head">'
        f'<h2 id="{E(heading_id)}">Case anatomy</h2>'
        f'<span class="sub">Synthetic development case {E(case.case_id)} ({E(case.mechanism)}, '
        f'{len(case.events)} turns, {E(str(meta.get("distance", "")))} override distance). Never a table case.</span></div>'
        '<div class="ana">'
        '<div class="ana-col"><h3>The user request</h3>'
        f'<p class="q">{E(case.query_text)}</p>'
        f'<p>Workflow slot: <code>{E(str(meta.get("slot", "")))}</code>. '
        f'Multi-session history spans {len(case.events)} turns across simulated weeks.</p>'
        '<p class="ana-foot">Mechanisms H0 through H3 and HU1 through HU2 test routine execution, '
        'preference override, expired permission, standing guard, missing parameter, and conflicting rules.</p></div>'
        f'<div class="ana-col"><h3>Decisive turns in the history</h3>{rec_html}</div>'
        '<div class="ana-col"><h3>What changes the action</h3>'
        '<p>With the full history (both decisive turns in view), the right action is:</p>'
        f'<div class="act right">{E(case.a_star)}</div>'
        '<p>When a memory view keeps the old turn but drops the override, the agent falls back to:</p>'
        f'<div class="act wrong">{E(without_override)}</div>'
        '<p class="ana-foot">On conflict cases (HU2), retrieving one of two opposing standing rules hides the conflict '
        'and induces wrong action; retrieving both or neither points to REQUEST_INFO.</p></div>'
        '</div></section>'
    )


def _v2_ci_html(stat: dict | None, cls: str = "", *, href: str | None = None) -> str:
    """Format one rate and its 95% interval uniformly as XX.X% [XX.X, XX.X] (1 decimal everywhere)."""
    r = _rate(stat)
    extra = f" {cls}" if cls else ""
    if not r:
        return f'<div class="ci-cell{extra}" data-protocol="v2"><b>n/a</b></div>'
    iv = f' <small>{E(r["iv"])}</small>' if r["iv"] else ""
    inner = f'<b>{E(r["t"])}</b>{iv}'
    if href:
        inner = f'<a class="pack-link" href="{E(href)}">{inner}</a>'
    return f'<div class="ci-cell{extra}" data-protocol="v2">{inner}</div>'


def _v2_share_bar(sys_label: str, right: dict | None, held: dict | None, wrong: dict | None) -> str:
    parts = (("right", "r", _rate(right)), ("withheld", "h", _rate(held)), ("wrong", "w", _rate(wrong)))
    x = 0.0
    rects: list[str] = []
    for _, c, p in parts:
        if not p:
            continue
        w = float(p["w"])
        if w <= 0:
            continue
        if x > 0:
            rects.append(f'<line class="bar-sep" x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="11"/>')
        rects.append(f'<rect class="p-{c}" x="{x:.2f}" y="0" width="{w:.2f}" height="11"/>')
        x += w
    nums = "".join(f'<span class="{c}">{E(p["t"]) if p else "n/a"}</span>' for _, c, p in parts)
    aria = ", ".join(f'{name} {p["t"] if p else "n/a"}' for name, _, p in parts)
    return (
        f'<svg class="bar-svg" viewBox="0 0 100 11" preserveAspectRatio="none" role="img" '
        f'aria-label="Share of decisions for {E(sys_label)}: {E(aria)}">{"".join(rects)}</svg>'
        f'<div class="bar-nums">{nums}</div>'
    )


def _v2_floor_thead(did: str, floor: dict | None, n_cases: Any, n_gated: Any, colspan: int) -> str:
    fl = floor or {}
    cnt = f"{n_gated} gated of {n_cases} cases" if n_gated is not None and n_cases is not None else "full history"
    return (
        f'<tr class="tbl-floor-row"><th scope="colgroup" colspan="{colspan}">'
        '<div class="floor-head-flex">'
        f'<span><b>Floor · {E(did)}</b> (full history, {E(cnt)}) <span class="lab x">exploratory</span></span>'
        '<span class="floor-metrics">'
        f'<span>wrong action <b>{E(_pct_t(fl.get("wrong_commit")))}</b></span>'
        f'<span>· withheld action <b>{E(_pct_t(fl.get("abstention")))}</b></span>'
        f'<span>· right action <b>{E(_pct_t(fl.get("right_action")))}</b></span>'
        '</span></div></th></tr>'
    )


def _load_v2_chat_table(root: Path) -> dict | None:
    """Load verified v2 chat_agent table runs when present (released results/table/v2 or local results/v2/table)."""
    from ..engine import files as F
    from ..engine.pool import PoolError, verdict as pool_verdict
    from ..scoring.chow import DISPLAY_CHOW_LAMBDAS, chow_summary

    candidates = (
        root / "results" / "table" / "v2" / "chat_agent",
        root / "results" / "v2" / "table" / "chat_agent",
    )
    troot = next((c for c in candidates if c.is_dir()), None)
    if troot is None:
        return None

    run_dirs: list[Path] = []
    by_decider: dict[str, dict[str, Any]] = {}
    boot_seed, boot_reps = 11, 1000
    proto_shas: set[str] = set()

    for dec_dir in sorted(p for p in troot.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for sys_dir in sorted(p for p in dec_dir.iterdir() if p.is_dir() and not p.name.startswith("_")):
            man = _json(sys_dir / "manifest.json")
            summ = _json(sys_dir / "summary.json")
            if not man or not summ or man.get("state") != "complete":
                continue
            run_dirs.append(sys_dir)
            boot = man.get("bootstrap") or {}
            boot_seed = int(boot.get("seed", boot_seed))
            boot_reps = int(boot.get("reps", boot_reps))
            psha = (man.get("protocol") or {}).get("protocol_sha256")
            if isinstance(psha, str) and psha:
                proto_shas.add(psha)

            inc = man.get("incomplete_cells") or []
            missing_by_cname: dict[str, set[str]] = {}
            union_missing: set[str] = set()
            for x in inc:
                if isinstance(x, dict):
                    mc = {str(cid) for cid in (x.get("missing_cases") or [])}
                    union_missing.update(mc)
                    if x.get("cell"):
                        missing_by_cname[str(x["cell"])] = mc
            repeats = max(1, int(man.get("repeats") or 1))
            acbi_file = _json(sys_dir / "acbi.json") or {}
            mp = sys_dir / F.METRICS
            mrows = list(F.read_metrics(mp)) if mp.is_file() else []

            for did, dd in sorted((summ.get("deciders") or {}).items()):
                model_name = next((m.get("model") for m in (man.get("deciders") or []) if m.get("id") == did), None)
                slot = by_decider.setdefault(
                    did,
                    {
                        "id": did,
                        "model": model_name,
                        "floor": dd.get("floor"),
                        "n_cases": dd.get("n_cases"),
                        "n_gated": dd.get("n_gated"),
                        "rows_map": {},
                        "acbi_cells": {},
                        "run_dirs": [],
                        "metrics_rows": [],
                    },
                )
                if slot["floor"] is None and dd.get("floor"):
                    slot["floor"] = dd.get("floor")
                slot["run_dirs"].append(sys_dir)
                dec_mrows = [r for r in mrows if r.get("decider") == did]
                slot["metrics_rows"].extend(dec_mrows)
                gate_all_ids = {str(r["case_id"]) for r in dec_mrows if r.get("kind") == "gate"}
                gate_fail_ids = {
                    str(r["case_id"])
                    for r in dec_mrows
                    if r.get("kind") == "gate" and r.get("action") != r.get("a_star")
                }

                for cname, cell in sorted((dd.get("cells") or {}).items()):
                    key = (cell.get("system"), cell.get("config_label"), cell.get("budget"))
                    if key in slot["rows_map"]:
                        continue
                    g_sum = float(((cell.get("right_action") or {}).get("sums") or {}).get("g") or 0.0)
                    gated_present = int(round(g_sum / repeats)) if g_sum else 0
                    if gate_all_ids:
                        view_ids = {
                            str(r["case_id"])
                            for r in dec_mrows
                            if r.get("kind") == "view"
                            and (r.get("system"), r.get("config_label"), r.get("budget")) == key
                        }
                        missing_ids = gate_all_ids - view_ids
                        n_cases_cell = len(gate_all_ids)
                        val_fails = len(gate_fail_ids)
                        ing_errs = len(missing_ids)
                        in_both = len(gate_fail_ids & missing_ids)
                        gated_total = len((gate_all_ids - gate_fail_ids) - missing_ids)
                    else:
                        n_cases_cell = int(dd.get("n_cases") or 0)
                        n_gated_floor = int(dd.get("n_gated") or 0)
                        val_fails = max(0, n_cases_cell - n_gated_floor)
                        ing_errs = len(missing_by_cname.get(cname, union_missing))
                        gated_total = gated_present
                        in_both = max(0, val_fails + ing_errs - (n_cases_cell - gated_total))

                    slot["rows_map"][key] = {
                        "key": key,
                        "cname": cname,
                        "system": str(key[0]),
                        "config_label": str(key[1]),
                        "budget": key[2],
                        "config": _config_line(str(key[0]), key[1], key[2]),
                        "base": str(key[0]) in BASELINES,
                        "cell": cell,
                        "n_cases": n_cases_cell,
                        "validity_fails": val_fails,
                        "ingest_errors": ing_errs,
                        "in_both": in_both,
                        "gated_cases": gated_total,
                        "over_excused_limit": _over_excused_limit(ing_errs, n_cases_cell),
                        "run_dir": sys_dir,
                        "pack_href": f"packs/v2/chat_agent/{did}/{sys_dir.name}/summary.json",
                    }
                acbi_dec = ((acbi_file.get("deciders") or {}).get(did) or {}).get("cells") or {}
                slot["acbi_cells"].update(acbi_dec)

    if not by_decider or not run_dirs:
        return None

    sep: dict[str, Any] | None = None
    cached_verdict = _json(troot / "verdict_r6_full.json")
    if (
        isinstance(cached_verdict, dict)
        and isinstance(cached_verdict.get("pool"), dict)
        and sorted(cached_verdict["pool"].get("deciders") or []) == sorted(by_decider)
        and len(proto_shas) == 1
        and cached_verdict["pool"].get("protocol_sha256") in proto_shas
    ):
        sep = cached_verdict.get("separation")
    if sep is None:
        try:
            sep = pool_verdict(run_dirs, seed=boot_seed, reps=boot_reps, require_table=False).get("separation")
        except PoolError:
            sep = None

    disqualified = {
        r["cname"]
        for slot in by_decider.values()
        for r in slot["rows_map"].values()
        if r.get("over_excused_limit")
    }
    if disqualified and isinstance(sep, dict) and isinstance(sep.get("separating_pairs"), list):
        kept_pairs = []
        for p in sep["separating_pairs"]:
            a_n, _, b_n = str(p.get("pair") or "").partition(" | ")
            if a_n not in disqualified and b_n not in disqualified:
                kept_pairs.append(p)
        sep = {**sep, "separating_pairs": kept_pairs}
        if not kept_pairs and sep.get("leaderboard"):
            sep["leaderboard"] = False

    # Compute Chow summary per decider over pooled rows (deduplicated by row key)
    from ..scoring.metrics import _row_key

    chow_by_decider: dict[str, Any] = {}
    for did, slot in sorted(by_decider.items()):
        seen_keys: dict[tuple, dict] = {}
        for row in slot["metrics_rows"]:
            seen_keys.setdefault(_row_key(row), row)
        if seen_keys:
            csum = chow_summary(
                seen_keys.values(),
                lambdas=DISPLAY_CHOW_LAMBDAS,
                seed=boot_seed,
                reps=min(boot_reps, 250),
                exploratory=True,
            )
            chow_by_decider[did] = (csum.get("deciders") or {}).get(did)

    # Order rows for each decider (products A-Z, then baselines)
    for slot in by_decider.values():
        slot["rows"] = _order(list(slot["rows_map"].values()))

    return {
        "deciders": by_decider,
        "separation": sep,
        "chow": chow_by_decider,
        "run_dirs": run_dirs,
        "n_runs": len(run_dirs),
    }


def _v2_gated_split_line(row: dict) -> str:
    n_cases = int(row.get("n_cases") or 300)
    vf = int(row.get("validity_fails") or 0)
    ie = int(row.get("ingest_errors") or 0)
    both = int(row.get("in_both") or 0)
    gated = int(row.get("gated_cases") or 0)
    vf_lbl = "validity fail" if vf == 1 else "validity fails"
    ie_lbl = "ingest error" if ie == 1 else "ingest errors"
    both_lbl = "case in both" if both == 1 else "cases in both"
    return (
        f'{n_cases} cases: <b>{vf}</b> {vf_lbl} + <b>{ie}</b> {ie_lbl} '
        f'&minus; <b>{both}</b> {both_lbl} &rarr; <b>{gated}</b> gated'
    )


def _v2_full_row_tr(
    row: dict,
    *,
    rank_badge: str,
    extra_meta: str = "",
    show_split: bool = False,
    row_idx: int = 0,
    link_product: bool = True,
    sys_cell_text: str | None = None,
) -> str:
    disp = sys_cell_text or DISPLAY.get(row["system"], row["system"])
    prod_href = f'product-{_slug(row["system"])}.html'
    sys_html = (
        f'<a class="sys-link" href="{E(prod_href)}"><span class="sys">{E(disp)}</span></a>'
        if link_product
        else f'<span class="sys">{E(disp)}</span>'
    )
    n_cases = row.get("n_cases") or 300
    ing = int(row.get("ingest_errors") or 0)
    gated = row.get("gated_cases")
    if show_split:
        ing_sub = f"of {n_cases} cases"
    else:
        ing_sub = f"of {n_cases} cases" + (f" ({gated} gated)" if gated is not None else "")
    cell = row["cell"]
    href = row.get("pack_href")
    health_href = href[:-12] + "health.json" if href and href.endswith("summary.json") else href
    share = _v2_share_bar(disp, cell.get("right_action"), cell.get("abstention"), cell.get("wrong_commit"))
    meta_html = (
        f'<span>{E(row["config"])}</span><span class="lab x">exploratory</span>'
        + (f"<span>{E(extra_meta)}</span>" if extra_meta else "")
    )
    base_flag = 1 if row["base"] else 0

    def _sval(k: str) -> str:
        v = (cell.get(k) or {}).get("value")
        return f"{float(v):.6f}" if isinstance(v, (int, float)) else ""

    row_id = f"r-{row_idx}"
    sort_attrs = (
        f' data-row-id="{row_id}" data-orig="{row_idx}" '
        f'data-wrong="{_sval("wrong_commit")}" data-withheld="{_sval("abstention")}" '
        f'data-right="{_sval("right_action")}"'
    )
    tr_cls = ' class="has-split"' if show_split else ""
    ing_inner = f'<b>{ing}</b> ' + (
        f'<a class="pack-link" href="{E(health_href)}"><small>{E(ing_sub)}</small></a>'
        if health_href else f'<small>{E(ing_sub)}</small>'
    )
    main_tr = (
        f'<tr{tr_cls} data-base="{base_flag}"{sort_attrs}>'
        '<th scope="row">'
        f'<div class="sys-t">{rank_badge}{sys_html}</div>'
        f'<div class="sys-m">{meta_html}</div></th>'
        f'<td class="c-share">{share}</td>'
        f'<td class="r" data-k="Wrong action">{_v2_ci_html(cell.get("wrong_commit"), "wrong", href=href)}</td>'
        f'<td class="r" data-k="Withheld action">{_v2_ci_html(cell.get("abstention"), href=href)}</td>'
        f'<td class="r" data-k="Right action">{_v2_ci_html(cell.get("right_action"), "right", href=href)}</td>'
        f'<td class="r" data-k="Ingest-error cases left out">'
        f'<div class="ci-cell" data-protocol="v2">{ing_inner}</div></td>'
        '</tr>'
    )
    if not show_split:
        return main_tr
    split_tr = (
        f'<tr class="v2-row-split" data-base="{base_flag}" data-parent-row="{row_id}">'
        f'<td colspan="6" class="gate-split">{_v2_gated_split_line(row)}</td>'
        '</tr>'
    )
    return main_tr + split_tr


def _v2_tabs_bar(
    group_id: str,
    deciders: dict[str, dict],
    *,
    with_baselines: bool = False,
    sortable: bool = False,
) -> str:
    sorted_decs = sorted(deciders.items())
    tabs = "".join(
        f'<button class="v2-tab" type="button" role="tab" id="{group_id}-tab-{_slug(did)}" '
        f'aria-controls="{group_id}-panel-{_slug(did)}" '
        f'aria-selected="{"true" if idx == 0 else "false"}">{E(did)}'
        f'<span>{E(slot["model"] or "")}{" · " if slot.get("model") else ""}{len(slot["rows"])} rows</span></button>'
        for idx, (did, slot) in enumerate(sorted_decs)
    )
    toggle = (
        f'<label class="tab-tools"><input type="checkbox" data-base-toggle checked>Baselines</label>'
        if with_baselines else ""
    )
    sticky = ""
    if sortable:
        opts = "".join(f'<option value="{E(did)}">{E(did)}</option>' for did, _ in sorted_decs)
        sticky = (
            '<div class="v2-sticky-bar js-only">'
            f'<label>Decider <select class="decider-select" aria-label="Filter by decider">{opts}</select></label>'
            '<div class="mob-sort"><span>Sort:</span>'
            '<button type="button" class="btn-mini" data-sort-col="wrong" data-default-dir="asc" aria-pressed="false">Wrong</button>'
            '<button type="button" class="btn-mini" data-sort-col="withheld" data-default-dir="asc" aria-pressed="false">Withheld</button>'
            '<button type="button" class="btn-mini" data-sort-col="right" data-default-dir="desc" aria-pressed="false">Right</button>'
            '</div></div>'
        )
    return (
        sticky
        + f'<div class="v2-tab-rail js-only"><div class="tablist" role="tablist" aria-label="Decider">{tabs}</div>'
        f'{toggle}</div>'
    )


def _v2_ranked_pairs_section(
    loaded: dict,
    *,
    section_id: str = "chat-ranked-pairs",
    heading_id: str = "chat-rank-h",
    more_link: bool = False,
) -> str:
    sep = loaded.get("separation") or {}
    pairs = sep.get("separating_pairs") or []
    if not pairs:
        return ""
    deciders = loaded["deciders"]
    panels: list[str] = []

    for idx, (did, slot) in enumerate(sorted(deciders.items())):
        by_cname = {r["cname"]: r for r in slot["rows"]}
        dec_pairs = ((sep.get("deciders") or {}).get(did) or {}).get("pairs") or {}
        trs: list[str] = []
        ridx = 0
        for p in pairs:
            pair_key = str(p.get("pair") or "")
            direction = str(p.get("direction") or "")
            a_name, _, b_name = pair_key.partition(" | ")
            lo_name, hi_name = (a_name, b_name) if direction == "a_lower" else (b_name, a_name)
            p_stat = dec_pairs.get(pair_key) or {}
            hp = p_stat.get("holm_p")
            hp_note = f"pair Holm p {hp:.3f}" if isinstance(hp, (int, float)) else ""
            lo_row, hi_row = by_cname.get(lo_name), by_cname.get(hi_name)
            if lo_row is not None:
                trs.append(
                    _v2_full_row_tr(
                        lo_row,
                        rank_badge='<span class="rank-tag">Lower wrong action</span>',
                        extra_meta=hp_note,
                        show_split=True,
                        row_idx=ridx,
                    )
                )
                ridx += 1
            if hi_row is not None:
                trs.append(
                    _v2_full_row_tr(
                        hi_row,
                        rank_badge='<span class="rank-tag r2">Higher wrong action</span>',
                        extra_meta=hp_note,
                        show_split=True,
                        row_idx=ridx,
                    )
                )
                ridx += 1
        sid = _slug(did)
        panels.append(
            f'<div class="v2-dpanel" id="pairs-panel-{sid}" role="tabpanel" aria-labelledby="pairs-tab-{sid}">'
            f'<h3 class="v2-dhead nojs-only">{E(did)} <span class="lab x">exploratory</span></h3>'
            '<div class="v2-full-wrap"><table class="v2-full-tbl" data-protocol="v2">'
            f'<thead>{_v2_floor_thead(did, slot["floor"], slot["n_cases"], slot["n_gated"], 6)}'
            '<tr class="tbl-cols">'
            '<th scope="col">Pair order &amp; system</th>'
            '<th scope="col">Right · withheld · wrong share</th>'
            '<th scope="col" class="r">Wrong action</th>'
            '<th scope="col" class="r">Withheld action</th>'
            '<th scope="col" class="r">Right action</th>'
            '<th scope="col" class="r">Ingest-error cases left out</th>'
            f'</tr></thead><tbody>{"".join(trs)}</tbody></table></div></div>'
        )

    pair_desc = "; ".join(_pair_words(str(p["pair"]), str(p["direction"])) for p in pairs)
    more_html = (
        '<p class="mt-14"><a class="more" href="chat.html#chat-board">Open the full 40-cell chat board</a></p>'
        if more_link
        else ""
    )
    return (
        f'<section class="sec" id="{E(section_id)}" aria-labelledby="{E(heading_id)}" data-protocol="v2">'
        f'<div class="sec-head"><h2 id="{E(heading_id)}">Separating product pairs\u00a0\u2013 lower wrong action with full row</h2>'
        '<span class="sub"><span class="lab x">exploratory</span> Lower wrong action and higher wrong action are shown '
        'alongside wrong action, withheld action, right action, and ingest-error cases left out\u00a0\u2013 never alone, and '
        'with no rank number.</span></div>'
        '<article class="v2-card v2-card-wide" data-tab-group="pairs">'
        '<div class="v2-head-bar"><h3>Frozen separation verdict: leaderboard '
        '<span class="lab x">exploratory</span></h3></div>'
        f'<p>{E(pair_desc)} across all {len(deciders)} gate-passing deciders (Holm-adjusted &alpha; = 0.05). '
        'Because lowering wrong action can come from withholding action, every separating pair displays the full '
        'decision row, the gated-case split, and no rank number.</p>'
        f'{_v2_tabs_bar("pairs", deciders)}'
        f'{"".join(panels)}'
        f'</article>{more_html}</section>'
    )


def _v2_chat_board_section(loaded: dict) -> str:
    sep = loaded.get("separation") or {}
    pairs = [(str(p["pair"]), str(p["direction"])) for p in (sep.get("separating_pairs") or [])]
    lo_members: set[str] = set()
    hi_members: set[str] = set()
    for pair, direction in pairs:
        a_name, _, b_name = pair.partition(" | ")
        lo_n, hi_n = (a_name, b_name) if direction == "a_lower" else (b_name, a_name)
        lo_members.add(lo_n)
        hi_members.add(hi_n)

    deciders = loaded["deciders"]
    panels: list[str] = []
    seen_sys: list[str] = []
    for did, slot in sorted(deciders.items()):
        trs: list[str] = []
        for ridx, row in enumerate(slot["rows"]):
            if row["system"] not in seen_sys:
                seen_sys.append(row["system"])
            if row["base"]:
                badge = '<span class="rank-tag unranked">baseline</span>'
                show_split = False
            elif row.get("over_excused_limit"):
                badge = '<span class="rank-tag unranked">unranked (&gt;5% excused)</span>'
                show_split = False
            elif row["cname"] in lo_members and row["cname"] not in hi_members:
                badge = '<span class="rank-tag">Lower wrong action</span>'
                show_split = True
            elif row["cname"] in hi_members:
                badge = '<span class="rank-tag r2">Higher wrong action</span>'
                show_split = True
            else:
                badge = '<span class="rank-tag unranked">not separated</span>'
                show_split = False
            trs.append(_v2_full_row_tr(row, rank_badge=badge, show_split=show_split, row_idx=ridx))

        sid = _slug(did)
        panels.append(
            f'<div class="v2-dpanel" id="cboard-panel-{sid}" role="tabpanel" aria-labelledby="cboard-tab-{sid}">'
            f'<h3 class="v2-dhead nojs-only">{E(did)} <span class="lab x">exploratory</span></h3>'
            '<div class="v2-full-wrap"><table class="v2-full-tbl" data-sortable="1" data-protocol="v2">'
            f'<thead>{_v2_floor_thead(did, slot["floor"], slot["n_cases"], slot["n_gated"], 6)}'
            '<tr class="tbl-cols">'
            '<th scope="col">System</th>'
            '<th scope="col">Right · withheld · wrong share</th>'
            '<th scope="col" class="r" aria-sort="none"><button type="button" class="sort-btn" data-sort-col="wrong" data-default-dir="asc">Wrong action <span class="sort-ind" aria-hidden="true">↕</span></button></th>'
            '<th scope="col" class="r" aria-sort="none"><button type="button" class="sort-btn" data-sort-col="withheld" data-default-dir="asc">Withheld action <span class="sort-ind" aria-hidden="true">↕</span></button></th>'
            '<th scope="col" class="r" aria-sort="none"><button type="button" class="sort-btn" data-sort-col="right" data-default-dir="desc">Right action <span class="sort-ind" aria-hidden="true">↕</span></button></th>'
            '<th scope="col" class="r">Ingest-error cases left out</th>'
            f'</tr></thead><tbody>{"".join(trs)}</tbody></table></div></div>'
        )

    pills = "".join(
        f'<a class="prod-pill" href="product-{_slug(s)}.html">{E(DISPLAY.get(s, s))}</a>'
        for s in seen_sys
    )
    prod_strip = (
        f'<div class="prod-strip" role="navigation" aria-label="Product detail pages">'
        f'<span>System pages:</span>{pills}</div>'
        if pills else ""
    )

    return (
        '<section class="sec" id="chat-board" aria-labelledby="chat-board-h" data-protocol="v2">'
        '<div class="sec-head"><h2 id="chat-board-h">Chat Agent 40-cell table (budget 1,000)</h2>'
        '<span class="sub"><span class="lab x">exploratory</span> Fixed initial order (products A to Z, then baselines). '
        'Click any rate header to sort; click any number to inspect its pack JSON.</span></div>'
        + prod_strip
        + '<article class="v2-card v2-card-wide" data-tab-group="cboard">'
        f'{_v2_tabs_bar("cboard", deciders, with_baselines=True, sortable=True)}'
        f'{"".join(panels)}'
        '<p class="v2-meta"><span class="lab x">exploratory</span> '
        '300 synthetic multi-session cases · 1,000-token view budget · 3 repeats · shared stored product views.</p>'
        '</article></section>'
    )


def _chat_dryrun_instruments(root: Path, loaded: dict | None = None) -> str:
    """Render exploratory instruments: populated from verified v2 runs when present, else empty frames."""
    proto_v2 = _json(root / "protocol" / "v2.json")
    disp_lams = ("0.5", "1.0", "2.0", "3.0", "5.0", "10.0", "20.0")
    lam_headers = "".join(
        f'<th scope="col" class="r">&lambda;={E(l.rstrip("0").rstrip("."))}</th>'
        for l in disp_lams
    )

    if loaded and loaded.get("chow"):
        deciders = loaded["deciders"]
        chow_panels: list[str] = []
        for did, slot in sorted(deciders.items()):
            cdec = (loaded["chow"] or {}).get(did) or {}
            ccells = cdec.get("cells") or {}
            crows: list[str] = []
            for row in slot["rows"]:
                c_entry = ccells.get(row["cname"]) or {}
                by_l = c_entry.get("by_lambda") or {}
                tds = "".join(
                    f'<td class="r" data-k="\u03bb={E(l.rstrip("0").rstrip("."))}">'
                    f'{_v2_ci_html((by_l.get(str(float(l))) or {}).get("risk"))}</td>'
                    for l in disp_lams
                )
                disp = DISPLAY.get(row["system"], row["system"])
                crows.append(
                    f'<tr data-base="{1 if row["base"] else 0}">'
                    f'<th scope="row">{E(disp)}</th>'
                    f'{tds}</tr>'
                )
            sid = _slug(did)
            chow_panels.append(
                f'<div class="v2-dpanel" id="chow-panel-{sid}" role="tabpanel" aria-labelledby="chow-tab-{sid}">'
                f'<h4 class="v2-dhead nojs-only">{E(did)} <span class="lab x">exploratory</span></h4>'
                '<div class="v2-tbl-wrap v2-no-scroll"><table class="v2-tbl v2-stack-mobile" data-protocol="v2">'
                f'<thead>{_v2_floor_thead(did, slot["floor"], slot["n_cases"], slot["n_gated"], len(disp_lams) + 1)}'
                f'<tr class="tbl-cols"><th scope="col">Cell</th>{lam_headers}</tr></thead>'
                f'<tbody>{"".join(crows)}</tbody></table></div></div>'
            )
        chow_card = (
            '<article class="v2-card v2-card-wide" data-tab-group="chow" data-protocol="v2">'
            '<div class="v2-head-bar"><h3>Decision cost by risk ratio (&lambda;) '
            '<span class="lab x">exploratory</span></h3></div>'
            '<p>Fixed-order Chow risk L(&lambda;) = &lambda; &middot; wrong_action + withheld_action across the '
            'registered display grid, expressed in percentage points with 95% paired-bootstrap intervals.</p>'
            f'{_v2_tabs_bar("chow", deciders, with_baselines=True)}'
            f'{"".join(chow_panels)}'
            '<p class="v2-meta"><span class="lab x">exploratory</span> '
            'Fixed row order; lower risk indicates fewer combined weighted wrong actions and withheld actions.</p></article>'
        )
    else:
        chow_card = (
            '<article class="v2-card v2-card-wide" data-protocol="v2">'
            '<div class="v2-head-bar"><h3>Decision cost by risk ratio (&lambda;) '
            '<span class="lab x">exploratory</span></h3></div>'
            '<p>Fixed-order Chow risk L(&lambda;) = &lambda; &middot; wrong_action + withheld_action across the '
            'registered display grid. No mock numbers are displayed; real exploratory numbers appear only after '
            'verified A5 runs complete, and no order is printed unless the frozen v2 separation rule is met.</p>'
            f'<div class="v2-tbl-wrap"><table class="v2-tbl" data-protocol="v2"><thead><tr class="tbl-cols"><th scope="col">Cell</th>{lam_headers}</tr></thead>'
            f'<tbody><tr><td colspan="{len(disp_lams) + 1}">Awaiting verified A5 runs under '
            '<code>results/table/v2/chat_agent/</code> (zero mock or table numbers shown).</td></tr></tbody></table></div>'
            '<p class="v2-meta"><span class="lab x">exploratory</span> '
            'Empty instrument awaiting verified A5 runs.</p></article>'
        )

    if loaded:
        deciders = loaded["deciders"]
        acbi_panels: list[str] = []
        for did, slot in sorted(deciders.items()):
            acells = slot.get("acbi_cells") or {}
            arows: list[str] = []
            for row in slot["rows"]:
                a_entry = acells.get(row["cname"]) or {}
                b_counts = a_entry.get("counts") or a_entry.get("bucket_counts") or {}
                n0 = b_counts.get("n_none", b_counts.get("none", "n/a"))
                n1 = b_counts.get("n_partial", b_counts.get("partial", "n/a"))
                n2 = b_counts.get("n_both", b_counts.get("both", "n/a"))
                disp = DISPLAY.get(row["system"], row["system"])
                arows.append(
                    f'<tr data-base="{1 if row["base"] else 0}">'
                    f'<th scope="row">{E(disp)}</th>'
                    f'<td class="r" data-k="0/2 rules"><b>{E(str(n0))}</b></td>'
                    f'<td class="r" data-k="1/2 rules"><b>{E(str(n1))}</b></td>'
                    f'<td class="r" data-k="2/2 rules"><b>{E(str(n2))}</b></td>'
                    f'<td class="r" data-k="Wrong | 0/2">{_v2_ci_html(a_entry.get("p_wc_given_none"))}</td>'
                    f'<td class="r" data-k="Wrong | 1/2">{_v2_ci_html(a_entry.get("p_wc_given_partial"), "wrong")}</td>'
                    f'<td class="r" data-k="Wrong | 2/2">{_v2_ci_html(a_entry.get("p_wc_given_both"))}</td>'
                    f'<td class="r" data-k="ACBI">{_v2_ci_html(a_entry.get("acbi"))}</td>'
                    '</tr>'
                )
            sid = _slug(did)
            acbi_panels.append(
                f'<div class="v2-dpanel" id="acbi-panel-{sid}" role="tabpanel" aria-labelledby="acbi-tab-{sid}">'
                f'<h4 class="v2-dhead nojs-only">{E(did)} <span class="lab x">exploratory</span></h4>'
                '<div class="v2-tbl-wrap v2-no-scroll"><table class="v2-tbl v2-stack-mobile" data-protocol="v2">'
                f'<thead>{_v2_floor_thead(did, slot["floor"], slot["n_cases"], slot["n_gated"], 8)}'
                '<tr class="tbl-cols">'
                '<th scope="col">Cell</th>'
                '<th scope="col" class="r">0/2</th>'
                '<th scope="col" class="r">1/2</th>'
                '<th scope="col" class="r">2/2</th>'
                '<th scope="col" class="r">Wrong|0/2</th>'
                '<th scope="col" class="r">Wrong|1/2</th>'
                '<th scope="col" class="r">Wrong|2/2</th>'
                '<th scope="col" class="r">ACBI</th>'
                f'</tr></thead><tbody>{"".join(arows)}</tbody></table></div></div>'
            )
        acbi_card = (
            '<article class="v2-card v2-card-wide" data-tab-group="acbi" data-protocol="v2">'
            '<div class="v2-head-bar"><h3>Asymmetric Conflict Hazard (HU2) '
            '<span class="lab x">exploratory</span></h3></div>'
            '<p>Standalone text-free conflict audit partitioning HU2 views into 0/2, 1/2 (half-retrieved), and '
            '2/2 decisive standing rules. Empty buckets stay unfilled (n/a) rather than imputed; ACBI is '
            'reported only when both 1/2 and 2/2 buckets are populated.</p>'
            f'{_v2_tabs_bar("acbi", deciders, with_baselines=True)}'
            f'{"".join(acbi_panels)}'
            '<p class="v2-meta"><span class="lab x">exploratory</span> '
            'Computed from text-free <code>acbi.json</code> artifacts.</p></article>'
        )
    else:
        acbi_card = (
            '<article class="v2-card v2-card-wide" data-protocol="v2">'
            '<div class="v2-head-bar"><h3>Asymmetric Conflict Hazard (HU2) '
            '<span class="lab x">exploratory</span></h3></div>'
            '<p>Standalone text-free conflict audit partitioning HU2 views into 0/2, 1/2 (half-retrieved), and '
            '2/2 decisive standing rules. Empty buckets stay unfilled (n/a) rather than imputed; ACBI is '
            'reported only when both 1/2 and 2/2 buckets are populated.</p>'
            '<div class="v2-tbl-wrap"><table class="v2-tbl" data-protocol="v2"><thead><tr class="tbl-cols">'
            '<th scope="col">Cell</th><th scope="col" class="r">0/2</th><th scope="col" class="r">1/2</th><th scope="col" class="r">2/2</th>'
            '<th scope="col" class="r">Wrong|0/2</th><th scope="col" class="r">Wrong|1/2</th><th scope="col" class="r">Wrong|2/2</th><th scope="col" class="r">ACBI</th>'
            '</tr></thead><tbody><tr><td colspan="8">Awaiting verified A5 runs under '
            '<code>results/table/v2/chat_agent/</code> (zero mock or table numbers shown).</td></tr></tbody></table></div>'
            '<p class="v2-meta"><span class="lab x">exploratory</span> '
            'Empty instrument awaiting verified A5 runs.</p></article>'
        )

    roster_html = ""
    s1_list = (((proto_v2 or {}).get("steps") or {}).get("1") or {}).get("values", {}).get("phase1_deciders") or []
    s3_list = (((proto_v2 or {}).get("steps") or {}).get("3") or {}).get("values", {}).get("paid_phase1_deciders") or []
    s7_list = (((proto_v2 or {}).get("steps") or {}).get("7") or {}).get("values", {}).get("added_phase1_deciders") or []
    p1_list = [*s1_list[:1], *s3_list, *s7_list, *s1_list[1:]] if s1_list else [*s3_list, *s7_list]
    if p1_list:
        r_rows = ""
        for entry in p1_list:
            did = str(entry.get("id") or "")
            w_status = str(entry.get("weights_status") or "")
            w_url = str(entry.get("weights_url") or "")
            if w_url.startswith("https://"):
                prov = (
                    f'weights published at <a href="{E(w_url)}" rel="noopener">{E(w_url.removeprefix("https://"))}</a>; '
                    'served build not verified'
                )
            else:
                prov = E(w_status)
            v_file = _json(root / "results" / "v2" / "validity" / "chat_agent" / did / "validity.json")
            gate_txt = "gate review pending"
            if isinstance(v_file, dict):
                acc = ((v_file.get("accuracy") or {}).get("overall") or {}).get("accuracy")
                passes = bool((v_file.get("gate") or {}).get("passes"))
                if isinstance(acc, (int, float)):
                    gate_txt = f'{"pass" if passes else "quarantined"} · {100.0 * float(acc):.1f}%'
                elif v_file.get("complete") is False:
                    gate_txt = "quarantined · incomplete"
            r_rows += (
                '<tr role="row">'
                f'<th scope="row" role="rowheader"><code>{E(str(entry.get("model", "")))}</code></th>'
                f'<td role="cell" data-k="Interface">{E(str(entry.get("wrapper", "")))}</td>'
                f'<td class="c-wide" role="cell" data-k="Weights provenance">{prov}</td>'
                f'<td role="cell" data-k="Snapshot">{E(str(entry.get("seen_date", "")))}</td>'
                f'<td role="cell" data-k="Gate status">{E(gate_txt)}</td></tr>'
            )
        roster_html = (
            '<article class="v2-card v2-card-wide" data-protocol="v2">'
            '<div class="v2-head-bar"><h3>Registered Phase 1 decider roster '
            '<span class="lab x">exploratory</span></h3></div>'
            '<p>Frozen in <code>protocol/v2.json</code> Steps 1, 3, and 7. Every model takes the 60-case full-history '
            'validity gate first (&ge; 90.0% overall, &ge; 80.0% per pattern, context &ge; 65,536 without truncation). '
            'Only gate-passers enter table runs; non-passers appear as gate-failed and quarantined after review.</p>'
            '<div class="v2-tbl-wrap v2-no-scroll"><table class="v2-tbl v2-stack-mobile v2-wrap-cells" role="table" data-protocol="v2">'
            '<thead role="rowgroup"><tr class="tbl-cols" role="row">'
            '<th scope="col" role="columnheader">Model ID</th>'
            '<th scope="col" role="columnheader">Interface</th>'
            '<th scope="col" role="columnheader">Weights provenance</th>'
            '<th scope="col" role="columnheader">Snapshot</th>'
            '<th scope="col" role="columnheader">Gate status</th>'
            f'</tr></thead><tbody role="rowgroup">{r_rows}</tbody></table></div>'
            '<p class="v2-meta"><span class="lab x">exploratory</span> '
            'OpenCode v2.3.0 snapshot; local Ollama models excluded from Phase 1 roster.</p></article>'
        )

    return f'<div class="v2-grid" data-protocol="v2">{chow_card}{acbi_card}{roster_html}</div>'


_CAUSE_ORDER = ("graphiti", "mem0", "langmem")
_CAUSE_COLS = (
    ("not_stored", "Not stored"),
    ("stored_not_returned", "Stored, not returned"),
    ("returned_not_used", "Returned, not used"),
    ("conflict_returned", "Conflict returned"),
    ("by_rule", "By rule"),
)
_CAUSE_SENTENCES = {
    "graphiti": "Graphiti stays silent because it never stored the fact.",
    "mem0": "Mem0 acts wrong because it returns both the old and the new rule.",
    "langmem": "LangMem fails from a mix of missing facts, dropped retrieval, and returned conflicts.",
}
_INGEST_LEFT_OUT = {"graphiti": 13, "mem0": 2, "langmem": 0}


def _load_v2_cause_split(root: Path) -> dict[str, dict] | None:
    cdir = root / "results" / "v2" / "diagnostics" / "cause_split"
    out: dict[str, dict] = {}
    for prod in _CAUSE_ORDER:
        data = _json(cdir / f"{prod}_cause_split.json")
        if isinstance(data, dict) and isinstance(data.get("deciders"), dict):
            out[prod] = data
    return out or None


def _cause_cell_html(entry: dict | None, *, highlight: bool = False) -> str:
    if not entry:
        return '<div class="ci-cell"><b>0</b> <small>n/a</small></div>'
    cnt = int(entry.get("count") or 0)
    sh = entry.get("share")
    ci = entry.get("ci95")
    cls = " wrong" if highlight and cnt > 0 else ""
    if sh is None or ci is None:
        return f'<div class="ci-cell{cls}"><b>{cnt}</b> <small>n/a</small></div>'
    pct = f"{100.0 * float(sh):.1f}%"
    iv = f"[{100.0 * float(ci[0]):.1f}, {100.0 * float(ci[1]):.1f}]"
    return f'<div class="ci-cell{cls}"><b>{cnt} · {pct}</b> <small>{iv}</small></div>'


def _range_pct(decs: dict[str, Any], kind: str, cause_key: str) -> str:
    vals = []
    for dd in decs.values():
        sh = (((dd.get(kind) or {}).get("causes") or {}).get(cause_key) or {}).get("share")
        if isinstance(sh, (int, float)):
            vals.append(100.0 * float(sh))
    if not vals:
        return "n/a"
    lo, hi = min(vals), max(vals)
    return f"{lo:.1f}%" if abs(hi - lo) < 0.05 else f"{lo:.1f}% to {hi:.1f}%"


_CAUSE_LEGEND = (
    ("not_stored", "Not stored", "c-ns"),
    ("stored_not_returned", "Stored, not returned", "c-sn"),
    ("returned_not_used", "Returned, not used", "c-rn"),
    ("conflict_returned", "Conflict returned", "c-cf"),
    ("by_rule", "By rule", "c-br"),
)


def _cause_legend_html() -> str:
    items = "".join(
        f'<span class="leg"><i class="cause-sw {cls}"></i>{E(lbl)}</span>'
        for _, lbl, cls in _CAUSE_LEGEND
    )
    return f'<div class="cause-legend" aria-label="Cause split legend">{items}</div>'


def _cause_stacked_svg(prod: str, pdata: dict) -> str:
    """Pooled 5-cause stacked bar SVG across deciders for Withheld and Wrong decisions."""
    disp = DISPLAY.get(prod, prod)
    decs = pdata.get("deciders") or {}
    bars: list[tuple[str, int, list[tuple[str, int, str]]]] = []
    aria_parts: list[str] = []
    for kind_key, kind_lbl in (("withheld", "Withheld"), ("wrong", "Wrong")):
        counts = {ck: 0 for ck, _, _ in _CAUSE_LEGEND}
        total = 0
        for dd in decs.values():
            blk = dd.get(kind_key) or {}
            total += int(blk.get("total") or 0)
            causes = blk.get("causes") or {}
            for ck, _, _ in _CAUSE_LEGEND:
                counts[ck] += int((causes.get(ck) or {}).get("count") or 0)
        bars.append((kind_lbl, total, [(lbl, counts[ck], cls) for ck, lbl, cls in _CAUSE_LEGEND]))
        top_bits = ", ".join(
            f"{lbl} {100.0 * counts[ck] / total:.1f}%"
            for ck, lbl, _ in _CAUSE_LEGEND
            if total > 0 and counts[ck] > 0
        ) or "none"
        aria_parts.append(f"{kind_lbl} ({total}): {top_bits}")

    aria = f"{disp} pooled causes across 5 deciders. " + "; ".join(aria_parts)
    rows_svg: list[str] = []
    x0, bw = 128.0, 496.0
    for bidx, (kind_lbl, total, segs) in enumerate(bars):
        y = 10 + bidx * 34
        rows_svg.append(f'<text x="4" y="{y + 13}">{E(kind_lbl)}</text>')
        rows_svg.append(f'<text class="cause-sub" x="4" y="{y + 24}">{total} calls</text>')
        if total <= 0:
            rows_svg.append(f'<rect x="{x0}" y="{y}" width="{bw}" height="18" fill="none" stroke="var(--line)"/>')
            continue
        cx = x0
        for lbl, cnt, cls in segs:
            if cnt <= 0:
                continue
            w = bw * (cnt / total)
            rows_svg.append(
                f'<rect class="{cls}" x="{cx:.2f}" y="{y}" width="{w:.2f}" height="18">'
                f'<title>{E(kind_lbl)} {E(lbl)}: {cnt} ({100.0 * cnt / total:.1f}%)</title></rect>'
            )
            if w > 34:
                pct_s = f"{100.0 * cnt / total:.0f}%"
                tx = cx + w / 2.0
                rows_svg.append(
                    f'<text class="cause-pct" x="{tx:.1f}" y="{y + 13}" text-anchor="middle">{E(pct_s)}</text>'
                )
            cx += w
        rows_svg.append(f'<rect x="{x0}" y="{y}" width="{bw}" height="18" fill="none" stroke="var(--ink)"/>')
    return (
        '<div class="cause-svg-wrap">'
        f'<svg class="cause-svg" viewBox="0 0 636 80" role="img" aria-label="{E(aria)}">'
        f'<title>{E(aria)}</title>{"".join(rows_svg)}</svg></div>'
    )


def _cause_panel_inner(prod: str, disp: str, pdata: dict, fullrow_line: str = "") -> str:
    rf = pdata.get("replay_fidelity") or {}
    rate = rf.get("view_unit_match_rate")
    matched = int(rf.get("matched_view_units") or 0)
    total_u = int(rf.get("total_view_units") or 0)
    low_fid = bool(rf.get("low_fidelity"))
    rate_txt = f"{100.0 * float(rate):.1f}%" if isinstance(rate, (int, float)) else "n/a"
    fid_badge = '<span class="lab x">low-fidelity</span>' if low_fid else '<span class="lab">high-fidelity</span>'
    decs = pdata.get("deciders") or {}
    floor_note = "5 gate-passing deciders · 1,000-token view budget · 300 cases"
    fid_line = (
        '<div class="cause-fid-bar">'
        f'<span><b>Replay view-unit match: {E(rate_txt)}</b> ({matched:,} / {total_u:,} units in dumped store)</span>'
        f'<span>{fid_badge} '
        + ("<span>Store vs. retrieval split is weaker below 90% match.</span>" if low_fid else "<span>High store-to-view agreement.</span>")
        + "</span></div>"
    )
    rows_html: list[str] = []
    for did, dd in sorted(decs.items()):
        for kind_key, kind_lbl in (("wrong", "Wrong action"), ("withheld", "Withheld action")):
            block = dd.get(kind_key) or {}
            causes = block.get("causes") or {}
            tds = "".join(
                f'<td class="r" data-k="{E(col_lbl)}">'
                f'{_cause_cell_html(causes.get(col_key), highlight=(col_key in ("not_stored", "conflict_returned")))}</td>'
                for col_key, col_lbl in _CAUSE_COLS
            )
            rows_html.append(
                '<tr>'
                f'<th scope="row"><b>{E(did)}</b><br><span>{E(kind_lbl)}</span></th>'
                f'<td class="r" data-k="Total"><b>{int(block.get("total") or 0)}</b></td>'
                f'{tds}</tr>'
            )
    col_ths = "".join(f'<th scope="col" class="r">{E(lbl)}</th>' for _, lbl in _CAUSE_COLS)
    return (
        f'{fid_line}{fullrow_line}{_cause_stacked_svg(prod, pdata)}'
        '<div class="v2-tbl-wrap v2-no-scroll"><table class="v2-tbl v2-stack-mobile" data-protocol="v2">'
        '<thead>'
        f'<tr class="tbl-floor-row"><th scope="colgroup" colspan="7">'
        f'<b>{E(disp)} · {E(floor_note)}</b> <span class="lab x">exploratory</span></th></tr>'
        '<tr class="tbl-cols"><th scope="col">Decider &amp; decision</th>'
        f'<th scope="col" class="r">Total</th>{col_ths}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table></div>'
    )


def _v2_cause_split_section(root: Path, chat_loaded: dict | None = None, *, compact_link: bool = True) -> str:
    data = _load_v2_cause_split(root)
    if not data:
        return ""

    cards_html: list[str] = []
    tabs_html: list[str] = []
    panels_html: list[str] = []

    for idx, prod in enumerate(_CAUSE_ORDER):
        pdata = data.get(prod)
        if not pdata:
            continue
        disp = DISPLAY.get(prod, prod)
        rf = pdata.get("replay_fidelity") or {}
        rate = rf.get("view_unit_match_rate")
        low_fid = bool(rf.get("low_fidelity"))
        rate_txt = f"{100.0 * float(rate):.1f}%" if isinstance(rate, (int, float)) else "n/a"
        fid_badge = '<span class="lab x">low-fidelity</span>' if low_fid else '<span class="lab">high-fidelity</span>'
        decs = pdata.get("deciders") or {}
        if prod == "graphiti":
            detail = (
                f"Not stored: {_range_pct(decs, 'withheld', 'not_stored')} of withheld actions; "
                f"stored but dropped retrieval: {_range_pct(decs, 'withheld', 'stored_not_returned')}. "
                f"{_INGEST_LEFT_OUT[prod]} ingest-error cases left out."
            )
        elif prod == "mem0":
            detail = (
                f"Both rules returned: {_range_pct(decs, 'wrong', 'conflict_returned')} of wrong actions. "
                f"{_INGEST_LEFT_OUT[prod]} ingest-error cases left out. Replay match {rate_txt}."
            )
        else:
            detail = (
                f"Withheld split across missing ({_range_pct(decs, 'withheld', 'not_stored')}), "
                f"dropped ({_range_pct(decs, 'withheld', 'stored_not_returned')}), and unused facts. "
                f"Replay match {rate_txt}."
            )

        cards_html.append(
            '<article class="cause-card" data-protocol="v2">'
            f'<div class="cause-card-top"><h3><a class="sys-link" href="product-{_slug(prod)}.html">{E(disp)}</a></h3>'
            f'<span>{fid_badge} <span class="lab x">exploratory</span></span></div>'
            f'<p class="cause-takeaway">{E(_CAUSE_SENTENCES[prod])}</p>'
            f'{_cause_stacked_svg(prod, pdata)}'
            f'<p class="cause-detail">{E(detail)}</p>'
            '</article>'
        )

        tab_note = f"{rate_txt} match · low-fidelity" if low_fid else f"{rate_txt} view-unit match"
        tabs_html.append(
            f'<button class="v2-tab" type="button" role="tab" id="cause-tab-{E(prod)}" '
            f'aria-controls="cause-panel-{E(prod)}" aria-selected="{"true" if idx == 0 else "false"}">'
            f'{E(disp)}<span>{E(tab_note)}</span></button>'
        )

        fullrow_line = ""
        if compact_link and chat_loaded and isinstance(chat_loaded.get("deciders"), dict):
            wrs, whs, ris = [], [], []
            for slot in chat_loaded["deciders"].values():
                match_row = next((r for r in slot.get("rows") or [] if r.get("system") == prod), None)
                if match_row:
                    cell = match_row["cell"]
                    for arr, key in ((wrs, "wrong_commit"), (whs, "abstention"), (ris, "right_action")):
                        v = (cell.get(key) or {}).get("value")
                        if isinstance(v, (int, float)):
                            arr.append(100.0 * float(v))
            if wrs and whs and ris:
                def _rng(vs: list[float]) -> str:
                    lo, hi = min(vs), max(vs)
                    return f"{lo:.1f}%" if abs(hi - lo) < 0.05 else f"{lo:.1f}% to {hi:.1f}%"
                fullrow_line = (
                    '<div class="cause-fullrow" data-protocol="v2">'
                    f'<strong>{E(disp)} full row across 5 deciders:</strong> '
                    f'wrong {_rng(wrs)} · withheld {_rng(whs)} · right {_rng(ris)} · '
                    f'{_INGEST_LEFT_OUT[prod]} ingest-error cases left out · '
                    f'<a href="product-{_slug(prod)}.html">Product page</a> · '
                    '<a href="chat.html#chat-board">Open full 40-cell chat board</a></div>'
                )

        panels_html.append(
            f'<div class="v2-dpanel" id="cause-panel-{E(prod)}" role="tabpanel" aria-labelledby="cause-tab-{E(prod)}">'
            f'<h3 class="v2-dhead nojs-only">{E(disp)} cause split <span class="lab x">exploratory</span></h3>'
            + _cause_panel_inner(prod, disp, pdata, fullrow_line)
            + '</div>'
        )

    return (
        '<section class="sec" id="cause-split" aria-labelledby="cause-split-h" data-protocol="v2">'
        '<div class="sec-head"><h2 id="cause-split-h">Why memory products fail <span class="lab x">exploratory</span></h2>'
        '<span class="sub">Offline diagnostic over replayed stores and shared 1,000-token views across 300 chat cases. '
        'Numbers stay exploratory and always sit beside the full row.</span></div>'
        + _cause_legend_html()
        + f'<div class="cause-cards">{"".join(cards_html)}</div>'
        '<article class="v2-card v2-card-wide" data-tab-group="cause">'
        f'<div class="v2-tab-rail js-only"><div class="tablist" role="tablist" aria-label="Memory product">{"".join(tabs_html)}</div></div>'
        f'{"".join(panels_html)}'
        '</article></section>'
    )


def _limits_section(*, v2_only: bool = True) -> str:
    """Honest limits rendered on both the front page and the chat page (C4)."""
    track_note = (
        "Protocol v2 measures synthetic multi-session chat (chat_agent). Coding Agent runs are held. Frozen v1 account-review lives on the archive page."
        if v2_only
        else "Protocol v2 measures synthetic multi-session chat (chat_agent). Protocol v1 covers account review. Coding Agent sweeps are held."
    )
    cases_note = (
        "All 300 v2 chat cases come from a deterministic generator, not production histories."
        if v2_only
        else "All 300 v2 chat cases and all 300 v1 table cases come from deterministic generators, not production histories."
    )
    bullets = (
        ("One chat track only.", track_note),
        ("Five deciders.", "All v2 chat numbers come from the five deciders that passed the full-history validity gate. Other models may read the same views differently."),
        ("Synthetic cases.", cases_note),
        ("Ingest-error cases left out.", "Mem0 drops 2 cases and Graphiti drops 13 cases during ingestion out of 300. Those left-out counts sit on every row."),
        ("The cause split is exploratory.", "It replays dumped stores with rule-set buckets (By rule) and pattern matchers; Mem0 and LangMem splits are marked low-fidelity."),
    )
    items = "".join(f"<li><strong>{E(head)}</strong> {E(body)}</li>" for head, body in bullets)
    proto_attr = "v2" if v2_only else "v2"
    return (
        f'<section class="sec" id="limits" aria-labelledby="limits-h" data-protocol="{proto_attr}">'
        '<div class="sec-head"><h2 id="limits-h">What this does not show</h2>'
        '<span class="sub">Scope and boundaries of the current v2 chat table.</span></div>'
        f'<div class="limits-box"><ul class="limits-list">{items}</ul></div>'
        '</section>'
    )


def _reproduce_section(*, v2: bool, root: Path | None = None, gate_id: str = "terms-repro") -> str:
    if v2:
        cmds = (
            "uv run bench freeze --check --protocol protocol/v2.json --accept-terms",
            "uv run bench verify results/table/v2/chat_agent/haiku-5-5/bm25 --accept-terms",
            "uv run bench verdict results/table/v2/chat_agent/*/* --accept-terms",
            "uv run bench site build --out site/preview-v2 --accept-terms",
            "uv run bench site serve --port 8795 --accept-terms",
        )
        lead = "Every number on this page traces to shipped JSON packs under results/table/v2/chat_agent/."
        ptag = "v2"
    else:
        cmds = (
            "uv run bench freeze --check --protocol protocol/v1.json --accept-terms",
            "uv run bench verdict results/table/v1/day13-table-* --accept-terms",
            "uv run bench site build --out site/dist --accept-terms",
            "uv run bench site serve --port 8795 --accept-terms",
        )
        lead = "Every number on this page traces to shipped JSON packs under results/table/v1/."
        ptag = "v1"
    return (
        f'<section class="sec" id="reproduce" aria-labelledby="repro-h" data-protocol="{ptag}">'
        '<div class="sec-head"><h2 id="repro-h">Reproduce this table</h2>'
        '<span class="sub">Zero API calls required to verify hashes, rescore metrics, and rebuild pages locally.</span></div>'
        f'<div class="repro-box"><p>{E(lead)}</p>'
        + _terms_gate_box(root, gate_id, "Terms notice (tick before copying commands)")
        + "".join(_job(c, gate_id=gate_id) for c in cmds)
        + '</div></section>'
    )


TOY_ADAPTER_CODE = """from __future__ import annotations
from collections.abc import Sequence
from memory_bench.adapters.base import MemoryAdapter, Record, Retrieved

class ToyKeywordAdapter(MemoryAdapter):
    name = "toy_keyword"
    product_version = "0.1.0"
    config_label = "default"
    reference = False
    needs_proxy = False

    def __init__(self) -> None:
        self._order: list[str] = []
        self._docs: dict[str, str] = {}

    def reset(self, namespace: str) -> None:
        self._order, self._docs = [], {}

    def ingest(self, records: Sequence[Record]) -> None:
        for r in records:
            self._order.append(r.id)
            self._docs[r.id] = r.text

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        terms = {w.strip(".,?!:;").lower() for w in query.split() if w.strip(".,?!:;")}
        rank = {rid: i for i, rid in enumerate(self._order)}
        scored = [(sum(1 for t in terms if t in self._docs[r].lower()), rank[r], r) for r in self._order]
        hits = sorted([x for x in scored if x[0] > 0], key=lambda x: (-x[0], x[1]))
        chosen = sorted([rid for _, _, rid in hits[:25]], key=rank.__getitem__)
        units = [self._docs[rid] for rid in chosen]
        return Retrieved(text="\\n".join(units), record_ids=chosen, units=units)

    def close(self) -> None:
        self._order, self._docs = [], {}"""


def _submit_steps_html(root: Path | None = None) -> str:
    gate_id = "terms-submit"
    s1 = _terms_gate_box(root, gate_id, "Terms notice (tick before copying install or run commands)") + _job("uv sync", gate_id=gate_id) + _job("uv run bench quickstart --mock --accept-terms", gate_id=gate_id)
    s2 = (
        '<p>Create <code>adapters/toy_keyword/adapter.py</code> without editing <code>src/</code> or <code>protocol/</code>.</p>'
        '<div class="code-block"><div class="code-head"><span>adapters/toy_keyword/adapter.py</span>'
        + _copy_btn(TOY_ADAPTER_CODE, "Copy adapter")
        + f'</div><pre><code>{E(TOY_ADAPTER_CODE)}</code></pre></div>'
    )
    s3 = (
        _job("uv run bench run --mock --n 20 --cases data/v2/chat_agent/table_cases.jsonl --adapters toy_keyword --budgets 1000 --repeats 3 --out results/runs/toy-mock --accept-terms", gate_id=gate_id)
        + _job("uv run bench submit pack results/runs/toy-mock --out submissions --exploratory --accept-terms", gate_id=gate_id)
        + _job("uv run bench verify submissions/toy_keyword/<YYYY-MM-DD>-default --accept-terms", gate_id=gate_id)
    )
    s4 = (
        _job("uv run bench site build --out site/dist --accept-terms", gate_id=gate_id)
        + _job("uv run bench site serve --port 8795 --accept-terms", gate_id=gate_id)
    )
    cards = (
        ("Step 1: Install and check (zero cost)", s1),
        ("Step 2: Write the worked toy adapter", s2),
        ("Step 3: Smoke test, pack exploratory, and verify", s3),
        ("Step 4: Preview locally and open a pull request", s4),
    )
    body = "".join(f'<article class="step-card"><h3>{E(t)}</h3>{c}</article>' for t, c in cards)
    return f'<h2>Four-step worked submission</h2><div class="step-list">{body}</div>'


def _method_prose(root: Path) -> str:
    return (
        _copy_block(root, COPY["method.html"])
        + f'<section id="limits">{_copy_block(root, COPY["limits"])}</section>'
        + '<section id="licence"><h2>Licence</h2>'
        '<p>Benchmark code is licensed under Apache-2.0 (<code>LICENSE</code>). '
        'Released synthetic cases and metric pack files are licensed under CC BY 4.0 (<code>LICENSE-DATA</code>). '
        'See <a href="legal.html">Legal notice</a> and <a href="terms.html">Terms of use</a>.</p></section>'
        + '<section id="citation"><h2>Citation</h2>'
        '<p>Release metadata lives in <code>CITATION.cff</code> (version <code>2.0.0-rc1</code>, date <code>2026-11-25</code>).</p>'
        '<div class="code-block"><div class="code-head"><span>BibTeX</span>'
        + _copy_btn(BIBTEX_ENTRY, "Copy BibTeX")
        + f'</div><pre><code>{E(BIBTEX_ENTRY)}</code></pre></div></section>'
    )


def _v2_product_pages(
    root: Path,
    chat_loaded: dict,
    cause_data: dict[str, dict] | None,
    proto: dict,
    proto_v2: dict | None,
    built_at: str,
    *,
    site_url: str = "https://hercyna.org",
    asset_refs: dict[str, str] | None = None,
    newsletter_action: str | None = None,
    analytics_src: str | None = None,
) -> dict[str, str]:
    excused_cfg = _json(root / "configs" / "v2" / "excused_ingest_errors.json") or {}
    excused_sys = excused_cfg.get("systems") or {}
    sep = chat_loaded.get("separation") or {}
    pairs = [(str(p["pair"]), str(p["direction"])) for p in (sep.get("separating_pairs") or [])]
    lo_members = {p.partition(" | ")[0] if d == "a_lower" else p.partition(" | ")[2] for p, d in pairs}
    hi_members = {p.partition(" | ")[2] if d == "a_lower" else p.partition(" | ")[0] for p, d in pairs}

    deciders = chat_loaded["deciders"]
    sys_order: list[str] = []
    for slot in deciders.values():
        for row in slot["rows"]:
            if row["system"] not in sys_order:
                sys_order.append(row["system"])

    pages: dict[str, str] = {}
    base_url = site_url.rstrip("/")
    for sys_name in sys_order:
        disp = DISPLAY.get(sys_name, sys_name)
        slug_name = _slug(sys_name)
        rows_across = []
        for did, slot in sorted(deciders.items()):
            match = next((r for r in slot["rows"] if r["system"] == sys_name), None)
            if match is not None:
                rows_across.append((did, slot, match))
        if not rows_across:
            continue
        sample = rows_across[0][2]
        cname_short = f"{sys_name}/{sample['config_label']}"
        exc_list = list(excused_sys.get(cname_short) or [])
        trs: list[str] = []
        for ridx, (did, slot, row) in enumerate(rows_across):
            if row["base"]:
                badge = '<span class="rank-tag unranked">baseline</span>'
                show_split = False
            elif row.get("over_excused_limit"):
                badge = '<span class="rank-tag unranked">unranked (&gt;5% excused)</span>'
                show_split = False
            elif row["cname"] in lo_members and row["cname"] not in hi_members:
                badge = '<span class="rank-tag">Lower wrong action</span>'
                show_split = True
            elif row["cname"] in hi_members:
                badge = '<span class="rank-tag r2">Higher wrong action</span>'
                show_split = True
            else:
                badge = '<span class="rank-tag unranked">not separated</span>'
                show_split = False
            fl = slot.get("floor") or {}
            fl_w = (_rate(fl.get("wrong_commit")) or {}).get("t", "n/a")
            trs.append(
                _v2_full_row_tr(
                    row,
                    rank_badge=badge,
                    extra_meta=f"floor wrong {fl_w}",
                    show_split=show_split,
                    row_idx=ridx,
                    link_product=False,
                    sys_cell_text=did,
                )
            )

        kind_lbl = "Algorithmic baseline" if sample["base"] else "Memory product"
        meta_strip = (
            '<div class="prod-meta-strip" data-protocol="v2">'
            f'<span class="lab">{E(kind_lbl)}</span>'
            f'<span class="lab x">exploratory</span>'
            f'<span>Config: <code>{E(sample["config"])}</code></span>'
            f'<span>Left-out cases: <b>{len(exc_list)}</b> of 300</span>'
            '<a class="more" href="chat.html#chat-board">Back to 40-cell table</a></div>'
        )
        table_html = (
            '<section class="sec" id="product-rows" aria-labelledby="prod-rows-h" data-protocol="v2">'
            '<div class="sec-head"><h2 id="prod-rows-h">Results across all gate-passing deciders</h2>'
            '<span class="sub">Click any rate or count to open the underlying JSON pack.</span></div>'
            '<div class="v2-full-wrap"><table class="v2-full-tbl" data-protocol="v2">'
            '<thead><tr class="tbl-cols">'
            '<th scope="col">Decider</th>'
            '<th scope="col">Right · withheld · wrong share</th>'
            '<th scope="col" class="r">Wrong action</th>'
            '<th scope="col" class="r">Withheld action</th>'
            '<th scope="col" class="r">Right action</th>'
            '<th scope="col" class="r">Ingest-error cases left out</th>'
            f'</tr></thead><tbody>{"".join(trs)}</tbody></table></div></section>'
        )
        if cause_data and sys_name in cause_data:
            cause_block = (
                '<section class="sec" id="product-causes" aria-labelledby="prod-cause-h" data-protocol="v2">'
                '<div class="sec-head"><h2 id="prod-cause-h">Cause split <span class="lab x">exploratory</span></h2>'
                f'<span class="sub">{E(_CAUSE_SENTENCES[sys_name])}</span></div>'
                + _cause_legend_html()
                + _cause_panel_inner(sys_name, disp, cause_data[sys_name])
                + '</section>'
            )
        else:
            cause_block = (
                '<section class="sec" id="product-causes" aria-labelledby="prod-cause-h" data-protocol="v2">'
                '<div class="sec-head"><h2 id="prod-cause-h">Cause split</h2>'
                '<span class="sub">Offline store-replay diagnostic applies to product adapters only.</span></div>'
                '<div class="limits-box"><p>This system is an algorithmic baseline without an external store or '
                'LLM extractor dump.</p></div></section>'
            )

        if exc_list:
            err_hint = " (JSONDecodeError)" if sys_name == "graphiti" else (" (AttributeError)" if sys_name == "mem0" else "")
            lis = "".join(f'<li><code>{E(cid)}</code></li>' for cid in exc_list)
            exc_body = (
                f'<p><b>{len(exc_list)}</b> of 300 cases failed ingestion{E(err_hint)} and are frozen in '
                '<a href="packs/v2/excused_ingest_errors.json">configs/v2/excused_ingest_errors.json</a> '
                'within the hard 5.0% cap.</p>'
                f'<ul class="excused-list">{lis}</ul>'
            )
        else:
            exc_body = '<p><b>0</b> of 300 cases omitted for ingest errors. Verified against <a href="packs/v2/excused_ingest_errors.json">configs/v2/excused_ingest_errors.json</a>.</p>'
        exc_block = (
            '<section class="sec" id="product-excused" aria-labelledby="prod-exc-h" data-protocol="v2">'
            '<div class="sec-head"><h2 id="prod-exc-h">Left-out ingest-error cases</h2>'
            '<span class="sub">Excluded from decision rates and shown beside every row.</span></div>'
            f'<div class="limits-box">{exc_body}</div></section>'
        )

        fname = f"product-{slug_name}.html"
        badge_md = f"[![Hercyna v2: Evaluated {disp}]({base_url}/badge/{slug_name}.svg)]({base_url}/v2/{fname})"
        badge_block = (
            '<section class="sec" id="product-badge" aria-labelledby="prod-badge-h" data-protocol="v2">'
            '<div class="sec-head"><h2 id="prod-badge-h">Embeddable participation badge</h2>'
            '<span class="sub">Links to this full evaluation page, never to a single score.</span></div>'
            '<div class="badge-box">'
            f'<div class="badge-preview"><img src="badge/{E(slug_name)}.svg" alt="Evaluated on Hercyna v2: {E(disp)}" width="290" height="22">'
            '<span>Neutral protocol badge only; implies no certification or endorsement.</span></div>'
            '<div class="code-block"><div class="code-head"><span>Markdown</span>'
            + _copy_btn(badge_md, "Copy badge Markdown", "btn")
            + f'</div><pre><code>{E(badge_md)}</code></pre></div></div></section>'
        )
        main_html = (
            f'<div class="doc"><h1>{E(disp)}</h1>'
            + meta_strip
            + table_html
            + cause_block
            + exc_block
            + badge_block
            + _reproduce_section(v2=True, root=root, gate_id=f"terms-{slug_name}")
            + '</div>'
        )
        pages[fname] = _page(
            disp,
            fname,
            main_html,
            proto,
            built_at,
            proto_v2=proto_v2,
            proto_tag="v2",
            show_seed=True,
            asset_refs=asset_refs,
            site_url=base_url,
            canonical_path=fname,
            og_image="og/chat.png",
            description=f"{disp} multi-session decision evaluation under Hercyna Protocol v2 across 5 deciders and 300 cases.",
            newsletter_action=newsletter_action,
            analytics_src=analytics_src,
            root=root,
        )
    return pages


def _copy_packs(
    root: Path,
    out: Path,
    sources: list[dict],
    chat_loaded: dict | None,
    proto_v2: dict | None,
) -> dict[str, int]:
    """Copy text-free JSON artifacts into <out>/packs/ so every number links to a verifiable file."""
    sizes: dict[str, int] = {}
    packs_dir = out / "packs"
    for src in sources:
        sfile = src["dir"] / "summary.json"
        if sfile.is_file():
            dst = packs_dir / "v1" / _slug(str(src["id"])) / "summary.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sfile, dst)
            sizes[str(dst.relative_to(out).as_posix())] = dst.stat().st_size

    pv1 = root / "protocol" / "v1.json"
    if pv1.is_file():
        dst = packs_dir / "v1" / "protocol-v1.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pv1, dst)
        sizes[str(dst.relative_to(out).as_posix())] = dst.stat().st_size

    if chat_loaded:
        for rdir in chat_loaded.get("run_dirs") or []:
            did = rdir.parent.name
            sys_slug = rdir.name
            for fname in ("summary.json", "manifest.json", "health.json"):
                fp = rdir / fname
                if fp.is_file():
                    dst = packs_dir / "v2" / "chat_agent" / did / sys_slug / fname
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fp, dst)
                    sizes[str(dst.relative_to(out).as_posix())] = dst.stat().st_size

    if proto_v2 is not None:
        pv2 = root / "protocol" / "v2.json"
        if pv2.is_file():
            dst = packs_dir / "v2" / "protocol-v2.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pv2, dst)
            sizes[str(dst.relative_to(out).as_posix())] = dst.stat().st_size

    exc_p = root / "configs" / "v2" / "excused_ingest_errors.json"
    if exc_p.is_file():
        dst = packs_dir / "v2" / "excused_ingest_errors.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exc_p, dst)
        sizes[str(dst.relative_to(out).as_posix())] = dst.stat().st_size
    return sizes


def _chat_page(root: Path, loaded: dict | None = None) -> str:
    proto_v2 = _json(root / "protocol" / "v2.json") or {}
    sha8 = str(proto_v2.get("protocol_sha256") or "")[:8]
    proto_note = f"Protocol v2 frozen ({sha8}). " if sha8 else ""
    if loaded is None:
        loaded = _load_v2_chat_table(root)
    cards = [
        (
            "Multi-Session Blind-Adapter Evaluation",
            "Longitudinal user histories (55 to 190 turns) tested at 1,000 and 4,000 view tokens against the untruncated full-history floor.",
            [
                "Measures right action, withheld action, and wrong action side by side",
                "Stratified temporal half-life across near, middle, and far override lag buckets",
                "Dual-ingest variance study isolates ingestion drift from decider repeat variance",
            ],
        ),
        (
            "Shared Extractor Gate Discipline",
            "Every shared memory-side extractor must pass the full-history validity gate before table runs.",
            [
                "Local private port (127.0.0.1:11535) keeps benchmark sweeps off shared host services",
                "View-audit seal verifies zero failed memory-side calls inside stored view windows",
                "Asymmetric conflict cases check whether returning one of two constraints induces wrong action",
            ],
        ),
    ]
    card_html = "".join(
        '<article class="v2-card">'
        f'<h3>{E(head)}</h3><p>{E(desc)}</p>'
        + ("<ul>" + "".join(f"<li>{E(item)}</li>" for item in items) + "</ul>" if items else "")
        + "</article>"
        for head, desc, items in cards
    )
    if loaded:
        n_decs = len(loaded["deciders"])
        banner = (
            '<div class="pending" role="note"><strong>exploratory preview</strong>'
            f'<span>{E(proto_note)}40-cell Chat Agent Mode A sweep complete across {n_decs} gate-passing deciders '
            'and 8 memory systems (budget 1,000 tokens). Every table on this page remains exploratory until '
            'accepted for release. Archived v1 account-review tables live on <a href="v1.html">v1 archive</a>.</span></div>'
        )
        stem = f"hercyna-v2-chat-{sha8}" if sha8 else "hercyna-v2-chat"
        data_bar = (
            '<div class="data-bar" id="chat-downloads" data-protocol="v2">'
            '<div><strong>Download the 40-cell table</strong> '
            '<span>CSV &amp; JSON stamped with protocol hash · Experimental results. See <a href="terms.html">TERMS</a>.</span></div>'
            '<div class="data-bar-btns">'
            f'<a class="btn" href="data/{E(stem)}.csv" download>Download CSV</a>'
            f'<a class="btn" href="data/{E(stem)}.json" download>Download JSON</a>'
            + _copy_btn(BIBTEX_ENTRY, "Copy BibTeX", "btn")
            + '</div></div>'
        )
        tables_html = data_bar + _v2_ranked_pairs_section(loaded) + _v2_chat_board_section(loaded)
        inst_sub = "Fixed row order, labelled exploratory, with uniform 95% paired-bootstrap intervals."
    else:
        banner = (
            '<div class="pending" role="note"><strong>exploratory preview</strong>'
            f'<span>{E(proto_note)}No mock dry-run numbers or frozen v2 table results are published here yet; '
            'exploratory instruments populate only after verified A5 runs are reviewed. '
            'Archived v1 account-review tables live on <a href="v1.html">v1 archive</a>.</span></div>'
        )
        tables_html = ""
        inst_sub = "Fixed row order, labelled exploratory, with zero mock or table numbers and no printed order."
    return (
        '<div class="doc">'
        '<h1>Chat Agent Track</h1>'
        + banner
        + tables_html
        + _v2_cause_split_section(root, loaded, compact_link=False)
        + _reproduce_section(v2=True, root=root, gate_id="terms-chat-repro")
        + _limits_section(v2_only=True)
        + _chat_dev_anatomy(root)
        + '<section class="sec" id="chat-instruments" aria-labelledby="chat-inst-h"><div class="sec-head">'
        '<h2 id="chat-inst-h">Registered exploratory instruments</h2>'
        f'<span class="sub">{E(inst_sub)}</span></div>'
        + _chat_dryrun_instruments(root, loaded)
        + '</section>'
        '<section class="sec" aria-labelledby="v2-modes-h"><div class="sec-head">'
        '<h2 id="v2-modes-h">Evaluation design</h2>'
        '<span class="sub">Multi-session decision continuity across overrides, expiry, guards, and conflicting constraints.</span></div>'
        f'<div class="v2-grid">{card_html}</div></section>'
        '<p class="mt-14"><a class="more" href="index.html#board">Return to the v2 Board</a></p>'
        '</div>'
    )


def _v1_archive_page(
    sources: list[dict],
    deciders: dict[str, dict],
    verdict: dict | None,
    vals: dict,
    proto: dict,
    media: set[str],
    root: Path | None = None,
) -> str:
    """Archived Protocol v1 account-review board (strictly zero v2 content)."""
    banner = (
        '<div class="pending" role="note" data-protocol="v1"><strong>v1 archive: account review</strong>'
        '<span>Frozen Protocol v1 account-review benchmark (single-session entity state, table seed 1283645051). '
        'Phase 2 multi-session chat results lead on the <a href="index.html#board">v2 Board</a>.</span></div>'
    )
    return (
        banner
        + _hero(vals, has_v2_chat=False, root=root)
        + _kpis(deciders, verdict, vals)
        + _board(sources, deciders, verdict, vals)
        + _reproduce_section(v2=False, root=root, gate_id="terms-v1-repro")
        + _how(deciders, vals, media)
        + _anatomy()
        + _method_submit(proto, protocol="v1")
    )


def _markdown_legal_page(root: Path, md_filename: str, fallback_title: str) -> str:
    """Render TERMS.md, LEGAL.md, or PRIVACY.md verbatim from repo root (single source of truth)."""
    p = root / md_filename
    if p.is_file():
        raw = p.read_text(encoding="utf-8")
        # Rewrite local repo doc refs into live site or repository links so internal link audits never break
        repl = {
            "(corrections.html)": "(corrections.html)",
            "(TERMS.md)": "(terms.html)",
            "(LEGAL.md)": "(legal.html)",
            "(PRIVACY.md)": "(privacy.html)",
            "(.github/ISSUE_TEMPLATE/correction_request.md)": "(https://github.com/hercyna/hercyna/issues/new?template=correction_request.md)",
        }
        for k, v in repl.items():
            raw = raw.replace(k, v)
        body = mdlite.render(raw)
        return f'<div class="doc" data-protocol="v2">{body}</div>'
    return f'<div class="doc" data-protocol="v2"><h1>{E(fallback_title)}</h1><p>Document pending.</p></div>'


def _changelog_page(root: Path) -> str:
    entries = assets_gen.parse_changelog_entries(root / "CHANGELOG.md")
    blocks: list[str] = []
    for ent in entries:
        ver = ent["version"]
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", ver).strip("-")
        note_html = f'<p>{E(ent["note"])}</p>' if ent.get("note") else ""
        secs_html = ""
        for sec in ent.get("sections") or []:
            lis = "".join(f"<li>{E(item)}</li>" for item in sec["items"])
            secs_html += f'<h3>{E(sec["heading"])}</h3><ul>{lis}</ul>'
        blocks.append(
            f'<article class="step-card" id="{E(slug)}">'
            f'<h2>{E(ver)} <span class="mono">({E(ent["date"])})</span></h2>'
            f'{note_html}{secs_html}</article>'
        )
    if not blocks:
        blocks.append('<p>No changelog entries recorded yet.</p>')
    return (
        '<div class="doc" data-protocol="v2">'
        '<h1>Release Notes and Changelog</h1>'
        '<div class="pending" role="note"><strong>feeds</strong>'
        '<span>Experimental results. See <a href="terms.html">Terms</a>. '
        'Subscribe directly via the <a href="feed.xml">Atom feed (feed.xml)</a>.</span></div>'
        f'<div class="step-list">{"".join(blocks)}</div>'
        '</div>'
    )


def _corrections_page() -> str:
    issue_url = "https://github.com/hercyna/hercyna/issues/new?template=correction_request.md"
    return (
        '<div class="doc" data-protocol="v2">'
        '<h1>Corrections and Right of Reply</h1>'
        '<div class="pending" role="note"><strong>public log</strong>'
        '<span>Every verified factual error, packaging defect, or maintainer rerun is published here with protocol hashes.</span></div>'
        '<section class="sec" aria-labelledby="corr-proc-h"><div class="sec-head">'
        '<h2 id="corr-proc-h">How to request a correction or rerun</h2>'
        '<span class="sub">Applies to all benchmark results under Terms point 6.</span></div>'
        '<div class="limits-box"><ol class="terms-list">'
        f'<li><strong>Open a request:</strong> Submit the <a href="{E(issue_url)}" rel="noopener">correction request template</a> naming the product, protocol hash (<code>v2 68af8d8e</code> or <code>v1 99494597</code>), and affected metric or file.</li>'
        '<li><strong>Attach verification (if submitting a rerun):</strong> Pack only text-free run artifacts verified cleanly with <code>bench verify</code>.</li>'
        '<li><strong>Response target:</strong> Maintainers aim to acknowledge correction requests within <strong>5 business days</strong> and publish a confirmed determination or updated exploratory row within <strong>15 business days</strong>.</li>'
        '<li><strong>Cache hygiene:</strong> Built HTML and pack JSON responses serve <code>max-age=300, must-revalidate</code> so verified corrections replace prior pages without stale caching.</li>'
        '</ol></div></section>'
        '<section class="sec" aria-labelledby="corr-log-h"><div class="sec-head">'
        '<h2 id="corr-log-h">Public Corrections Log</h2>'
        '<span class="sub">Empty at launch. New corrections append newest-first.</span></div>'
        '<div class="v2-tbl-wrap"><table class="doc-table" data-protocol="v2">'
        '<thead><tr><th scope="col">Date</th><th scope="col">Protocol</th><th scope="col">Product / Cell</th><th scope="col">Summary</th><th scope="col">Reference</th></tr></thead>'
        '<tbody><tr><td colspan="5">Zero corrections logged at launch.</td></tr></tbody>'
        '</table></div></section></div>'
    )


def _not_found_page() -> str:
    return (
        '<div class="doc" data-protocol="v2">'
        '<div class="err-hero">'
        '<h1>404 - Page not found</h1>'
        '<p>This route does not match any released Hercyna benchmark page or artifact.</p>'
        '<p><a class="btn solid" href="index.html">Go to v2 Board</a> '
        '<a class="btn" href="chat.html">Chat 40-cell table</a> '
        '<a class="btn" href="v1.html">v1 archive</a></p>'
        '</div></div>'
    )


def _apply_v2_prefix(html_text: str) -> str:
    """Adapt root-relative asset and cross-page links inside `/v2/` permalinks."""
    root_targets = (
        "assets/",
        "fonts/",
        "media/",
        "packs/",
        "badge/",
        "data/",
        "og/",
        "feed.xml",
        "favicon.ico",
        "favicon.svg",
        "favicon-32x32.png",
        "apple-touch-icon.png",
        "v1.html",
        "terms.html",
        "privacy.html",
        "legal.html",
        "why.html",
        "about.html",
    )

    def _sub_attr(m: re.Match[str]) -> str:
        attr, val = m.group(1), m.group(2)
        if any(val.startswith(t) for t in root_targets):
            return f'{attr}="../{val}"'
        return m.group(0)

    return re.sub(r'\b(href|src|poster)="([^"]+)"', _sub_attr, html_text)


def build(
    root: Path,
    out: Path,
    *,
    include_exploratory: bool = False,
    built_at: str | None = None,
    site_url: str | None = None,
    newsletter_action: str | None = None,
    analytics_src: str | None = None,
) -> dict:
    root, out = Path(root), Path(out)
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    resolved_site_url = _valid_site_url(site_url or os.environ.get("HERCYNA_SITE_URL"))
    resolved_newsletter = _valid_https_url(newsletter_action if newsletter_action is not None else os.environ.get("HERCYNA_NEWSLETTER_ACTION"))
    resolved_analytics = _valid_https_url(analytics_src if analytics_src is not None else os.environ.get("HERCYNA_ANALYTICS_SRC"))

    proto = _json(root / "protocol" / "v1.json") or {}
    proto_v2 = _json(root / "protocol" / "v2.json")
    vals = dict(_proto_values(proto), _version=proto.get("version") or "v1")
    sources = _sources(root, include_exploratory)
    MEMORY_COST.clear()
    MEMORY_COST.update(_memory_costs(sources))
    deciders = _entries(sources)
    verdict = _rule_l(deciders) if deciders else None
    media = {p.name for p in MEDIA_DIR.iterdir()} if MEDIA_DIR.is_dir() else set()
    cause_data = _load_v2_cause_split(root)
    chat_loaded = _load_v2_chat_table(root)
    v2_decider_ids = sorted(chat_loaded["deciders"]) if chat_loaded else None

    # Build externalized CSS + JS + hashed URLs (strict zero-inline CSP)
    css_text = _compact_css(CSS).replace('url("fonts/', 'url("../fonts/')
    head_js_text = HEAD_JS
    app_js_text = _compact_js(JS)
    css_bytes = css_text.encode("utf-8")
    head_bytes = head_js_text.encode("utf-8")
    app_bytes = app_js_text.encode("utf-8")
    asset_refs = {
        "css": f"assets/site.css?v={hashlib.sha256(css_bytes).hexdigest()[:8]}",
        "head": f"assets/head.js?v={hashlib.sha256(head_bytes).hexdigest()[:8]}",
        "app": f"assets/app.js?v={hashlib.sha256(app_bytes).hexdigest()[:8]}",
    }

    if chat_loaded:
        index = (
            _hero_v2(chat_loaded, proto_v2, root=root)
            + _v2_kpis(chat_loaded)
            + _v2_ranked_pairs_section(chat_loaded, section_id="board", heading_id="board-h", more_link=True)
            + _v2_cause_split_section(root, chat_loaded, compact_link=True)
            + _coming_next_section()
            + _explainer_section(media)
            + _reproduce_section(v2=True, root=root, gate_id="terms-repro")
            + _limits_section(v2_only=True)
            + _how_v2(chat_loaded, proto_v2, media)
            + _chat_dev_anatomy(root, section_id="anatomy", heading_id="ana-h")
            + _method_submit(proto_v2 or proto, protocol="v2" if proto_v2 else "v1", decider_ids=v2_decider_ids)
        )
        index_tag = "v2"
        index_pv2 = proto_v2
    else:
        index = (
            _hero(vals, has_v2_chat=False, root=root)
            + _kpis(deciders, verdict, vals)
            + _board(sources, deciders, verdict, vals)
            + _v2_cause_split_section(root, None, compact_link=True)
            + _reproduce_section(v2=False, root=root, gate_id="terms-repro")
            + _limits_section(v2_only=False)
            + _how(deciders, vals, media)
            + _anatomy()
            + _method_submit(proto, protocol="v1")
        )
        index_tag = "v1"
        index_pv2 = None

    v2_page_tag = "v2" if proto_v2 else "v1"
    v2_page_pv2 = proto_v2 if proto_v2 else None
    coding_chip = (
        f"Protocol v2 · {str(proto_v2.get('protocol_sha256') or '')[:8]} · Coding held"
        if proto_v2
        else None
    )
    submit_cmds = '<h2>Commands</h2>' + "".join(_job(c, gate_id="terms-submit") for c in SUBMIT_COMMANDS)
    v1_html = _v1_archive_page(sources, deciders, verdict, vals, proto, media, root=root)

    common_kw = dict(
        asset_refs=asset_refs,
        site_url=resolved_site_url,
        newsletter_action=resolved_newsletter,
        analytics_src=resolved_analytics,
        root=root,
    )
    pages = {
        "index.html": _page(
            "Board",
            "index.html",
            index,
            proto,
            built_at,
            proto_v2=index_pv2,
            proto_tag=index_tag,
            show_seed=True,
            canonical_path="",
            og_image="og/index.png",
            description="Hercyna Protocol v2 decision benchmark: Graphiti vs Mem0 across 5 deciders and 300 multi-session chat cases.",
            **common_kw,
        ),
        "v1.html": _page(
            "v1 archive: account review",
            "v1.html",
            v1_html,
            proto,
            built_at,
            proto_v2=None,
            proto_tag="v1",
            show_seed=True,
            og_image="og/v1.png",
            description="Archived Hercyna Protocol v1 account-review benchmark board (table seed 1283645051).",
            **common_kw,
        ),
        "coding.html": _page(
            "Coding",
            "coding.html",
            _coding_page(),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            chip_note=coding_chip,
            og_image="og/coding.png",
            description="Hercyna Coding Agent track specification (Mode A blind-adapter and Mode B harness). Runs held.",
            **common_kw,
        ),
        "chat.html": _page(
            "Chat",
            "chat.html",
            _chat_page(root, chat_loaded),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=bool(proto_v2),
            og_image="og/chat.png",
            description="Full 40-cell Hercyna Chat Agent v2 table, Chow decision cost, and ACBI conflict hazard.",
            **common_kw,
        ),
        "method.html": _page(
            "Method",
            "method.html",
            _doc(
                "Method",
                _method_prose(root),
                _frozen_block(root, use_v2=bool(proto_v2), decider_ids=v2_decider_ids),
            ),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            og_image="og/method.png",
            description="Frozen benchmark methodology, floor measurement, paired-bootstrap separation rule, and citation metadata.",
            **common_kw,
        ),
        "submit.html": _page(
            "Submit",
            "submit.html",
            _doc("Submit", _copy_block(root, COPY["submit.html"]) + _submit_steps_html(root), submit_cmds),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            og_image="og/submit.png",
            description="Four-step blind-adapter submission guide for evaluating memory products under Hercyna.",
            **common_kw,
        ),
        "why.html": _page(
            "Why Hercyna",
            "why.html",
            _doc(
                "Why Hercyna",
                _copy_block(root, COPY["why"]) + '<p><a class="more" href="about.html">Who builds Hercyna</a></p>',
                _why_aside(),
            ),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Why Hercyna scores decisions rather than recall snippets.",
            **common_kw,
        ),
        "about.html": _page(
            "About",
            "about.html",
            _doc("About", _copy_block(root, COPY["about.html"]), _about_aside()),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Author and background notes for the Hercyna memory benchmark.",
            **common_kw,
        ),
        "terms.html": _page(
            "Terms of Use",
            "terms.html",
            _markdown_legal_page(root, "TERMS.md", "Terms of Use"),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Hercyna terms of use, experimental research disclaimer, trademark notice, and corrections link.",
            **common_kw,
        ),
        "legal.html": _page(
            "Legal Notice",
            "legal.html",
            _markdown_legal_page(root, "LEGAL.md", "Legal Notice"),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Apache-2.0 Sections 7-8 and CC BY 4.0 Section 5 warranty disclaimers and liability limits.",
            **common_kw,
        ),
        "privacy.html": _page(
            "Privacy Notice",
            "privacy.html",
            _markdown_legal_page(root, "PRIVACY.md", "Privacy Notice"),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Cookie-free site privacy, optional double-opt-in updates, and zero-PII CLI storage.",
            **common_kw,
        ),
        "changelog.html": _page(
            "Changelog",
            "changelog.html",
            _changelog_page(root),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            og_image="og/changelog.png",
            description="Release history and protocol changelog for Hercyna with Atom feed subscription.",
            **common_kw,
        ),
        "corrections.html": _page(
            "Corrections and Right of Reply",
            "corrections.html",
            _corrections_page(),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            og_image="og/corrections.png",
            description="Vendor correction, rerun, and right-of-reply procedure with public corrections log.",
            **common_kw,
        ),
        "404.html": _page(
            "Page Not Found",
            "404.html",
            _not_found_page(),
            proto,
            built_at,
            proto_v2=v2_page_pv2,
            proto_tag=v2_page_tag,
            show_seed=False,
            description="Page not found on Hercyna.",
            **common_kw,
        ),
    }
    product_pages: dict[str, str] = {}
    if chat_loaded:
        product_pages = _v2_product_pages(
            root,
            chat_loaded,
            cause_data,
            proto,
            proto_v2,
            built_at,
            site_url=resolved_site_url,
            asset_refs=asset_refs,
            newsletter_action=resolved_newsletter,
            analytics_src=resolved_analytics,
        )
        pages.update(product_pages)

    # /v2/ permalink mirrors for every v2 result & track page
    v2_ref_names = ["index.html", "chat.html", "coding.html", "method.html", "submit.html", "changelog.html", "corrections.html", *sorted(product_pages)]
    v2_refs = {k: f"../{v}" for k, v in asset_refs.items()}
    v2_pages: dict[str, str] = {}
    for pname in v2_ref_names:
        raw_html = pages[pname]
        rewritten = _apply_v2_prefix(raw_html)
        for k, v in asset_refs.items():
            rewritten = rewritten.replace(f'"{v}"', f'"{v2_refs[k]}"')
        rewritten = rewritten.replace(
            f'<link rel="canonical" href="{resolved_site_url.rstrip("/")}/{"" if pname == "index.html" else pname}">',
            f'<link rel="canonical" href="{resolved_site_url.rstrip("/")}/v2/{"" if pname == "index.html" else pname}">',
        )
        rewritten = rewritten.replace(
            f'<meta property="og:url" content="{resolved_site_url.rstrip("/")}/{"" if pname == "index.html" else pname}">',
            f'<meta property="og:url" content="{resolved_site_url.rstrip("/")}/v2/{"" if pname == "index.html" else pname}">',
        )
        v2_pages[f"v2/{pname}"] = rewritten

    out.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, int] = {}

    def _write_blob(rel: str, payload: bytes) -> None:
        fp = out / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(payload)
        sizes[rel] = len(payload)

    _write_blob("assets/site.css", css_bytes)
    _write_blob("assets/head.js", head_bytes)
    _write_blob("assets/app.js", app_bytes)

    for name, text in {**pages, **v2_pages}.items():
        _write_blob(name, text.encode("utf-8"))

    # Favicons (ICO, PNG, Apple touch, SVG)
    png32 = assets_gen.render_icon_png(32)
    _write_blob("favicon-32x32.png", png32)
    _write_blob("favicon.ico", assets_gen.png_to_ico(png32, 32))
    _write_blob("apple-touch-icon.png", assets_gen.render_icon_png(180))
    icon_svg_src = BRAND_DIR / "hercyna-icon.svg"
    if icon_svg_src.is_file():
        _write_blob("favicon.svg", icon_svg_src.read_bytes())

    # Open Graph / Twitter share cards (1200x630 PNG + SVG)
    sha8 = str((proto_v2 or {}).get("protocol_sha256") or "68af8d8e")[:8]
    og_specs = (
        ("index", "HERCYNA CHAT BENCHMARK V2", "Graphiti below Mem0 on wrong action mainly by withholding across 5 deciders and 300 cases", f"PROTOCOL V2 - {sha8}"),
        ("chat", "CHAT AGENT 40-CELL TABLE", "Wrong action, withheld action, right action, and left-out ingest errors side by side", f"PROTOCOL V2 - {sha8}"),
        ("v1", "V1 ARCHIVE: ACCOUNT REVIEW", "Single-session account-review benchmark across 2 deciders and 300 cases", "PROTOCOL V1 - 99494597"),
        ("coding", "CODING AGENT TRACK", "Blind-adapter and harness evaluations in preparation (runs held)", f"PROTOCOL V2 - {sha8}"),
        ("method", "HERCYNA METHODOLOGY", "Decision-scored memory evaluation with full-history noise floors and paired bootstrap", f"PROTOCOL V2 - {sha8}"),
        ("submit", "SUBMIT A MEMORY ADAPTER", "Four-method blind adapter tested against frozen synthetic histories", f"PROTOCOL V2 - {sha8}"),
        ("changelog", "HERCYNA RELEASE NOTES", "Protocol milestones, ledger audits, and static launch releases", f"PROTOCOL V2 - {sha8}"),
        ("corrections", "CORRECTIONS AND RIGHT OF REPLY", "Public vendor correction workflow and transparent audit log", f"PROTOCOL V2 - {sha8}"),
        ("default", "HERCYNA MEMORY BENCHMARK", "Which memory makes your agent act wrong? Wrong, withheld, and right side by side", f"PROTOCOL V2 - {sha8}"),
    )
    for slug_og, og_t, og_f, og_tag in og_specs:
        _write_blob(f"og/{slug_og}.png", assets_gen.render_og_png(og_t, og_f, og_tag))
        _write_blob(f"og/{slug_og}.svg", assets_gen.render_og_svg(og_t, og_f, og_tag).encode("utf-8"))

    # Explainer poster + captions
    _write_blob("media/explainer-poster.svg", assets_gen.render_explainer_poster_svg().encode("utf-8"))
    _write_blob("media/explainer.vtt", assets_gen.EXPLAINER_VTT.encode("utf-8"))

    # Neutral product badges + downloadable CSV/JSON
    if chat_loaded:
        seen_sys = {r["system"] for slot in chat_loaded["deciders"].values() for r in slot["rows"]}
        for sys_name in sorted(seen_sys):
            disp = DISPLAY.get(sys_name, sys_name)
            bsvg = assets_gen.render_neutral_badge_svg(disp, "v2", sha8)
            _write_blob(f"badge/{_slug(sys_name)}.svg", bsvg.encode("utf-8"))
        for rel_d, blob_d in assets_gen.build_chat_downloads(chat_loaded, sha8).items():
            _write_blob(rel_d, blob_d)

    # Atom feed + robots.txt + sitemap.xml + .well-known/security.txt
    ch_entries = assets_gen.parse_changelog_entries(root / "CHANGELOG.md")
    _write_blob("feed.xml", assets_gen.build_atom_feed(ch_entries, resolved_site_url, built_at).encode("utf-8"))
    all_urls = [*pages.keys(), *[f"v2/{p}" for p in v2_ref_names]]
    for rel_w, blob_w in assets_gen.build_well_known_files(resolved_site_url, all_urls, built_at).items():
        _write_blob(rel_w, blob_w)

    sizes.update(_copy_packs(root, out, sources, chat_loaded, proto_v2))
    sizes.update(_fonts(out))
    sizes.update(_media(out))
    return {"out": str(out), "bytes": sizes, "sources": [s["id"] for s in sources]}
