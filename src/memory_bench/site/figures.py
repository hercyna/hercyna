"""Drawings for the site, written as inline SVG from the board data: the 2D frontier, the audit whiskers and
the stage stepper. The case anatomy is rendered here too, from one public development case.

Nothing here computes a rate. Positions are the printed values scaled to the drawing.
"""

from __future__ import annotations

import html
import math
from typing import Any

E = html.escape


# ---------------------------------------------------------------- scales

def ticks(a: float, z: float, n: int = 5) -> list[float]:
    """Round tick values covering [a, z], about n of them."""
    span = max(z - a, 1e-9)
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t = math.ceil(a / step - 1e-9) * step
    out = []
    while t <= z + 1e-9:
        out.append(round(t, 6))
        t += step
    return out


def _fmt_tick(t: float) -> str:
    if abs(t) < 1e-9:
        return "0"
    s = f"{t:+.1f}" if abs(t - round(t)) > 1e-9 else f"{t:+.0f}"
    return s


def _domain(vals: list[float]) -> tuple[float, float]:
    a, z = min(vals + [0.0]), max(vals + [0.0])
    pad = max(z - a, 4.0) * 0.07
    return a - pad, z + pad


def _n(x: float) -> str:
    return f"{x:.1f}"


# ---------------------------------------------------------------- frontier

def frontier_svg(rows: list[dict], did: str) -> str:
    """x = withheld above floor, y = excess wrong action, both in points; the floor sits at (0, 0).
    One <g class="pt"> per row that has both values, keyed by data-r to its board row and audit."""
    pts = [(j, r) for j, r in enumerate(rows) if r.get("ex") and r.get("ha")]
    W, H, ml, mr, mt, mb = 400, 300, 46, 18, 14, 46
    xs, ys = [], []
    for _, r in pts:
        xs += [v for v in (r["ha"]["v"], r["ha"]["lo"], r["ha"]["hi"]) if v is not None]
        ys += [v for v in (r["ex"]["v"], r["ex"]["lo"], r["ex"]["hi"]) if v is not None]
    xa, xz = _domain(xs)
    ya, yz = _domain(ys)

    def X(v: float) -> float:
        return ml + (v - xa) / (xz - xa) * (W - ml - mr)

    def Y(v: float) -> float:
        return H - mb - (v - ya) / (yz - ya) * (H - mt - mb)

    out = [f'<svg class="frontier" viewBox="0 0 {W} {H}" role="group" aria-labelledby="ft-{E(did)}">'
           f'<title id="ft-{E(did)}">Frontier for {E(did)}: excess wrong action against withheld action above '
           'the floor, in points. The floor is at zero on both axes; lower and further left is better.</title>']
    for t in ticks(xa, xz):
        out.append(f'<line class="grid" x1="{_n(X(t))}" x2="{_n(X(t))}" y1="{mt}" y2="{H - mb}"/>'
                   f'<text x="{_n(X(t))}" y="{H - mb + 14}" text-anchor="middle">{_fmt_tick(t)}</text>')
    for t in ticks(ya, yz):
        out.append(f'<line class="grid" x1="{ml}" x2="{W - mr}" y1="{_n(Y(t))}" y2="{_n(Y(t))}"/>'
                   f'<text x="{ml - 6}" y="{_n(Y(t) + 3.5)}" text-anchor="end">{_fmt_tick(t)}</text>')
    out.append(f'<line class="axis" x1="{ml}" x2="{W - mr}" y1="{H - mb}" y2="{H - mb}"/>'
               f'<line class="axis" x1="{ml}" x2="{ml}" y1="{mt}" y2="{H - mb}"/>'
               f'<line class="zero" x1="{_n(X(0))}" x2="{_n(X(0))}" y1="{mt}" y2="{H - mb}"/>'
               f'<line class="zero" x1="{ml}" x2="{W - mr}" y1="{_n(Y(0))}" y2="{_n(Y(0))}"/>'
               f'<text class="t-ax" x="{(ml + W - mr) / 2:.1f}" y="{H - 8}" text-anchor="middle">'
               'Withheld above floor, points</text>'
               f'<text class="t-ax" transform="translate(12 {(mt + H - mb) / 2:.1f}) rotate(-90)" text-anchor="middle">'
               'Excess wrong action, points</text>'
               f'<g class="floor" aria-hidden="true"><rect x="{_n(X(0) - 4)}" y="{_n(Y(0) - 4)}" width="8" height="8"/>'
               f'<text x="{_n(X(0) + 8)}" y="{_n(Y(0) + 14)}">floor</text></g>')
    placed: list[tuple[float, float, float, float]] = [(X(0), Y(0) + 4, X(0) + 40, Y(0) + 16)]

    def free(box: tuple[float, float, float, float]) -> bool:
        a1, b1, a2, b2 = box
        if a1 < ml or a2 > W - 2 or b1 < 2 or b2 > H - mb - 2:
            return False
        return all(a2 < c1 or a1 > c2 or b2 < d1 or b1 > d2 for c1, d1, c2, d2 in placed)

    for j, r in pts:
        ex, ha = r["ex"], r["ha"]
        cx, cy = X(ha["v"]), Y(ex["v"])
        name = r["system"]
        w = 6.4 * len(name) + 2
        label = (cx + 9, cy + 3.5, "start")
        for dx, dy, anchor in ((7, -6, "start"), (-7, -6, "end"), (7, 14, "start"), (-7, 14, "end"),
                               (9, 3.5, "start"), (-9, 3.5, "end"), (0, -10, "middle"), (0, 17, "middle")):
            x0 = cx + dx - (w if anchor == "end" else w / 2 if anchor == "middle" else 0)
            box = (x0, cy + dy - 9, x0 + w, cy + dy + 2)
            if free(box):
                label = (cx + dx, cy + dy, anchor)
                placed.append(box)
                break
        aria = (f'{name}: withheld above floor {ha["t"]}, excess wrong action {ex["t"]}')
        whisk = ""
        if ha["lo"] is not None and ha["hi"] is not None:
            whisk += f'<line class="ci" x1="{_n(X(ha["lo"]))}" x2="{_n(X(ha["hi"]))}" y1="{_n(cy)}" y2="{_n(cy)}"/>'
        if ex["lo"] is not None and ex["hi"] is not None:
            whisk += f'<line class="ci" x1="{_n(cx)}" x2="{_n(cx)}" y1="{_n(Y(ex["lo"]))}" y2="{_n(Y(ex["hi"]))}"/>'
        out.append(f'<g class="pt" data-r="{j}" data-base="{1 if r["base"] else 0}" tabindex="0" role="button" '
                   f'aria-label="{E(aria)}">{whisk}<circle cx="{_n(cx)}" cy="{_n(cy)}" r="4.8"/>'
                   f'<text x="{_n(label[0])}" y="{_n(label[1])}" text-anchor="{label[2]}">{E(name)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def whisker_svg(ex: dict | None, ha: dict | None) -> str:
    """The two intervals of one row against the floor at 0, on one scale."""
    vals = [v for p in (ex, ha) if p for v in (p["v"], p["lo"], p["hi"]) if v is not None]
    if not vals:
        return ""
    a, z = _domain(vals)
    W, ml, mr = 340, 62, 12

    def X(v: float) -> float:
        return ml + (v - a) / (z - a) * (W - ml - mr)

    out = [f'<svg class="whisk" viewBox="0 0 {W} 92" aria-hidden="true" focusable="false">']
    for t in ticks(a, z, 4):
        out.append(f'<text x="{_n(X(t))}" y="88" text-anchor="middle">{_fmt_tick(t)}</text>')
    out.append(f'<line x1="{ml}" x2="{W - mr}" y1="74" y2="74" stroke="var(--line)"/>'
               f'<line x1="{_n(X(0))}" x2="{_n(X(0))}" y1="6" y2="74" stroke="var(--ink-2)" stroke-dasharray="3 3"/>')
    for p, y, cls, name in ((ex, 28, "ex", "wrong"), (ha, 58, "ha", "withheld")):
        if not p:
            continue
        lo = p["lo"] if p["lo"] is not None else p["v"]
        hi = p["hi"] if p["hi"] is not None else p["v"]
        tx = X(p["v"])
        anchor = "end" if tx > W - 70 else "start" if tx < ml + 40 else "middle"
        out.append(f'<text x="0" y="{y + 3.5}">{name}</text>'
                   f'<line class="{cls}" x1="{_n(X(lo))}" x2="{_n(X(hi))}" y1="{y}" y2="{y}" stroke-width="2.2"/>'
                   f'<circle class="{cls}" cx="{_n(tx)}" cy="{y}" r="4.5"/>'
                   f'<text class="lb {cls}" x="{_n(tx)}" y="{y - 9}" text-anchor="{anchor}">{E(p["t"])}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- stage stepper

def flow_svg(deciders: list[tuple[str, str]], budget: Any) -> str:
    """The five stages as a line drawing. deciders = [(id, detail line)]; at most two are drawn."""
    b = f"{budget} tokens" if budget is not None else "a fixed budget"
    decs = (deciders + [("decider", "")] * 2)[:2] if deciders else [("decider", ""), ("decider", "")]

    def node(cls: str, x: int, y: int, w: int, h: int, tag: str, title: str, lines: list[str]) -> str:
        t = "".join(f'<text class="dt" x="{x + 14}" y="{y + 64 + i * 17}">{E(s)}</text>' for i, s in enumerate(lines) if s)
        return (f'<g class="node {cls}"><rect x="{x}" y="{y}" width="{w}" height="{h}"/>'
                f'<rect class="bar-top" x="{x}" y="{y}" width="{w}" height="3"/>'
                f'<text class="tag" x="{x + 14}" y="{y + 22}">{E(tag)}</text>'
                f'<text class="ttl" x="{x + 14}" y="{y + 44}">{E(title)}</text>{t}</g>')

    wires = [("w0", "M175 125 C 195 125, 195 125, 215 125"), ("w1", "M375 125 C 395 125, 395 125, 415 125"),
             ("w2", "M575 125 C 600 125, 590 72, 615 72"), ("w2", "M575 125 C 600 125, 590 182, 615 182"),
             ("w3", "M785 72 C 805 72, 800 112, 820 112"), ("w3", "M785 182 C 805 182, 800 140, 820 140")]
    paths = "".join(f'<path class="wire {c}" d="{d}"/>' for c, d in wires)
    pulses = "".join(f'<rect class="pulse p{c[1]}" x="-3.5" y="-3.5" width="7" height="7">'
                     f'<animateMotion dur="1.6s" repeatCount="indefinite" path="{d}"/></rect>' for c, d in wires)
    lethe = "".join(f'<circle cx="{280 + (k * 37) % 46}" cy="{186 + (k * 11) % 9}" r="{2.4 - k * .15:.2f}" '
                    f'style="animation-delay:{k * .3:.1f}s"/>' for k in range(8))
    return ('<svg class="flow" data-s="0" viewBox="0 0 1000 250" role="img" aria-labelledby="flow-t">'
            '<title id="flow-t">The five stages: history, memory, view, two deciders, action.</title>'
            + paths
            + node("n0", 10, 55, 165, 140, "01 HISTORY", "Dated records", ["100 or 400 per case", "a rule oracle knows", "the right action"])
            + node("n1", 215, 70, 160, 110, "02 MEMORY", "Blind adapter", ["reset, ingest, view", f"at most {b}"])
            + node("n2", 415, 70, 160, 110, "03 VIEW", "What the agent sees", ["only the returned view", "never the oracle"])
            + node("n3", 615, 25, 170, 95, "04 DECIDER", decs[0][0], [decs[0][1]])
            + node("n3", 615, 135, 170, 95, "04 DECIDER", decs[1][0], [decs[1][1]])
            + node("n4", 820, 55, 170, 140, "05 ACTION", "Scored", ["right, withheld or wrong", "measured above", "the decider's floor"])
            + f'<g class="lethe" aria-hidden="true">{lethe}</g>' + pulses + "</svg>")


# ---------------------------------------------------------------- case anatomy

def _rec_text(e: Any) -> str:
    kind = e.type + (f" {e.subtype}" if e.subtype else "")
    fields = " · ".join(f"{k} {v}" for k, v in e.fields.items())
    return f'<b>{E(e.date)}</b> · {E(kind)} · {E(fields)} <span class="mono">({E(e.id)})</span>'


def anatomy_html(case: Any, reduced: str, n_cases: int) -> str:
    """Three columns: the request and the oracle's right action, the records that decide it, and what the
    oracle says on the same history without the decisive records. `reduced` is that oracle action."""
    from ..cases.schema import REQUEST_INFO

    keys = set(case.d_star)
    stmts = [e for e in case.events if e.type == "statement" and e.fields.get("period") == case.query_period]
    shown = sorted({e.id: e for e in [e for e in case.events if e.id in keys] + stmts}.values(),
                   key=lambda e: (e.date, e.id))
    recs = "".join(f'<p class="rec{" key" if e.id in keys else ""}">{_rec_text(e)}'
                   + ('<span class="why-k">decides the case</span>' if e.id in keys else "") + "</p>" for e in shown)
    ids = ", ".join(sorted(keys)) or "none"
    q = case.query_text.split(" Choose one action")[0]
    if reduced == case.a_star:
        cls, what = "right", "The action does not change without these records."
    elif reduced == REQUEST_INFO:
        cls, what = "held", ("An agent that asks for more information here withholds action. "
                             "The record was in the history, so the bench counts it as withheld.")
    else:
        cls, what = "wrong", f"An agent that acts on this view picks {reduced}, a wrong action."
    dropped = "".join(f'<p class="rec drop">{_rec_text(e)}</p>' for e in shown if e.id in keys)
    return (
        '<div class="ana">'
        '<div class="ana-col"><h3>The request</h3>'
        f'<p class="q">{E(q)}</p>'
        f'<p>The agent picks one of five actions: {E(", ".join(case.actions))}.</p>'
        f'<p>Right action on the full history</p><span class="act right">{E(case.a_star)}</span>'
        f'<p>The rule oracle reads all {len(case.events)} records.</p></div>'
        '<div class="ana-col"><h3>The records that decide it</h3>'
        f'<p>Of {len(case.events)} records, these settle the request for period {E(case.query_period)}.</p>'
        f'{recs}</div>'
        '<div class="ana-col"><h3>A view that drops the decisive record</h3>'
        f'{dropped}<p>The rule oracle on the same history without {E(ids)}:</p>'
        f'<span class="act {cls}">{E(reduced)}</span><p>{E(what)}</p></div>'
        '</div>'
        f'<p class="ana-foot">Synthetic development case {E(case.case_id)}, pattern {E(case.mechanism)}, '
        f'one of the {n_cases} public development cases. No table case is shown.</p>')
