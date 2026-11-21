"""The public site's look: the Observatory (Varma's prototype 06) in light, prototype 05's palette in dark.

The dark tokens are
written twice: once under prefers-color-scheme (unless the visitor chose light) and once for an explicit
dark choice from the header toggle.
"""

FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;'
             '1,6..72,400&family=IBM+Plex+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700'
             '&display=swap" rel="stylesheet">')

LIGHT = {
    "bg": "#f7f6f2", "paper": "#efece5", "surface": "#ffffff", "shape": "#e7e3da", "hover": "#ebe8e0",
    "ink": "#121716", "ink-2": "#4b5653", "ink-3": "#5f6763", "line": "#d6d2c8", "line-2": "#e6e2d9",
    "teal": "#0e6f76", "teal-soft": "#e1f0f1", "red": "#d8252b", "verdict": "#c21f25", "verdict-soft": "#fbe6e6",
    "rust": "#b2462b", "rust-soft": "#f8e8e2", "slate": "#c7c4bd", "slate-hatch": "#858179",
    "amber": "#8d5807", "amber-soft": "#f8efdc", "sel": "#bfe0e2", "on-teal": "#ffffff", "h-accent": "#0e6f76",
}
DARK = {
    "bg": "#0c1117", "paper": "#101720", "surface": "#161f2b", "shape": "#1d2938", "hover": "#233245",
    "ink": "#f4f8fa", "ink-2": "#cad7df", "ink-3": "#96abb8", "line": "#2c3c4f", "line-2": "#1f2c3b",
    "teal": "#4be3eb", "teal-soft": "rgba(75, 227, 235, .14)", "red": "#ff7b5c", "verdict": "#f8c45e",
    "verdict-soft": "rgba(248, 196, 94, .12)", "rust": "#ff7b5c", "rust-soft": "rgba(255, 123, 92, .15)",
    "slate": "#5c7180", "slate-hatch": "#94aab8", "amber": "#f8c45e", "amber-soft": "rgba(248, 196, 94, .14)",
    "sel": "#245a62", "on-teal": "#061518", "h-accent": "#4be3eb",
}


def _vars(d: dict) -> str:
    return " ".join(f"--{k}: {v};" for k, v in d.items())


TOKENS = (":root { color-scheme: light; " + _vars(LIGHT) + " }\n"
          "@media (prefers-color-scheme: dark) { :root:not([data-theme=\"light\"]) { color-scheme: dark; "
          + _vars(DARK) + " } }\n"
          ":root[data-theme=\"dark\"] { color-scheme: dark; " + _vars(DARK) + " }\n")

CSS = TOKENS + r"""
:root {
  --serif: "Newsreader", Georgia, "Times New Roman", serif;
  --sans: "IBM Plex Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --ease: cubic-bezier(.16, 1, .3, 1);
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; background: var(--bg); color: var(--ink); scrollbar-color: var(--line) var(--bg); }
body { margin: 0; background: var(--bg); color: var(--ink); font: 400 15.5px/1.55 var(--sans);
  font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; caret-color: var(--teal); }
::selection { background: var(--sel); color: var(--ink); }
::-webkit-scrollbar { width: 11px; height: 9px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: var(--ink-3); }
/* no global smooth scroll: the stepper's layout changes cancel it, so anchor links went nowhere (2026-11-21) */
a { color: var(--teal); text-decoration: none; text-underline-offset: 3px; text-decoration-thickness: 1px; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--teal); outline-offset: 2px; }
code, .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
code { font-size: .92em; }
.vh { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
html:not(.js) .js-only { display: none !important; }
.js .nojs-only { display: none !important; }
.skip { position: absolute; left: 8px; top: -60px; z-index: 50; background: var(--ink); color: var(--bg); padding: 8px 12px; font: 600 13px/1 var(--mono); }
.skip:focus { top: 8px; }

/* shell: one left rail, no duplicate top nav */
.shell { display: grid; grid-template-columns: 224px minmax(0, 1fr); min-height: 100vh; }
.rail { background: var(--paper); border-right: 1px solid var(--line); position: sticky; top: 0; height: 100vh;
  display: flex; flex-direction: column; padding: 16px 12px; z-index: 30; }
.rail-brand { display: flex; align-items: center; gap: 10px; padding: 2px 4px 16px; border-bottom: 1px solid var(--ink);
  margin-bottom: 16px; color: var(--ink); }
.rail-brand:hover { text-decoration: none; }
.rail-brand .mark { width: 30px; height: 30px; flex: none; color: var(--ink); }
.rail-brand b { display: block; font: 700 13px/1.1 var(--sans); letter-spacing: .16em; text-transform: uppercase; }
.rail-brand small { display: block; font: 500 11px/1 var(--mono); letter-spacing: 0; white-space: nowrap; color: var(--teal); margin-top: 4px; }
.rail-nav { display: grid; gap: 2px; }
.rail-nav a { display: block; padding: 9px 10px; color: var(--ink-2); font: 600 13.5px/1.2 var(--sans);
  border-left: 2px solid transparent; transition: background .14s, color .14s; }
.rail-nav a:hover { background: var(--shape); color: var(--ink); text-decoration: none; }
.rail-nav a.on, .rail-nav a[aria-current] { background: var(--shape); color: var(--ink); border-left-color: var(--red); }
.rail-foot { margin-top: auto; padding-top: 14px; border-top: 1px solid var(--line); font: 400 11.5px/1.5 var(--mono); color: var(--ink-3); display: grid; gap: 6px; }

/* utility ribbon */
.main-col { min-width: 0; }
.ribbon { position: sticky; top: 0; z-index: 25; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 9px clamp(18px, 3vw, 40px); display: flex; align-items: center; justify-content: space-between; gap: 10px 14px; flex-wrap: wrap; }
.ribbon-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font: 500 11.5px/1 var(--mono); color: var(--ink-2); }
.chip { display: inline-flex; align-items: center; gap: 7px; padding: 5px 8px; background: var(--shape); border: 1px solid var(--line); color: var(--ink); font-weight: 600; }
.chip i { width: 6px; height: 6px; border-radius: 50%; background: var(--teal); display: block; }
.ribbon-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.btn { appearance: none; border-radius: 0; border: 1px solid var(--ink); background: var(--bg); color: var(--ink);
  font: 600 12px/1 var(--mono); letter-spacing: .02em; padding: 8px 12px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 7px; white-space: nowrap; transition: background .14s, color .14s, border-color .14s; }
.btn:hover { background: var(--shape); text-decoration: none; }
.btn.solid { background: var(--ink); color: var(--bg); }
.btn.solid:hover { background: var(--teal); border-color: var(--teal); color: var(--on-teal); }
.btn svg { width: 13px; height: 13px; flex: none; }
.btn.done { background: var(--teal); border-color: var(--teal); color: var(--on-teal); }
.copy .i-ok, .copy.done .i-copy { display: none; }
.copy.done .i-ok { display: block; }

.wrap { max-width: 1320px; margin: 0 auto; padding: 0 clamp(18px, 3vw, 40px) 64px; }

/* hero */
.hero { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, .9fr); gap: 24px 48px; align-items: end;
  padding: 34px 0 28px; border-bottom: 1.5px solid var(--ink); }
.hero h1 { font: 400 clamp(2.3rem, 4.4vw, 3.6rem)/1.03 var(--serif); letter-spacing: -.025em; margin: 0 0 16px; max-width: 17ch; text-wrap: balance; }
.hero h1 em { font-style: italic; color: var(--red); }
.hero-lead { margin: 0; font-size: 16.5px; color: var(--ink-2); max-width: 56ch; line-height: 1.58; }
.hero-right { display: grid; gap: 10px; }
.hero-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 0; }
.hero-meta > div { background: var(--paper); padding: 9px 11px; }
.hero-meta dd { margin: 0; font: 700 13px/1.2 var(--mono); color: var(--ink); }
.hero-meta dt { font: 400 11px/1.3 var(--mono); color: var(--ink-3); margin-top: 3px; }
.hero-meta > div { display: flex; flex-direction: column-reverse; justify-content: flex-end; }
.cli-box { background: var(--surface); border: 1px solid var(--ink); padding: 14px 16px; }
.cli-top { display: flex; justify-content: space-between; gap: 10px; font: 600 11px/1.2 var(--mono); color: var(--ink-3); margin: 0 0 9px; }
.cli-cmd { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: var(--paper);
  border: 1px solid var(--line); padding: 8px 8px 8px 11px; font: 500 13px/1.3 var(--mono); color: var(--ink); margin: 0; }
.cli-cmd code { overflow-x: auto; white-space: nowrap; font-size: 13px; }
.cli-cmd .btn { padding: 6px 9px; }
.cli-note { margin: 9px 0 0; font-size: 13px; color: var(--ink-2); line-height: 1.45; }

/* stat strip */
.kpis { display: grid; grid-template-columns: repeat(var(--n, 4), minmax(0, 1fr)); border-bottom: 1px solid var(--line); margin: 0; }
.kpis > div { padding: 13px 16px; border-right: 1px solid var(--line-2); }
.kpis > div:first-child { padding-left: 0; }
.kpis > div:last-child { border-right: 0; padding-right: 0; }
.kpis dt { font: 600 11px/1.2 var(--mono); color: var(--ink-3); margin-bottom: 7px; }
.kpis dd { margin: 0; }
.kpis .v { display: block; font: 700 18px/1.15 var(--mono); color: var(--ink); }
.kpis .v.stop { color: var(--verdict); }
.kpis .v.teal { color: var(--teal); }
.kpis small { display: block; font: 400 12.5px/1.4 var(--sans); color: var(--ink-2); margin-top: 4px; }

/* sections */
.sec { padding-top: 40px; scroll-margin-top: 56px; }
.sec-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 20px;
  border-bottom: 1.5px solid var(--ink); padding-bottom: 9px; margin-bottom: 16px; }
.sec-head h2 { margin: 0; font: 500 26px/1.15 var(--serif); letter-spacing: -.01em; }
.sec-head .sub { font: 400 12px/1.4 var(--mono); color: var(--ink-2); max-width: 70ch; }

/* board */
.tablist { display: contents; }
.tab-rail { display: flex; flex-wrap: wrap; border-bottom: 1px solid var(--line); margin-bottom: 12px; align-items: stretch; }
.tab { appearance: none; background: transparent; border: 0; border-bottom: 2px solid transparent; padding: 9px 15px 10px;
  margin-bottom: -1px; cursor: pointer; text-align: left; font: 600 13.5px/1.2 var(--sans); color: var(--ink-2); }
.tab:hover { color: var(--ink); background: var(--shape); }
.tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--ink); }
.tab span { display: block; font: 400 11px/1.2 var(--mono); color: var(--ink-3); margin-top: 3px; }
.tab-tools { margin-left: auto; display: inline-flex; align-items: center; gap: 7px; font: 500 12px/1 var(--mono); color: var(--ink-2); cursor: pointer; padding: 6px 0; }
.tab-tools input { accent-color: var(--teal); width: 15px; height: 15px; margin: 0; }
.legend-line { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; font: 400 11.5px/1.2 var(--mono); color: var(--ink-2); margin-bottom: 8px; }
.leg-grp { display: flex; gap: 14px; flex-wrap: wrap; }
.leg { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 10px; height: 10px; border: 1px solid var(--ink); display: inline-block; }
.sw.r, .bar .p-r { background: var(--teal); }
.sw.h, .bar .p-h { background-color: var(--slate); background-image: repeating-linear-gradient(135deg, transparent 0 2.5px, var(--slate-hatch) 2.5px 4px); }
.sw.w, .bar .p-w { background: var(--rust); }
.dhead { font: 600 15px/1.3 var(--sans); margin: 18px 0 8px; }
.dhead span { font: 400 12px/1 var(--mono); color: var(--ink-3); }
.obs-split { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(340px, 1fr); gap: 22px; align-items: start; }
.board-col { min-width: 0; overflow-x: auto; }

table.tbl { width: 100%; border-collapse: collapse; border-top: 1px solid var(--ink); }
.tbl th { text-align: left; font: 600 11px/1.3 var(--mono); letter-spacing: .04em; color: var(--ink-3); padding: 8px; border-bottom: 1px solid var(--line); vertical-align: bottom; }
.tbl th.r, .tbl td.r { text-align: right; }
.tbl td { padding: 12px 8px; border-bottom: 1px solid var(--line-2); vertical-align: middle; transition: background .12s; }
.tbl tbody tr.row { cursor: pointer; }
.tbl tbody tr.row:hover td { background: var(--hover); }
.tbl tbody tr.row.active td { background: var(--teal-soft); }
.tbl tbody tr.row.active td:first-child { box-shadow: inset 2px 0 0 var(--teal); }
.tbl td.empty { padding: 26px 8px; color: var(--ink-2); font: 400 13px/1.5 var(--mono); }
.sys-t { display: flex; align-items: center; gap: 7px; }
.sys { font: 700 15px/1.2 var(--sans); color: var(--ink); }
.sys[role="button"]:focus-visible { outline-offset: 3px; }
.rank { display: inline-grid; place-items: center; min-width: 20px; height: 20px; padding: 0 4px; background: var(--ink); color: var(--bg); font: 700 11px/1 var(--mono); }
.sys-m { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; font: 400 11.5px/1.3 var(--mono); color: var(--ink-3); margin-top: 4px; }
.lab { font: 600 11px/1 var(--mono); padding: 3px 5px; border: 1px solid var(--line); color: var(--ink-2); background: var(--paper); }
.lab.x { border: 1px dashed var(--amber); color: var(--amber); background: var(--amber-soft); }
.bar { display: flex; height: 11px; border: 1px solid var(--ink); background: var(--bg); overflow: hidden; min-width: 150px; }
.bar i + i { border-left: 1px solid var(--bg); }
.bar-nums { display: flex; justify-content: space-between; gap: 6px; margin-top: 5px; font: 500 11px/1.2 var(--mono); color: var(--ink-2); }
.bar-nums .r { color: var(--teal); }
.bar-nums .w { color: var(--rust); font-weight: 700; }
.num b { display: block; font: 700 13.5px/1.2 var(--mono); color: var(--ink); white-space: nowrap; }
.num small { display: block; font: 400 11px/1.3 var(--mono); color: var(--ink-3); margin-top: 2px; white-space: nowrap; }
.nojs-meta { font: 400 11.5px/1.45 var(--mono); color: var(--ink-2); margin-top: 6px; }
.order { font: 400 12px/1.4 var(--mono); color: var(--ink-3); margin: 10px 0 0; }

/* lens: frontier and run audit */
.lens { position: sticky; top: 64px; background: var(--paper); border: 1px solid var(--ink); }
.lens-sec { padding: 14px 16px 16px; }
.lens-sec + .lens-sec { border-top: 1px solid var(--ink); }
.lens h3 { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin: 0 0 8px; font: 600 14px/1.2 var(--sans); }
.lens h3 span { font: 500 11px/1 var(--mono); color: var(--ink-3); }
.frontier { width: 100%; height: auto; display: block; overflow: visible; }
.frontier text { font-family: var(--mono); font-size: 10px; fill: var(--ink-3); }
.frontier .grid { stroke: var(--line-2); }
.frontier .axis { stroke: var(--ink-3); }
.frontier .zero { stroke: var(--ink); stroke-dasharray: 3 3; }
.frontier .t-ax { fill: var(--ink-2); font-size: 10.5px; }
.frontier .floor rect { fill: var(--teal); }
.frontier .floor text { fill: var(--teal); font-weight: 700; }
.pt { cursor: pointer; }
.pt .ci { stroke: var(--ink-2); stroke-opacity: .4; stroke-width: 1.3; }
.pt circle { fill: var(--ink); stroke: var(--paper); stroke-width: 1.6; transition: r .2s var(--ease); }
.pt[data-base="1"] circle { fill: var(--paper); stroke: var(--ink-2); stroke-width: 1.6; }
.pt text { fill: var(--ink); font-size: 10px; font-weight: 500; }
.pt.active .ci { stroke: var(--teal); stroke-opacity: .95; stroke-width: 2.2; }
.pt.active circle { fill: var(--teal); stroke: var(--paper); r: 6.5px; }
.pt.active text { fill: var(--teal); font-weight: 700; }
.pt:focus-visible { outline: none; }
.pt:focus-visible circle { stroke: var(--teal); stroke-width: 3; }
.pt.off, .row.off { display: none; }
.lens-note { margin: 8px 0 0; font-size: 12.5px; color: var(--ink-2); line-height: 1.45; }
.audit:not(.on) { display: none; }
.a-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.a-head b { font: 700 15px/1.2 var(--sans); }
.whisk { width: 100%; height: auto; display: block; }
.whisk text { font-family: var(--mono); font-size: 10px; fill: var(--ink-3); }
.whisk .ex { stroke: var(--rust); fill: var(--rust); }
.whisk .ha { stroke: var(--ink-2); fill: var(--ink-2); }
.whisk .lb { font-weight: 700; }
.whisk .lb.ex { stroke: none; }
.whisk .lb.ha { stroke: none; fill: var(--ink); }
dl.dl-tight { margin: 10px 0 0; border-top: 1px solid var(--line); }
.dl-tight > div { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--line-2); font: 400 12px/1.4 var(--mono); }
.dl-tight dt { color: var(--ink-3); flex: none; }
.dl-tight dd { margin: 0; color: var(--ink); font-weight: 600; text-align: right; overflow-wrap: anywhere; }

/* verdict callout and the why-no-rank explainer */
.verdict { margin-top: 14px; background: var(--verdict-soft); border-left: 3px solid var(--verdict); }
.verdict.ok { background: var(--teal-soft); border-left-color: var(--teal); }
.verdict summary { list-style: none; cursor: pointer; padding: 11px 14px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 14px; font-size: 14px; }
.verdict summary::-webkit-details-marker { display: none; }
.verdict summary strong { font: 700 13px/1.3 var(--mono); color: var(--verdict); }
.verdict.ok summary strong { color: var(--teal); }
.verdict .v-t { flex: 1 1 32ch; color: var(--ink); }
.verdict .v-more { font: 600 12px/1.3 var(--mono); color: var(--teal); display: inline-flex; align-items: center; gap: 6px; }
.verdict .v-more svg { width: 11px; height: 11px; transition: transform .25s var(--ease); }
.verdict[open] .v-more svg { transform: rotate(90deg); }
.why { padding: 4px 14px 16px; display: grid; gap: 12px; max-width: 92ch; }
.why p { margin: 0; font-size: 14px; color: var(--ink-2); line-height: 1.55; }
.why p strong { color: var(--ink); }
.why h3 { margin: 6px 0 0; font: 600 13.5px/1.3 var(--sans); }
.why table { width: 100%; border-collapse: collapse; font: 400 12px/1.4 var(--mono); border-top: 1px solid var(--ink); }
.why th { text-align: left; font-weight: 600; font-size: 10.5px; color: var(--ink-3); padding: 7px 8px; border-bottom: 1px solid var(--line); }
.why td { padding: 8px; border-bottom: 1px solid var(--line-2); color: var(--ink); }
.why td.r, .why th.r { text-align: right; }
.why .yes { color: var(--teal); font-weight: 700; }
.floors-why { font: 400 12px/1.5 var(--mono); color: var(--ink-2); }

/* what the table shows: generated findings under the verdict */
.findings { margin-top: 14px; padding: 12px 14px 14px; border-left: 3px solid var(--ink); background: var(--surface); max-width: 92ch; }
.findings h3 { margin: 0 0 6px; font: 700 13px/1.3 var(--mono); }
.findings > p { margin: 0 0 8px; font-size: 13px; color: var(--ink-3); }
.findings ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.findings li p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--ink); }
.findings li p.mono { font: 400 12px/1.5 var(--mono); color: var(--ink-2); }

/* how it works studio */
.studio { border: 1px solid var(--ink); background: var(--surface); }
.studio-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 16px; background: var(--paper); border-bottom: 1px solid var(--line); }
.studio-tabs, .studio-ctl { display: flex; gap: 6px; flex-wrap: wrap; }
.studio-tabs .btn[aria-selected="true"] { background: var(--ink); color: var(--bg); }
.studio-stage { padding: 18px; }
.js .studio-stage[hidden] { display: none; }
.flow { width: 100%; height: auto; display: block; }
.flow .node rect { fill: var(--paper); stroke: var(--line); stroke-width: 1.2; transition: stroke .3s, fill .3s; }
.flow .node .bar-top { fill: var(--ink); stroke: none; }
.flow .node .tag { font: 700 11px var(--mono); fill: var(--ink-3); letter-spacing: .04em; }
.flow .node .ttl { font: 600 16px var(--sans); fill: var(--ink); }
.flow .node .dt { font: 400 11.5px var(--mono); fill: var(--ink-2); }
.flow .wire { fill: none; stroke: var(--line); stroke-width: 1.6; transition: stroke .3s; }
.flow .pulse { fill: var(--teal); opacity: 0; }
.flow .lethe circle { fill: var(--teal); opacity: 0; }
.flow[data-s="0"] .n0 rect, .flow[data-s="1"] .n1 rect, .flow[data-s="2"] .n2 rect, .flow[data-s="3"] .n3 rect, .flow[data-s="4"] .n4 rect { stroke: var(--teal); stroke-width: 2.2; fill: var(--teal-soft); }
.flow[data-s="0"] .n0 .bar-top, .flow[data-s="1"] .n1 .bar-top, .flow[data-s="2"] .n2 .bar-top, .flow[data-s="3"] .n3 .bar-top, .flow[data-s="4"] .n4 .bar-top { fill: var(--teal); }
.flow[data-s="0"] .w0, .flow[data-s="1"] .w1, .flow[data-s="2"] .w2, .flow[data-s="3"] .w3 { stroke: var(--teal); stroke-width: 2.2; }
@media (prefers-reduced-motion: no-preference) {
  .flow[data-s="0"] .p0, .flow[data-s="1"] .p1, .flow[data-s="2"] .p2, .flow[data-s="3"] .p3 { opacity: 1; }
  .flow[data-s="1"] .lethe circle, .flow[data-s="2"] .lethe circle { animation: lethe 2.4s linear infinite; }
}
@keyframes lethe { 0% { transform: translateY(0); opacity: .8; } 100% { transform: translateY(46px); opacity: 0; } }
.reel video, .reel img { width: 100%; display: block; border: 1px solid var(--line); aspect-ratio: 16 / 9; object-fit: cover; background: var(--paper); }
.reel .v-dark, .reel .i-dark { display: none; }
:root[data-theme="dark"] .reel .v-dark, :root[data-theme="dark"] .reel .i-dark { display: block; }
:root[data-theme="dark"] .reel .v-light, :root[data-theme="dark"] .reel .i-light { display: none; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .reel .v-dark, :root:not([data-theme="light"]) .reel .i-dark { display: block; }
  :root:not([data-theme="light"]) .reel .v-light, :root:not([data-theme="light"]) .reel .i-light { display: none; }
}
.reel img { display: none !important; }
@media (prefers-reduced-motion: reduce) {
  .reel video { display: none !important; }
  .reel img.i-light { display: block !important; }
  .reel img.i-dark { display: none !important; }
  :root[data-theme="dark"] .reel img.i-light { display: none !important; }
  :root[data-theme="dark"] .reel img.i-dark { display: block !important; }
}
@media (prefers-reduced-motion: reduce) and (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .reel img.i-light { display: none !important; }
  :root:not([data-theme="light"]) .reel img.i-dark { display: block !important; }
}
.reel-note { margin: 10px 0 0; font: 400 12px/1.45 var(--mono); color: var(--ink-3); }
.stage-strip { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); border-top: 1px solid var(--ink); background: var(--paper); list-style: none; margin: 0; padding: 0; }
.stage { padding: 13px 14px 15px; border-right: 1px solid var(--line); border-top: 2px solid transparent; margin-top: -1px; transition: background .2s, border-color .2s; }
.stage:last-child { border-right: 0; }
.js .stage { cursor: pointer; }
.js .stage:hover { background: var(--hover); }
.stage.on { border-top-color: var(--teal); background: var(--surface); }
.stage h3 { margin: 0 0 5px; font: 700 11.5px/1 var(--mono); letter-spacing: .08em; color: var(--teal); display: flex; gap: 8px; }
.stage h3 span { color: var(--ink-3); font-weight: 500; }
.stage p { margin: 0; font-size: 13px; color: var(--ink-2); line-height: 1.45; }

/* case anatomy */
.ana { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--ink); background: var(--surface); }
.ana-col { padding: 18px; border-right: 1px solid var(--line); min-width: 0; }
.ana-col:last-child { border-right: 0; }
.ana h3 { margin: 0 0 10px; font: 600 15px/1.25 var(--sans); }
.ana p { margin: 0 0 12px; font-size: 14px; color: var(--ink-2); line-height: 1.55; }
.ana .q { font: 400 17px/1.45 var(--serif); color: var(--ink); }
.rec { padding: 9px 10px; border: 1px solid var(--line); background: var(--bg); color: var(--ink-2); font: 400 12px/1.5 var(--mono); margin: 0 0 6px; overflow-wrap: anywhere; }
.rec b { color: var(--ink); font-weight: 600; }
.rec.key { border-color: var(--teal); background: var(--teal-soft); color: var(--ink); }
.rec.drop { border-style: dashed; color: var(--ink-3); text-decoration: line-through; text-decoration-color: var(--ink-3); }
.rec .why-k { display: block; text-decoration: none; font-size: 11px; color: var(--teal); margin-top: 3px; }
.act { display: inline-flex; padding: 8px 12px; font: 700 12.5px/1 var(--mono); margin: 2px 0 10px; border: 1px solid; }
.act.right { background: var(--teal-soft); color: var(--teal); border-color: var(--teal); }
.act.held { background: var(--paper); color: var(--ink); border-color: var(--slate-hatch); }
.act.wrong { background: var(--rust-soft); color: var(--rust); border-color: var(--rust); }
.ana-foot { margin: 10px 0 0; font: 400 12px/1.5 var(--mono); color: var(--ink-3); }

/* method and submit */
.two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.two-col > div { padding: 20px 26px 20px 0; border-right: 1px solid var(--line); min-width: 0; }
.two-col > div:last-child { padding: 20px 0 20px 26px; border-right: 0; }
.two-col h3 { margin: 0 0 8px; font: 600 18px/1.25 var(--sans); }
.two-col p { margin: 0 0 12px; font-size: 14.5px; color: var(--ink-2); max-width: 66ch; }
.two-col .cli-cmd { margin-bottom: 8px; }
.two-col .cli-cmd code { font-size: 12px; }
.more { font: 600 13px/1.3 var(--mono); }

/* method and submit pages */
.doc { padding-top: 34px; }
.doc h1 { font: 400 clamp(2.2rem, 4vw, 3.2rem)/1.05 var(--serif); letter-spacing: -.02em; margin: 0 0 22px; padding-bottom: 16px; border-bottom: 1.5px solid var(--ink); }
.doc-g { display: grid; grid-template-columns: minmax(0, 70ch) minmax(280px, 1fr); gap: 48px; align-items: start; }
.col { font-size: 16px; line-height: 1.65; color: var(--ink); }
.col h1 { font-size: 2rem; border: 0; padding: 0; margin: 0 0 12px; }
.col h2 { font: 500 23px/1.2 var(--serif); margin: 34px 0 10px; padding-top: 12px; border-top: 1px solid var(--line); }
.col h3 { font: 600 16px/1.3 var(--sans); margin: 22px 0 6px; }
.col p, .col li { color: var(--ink-2); max-width: 70ch; }
.col code { background: var(--paper); border: 1px solid var(--line-2); padding: 1px 4px; }
.col ul, .col ol { padding-left: 22px; }
.aside { position: sticky; top: 64px; background: var(--paper); border: 1px solid var(--ink); padding: 16px; }
.aside h2 { margin: 0 0 10px; font: 600 14px/1.2 var(--sans); }
.aside .cli-cmd { margin-bottom: 8px; }
.aside .cli-cmd code { font-size: 12px; }
dl.params { margin: 0; border-top: 1px solid var(--line); }
.params > div { display: grid; grid-template-columns: 9.5em minmax(0, 1fr); gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line-2); font: 400 12px/1.45 var(--mono); }
.params dt { color: var(--ink-3); }
.params dd { margin: 0; color: var(--ink); font-weight: 600; overflow-wrap: anywhere; }
.pending { border: 1px dashed var(--amber); background: var(--amber-soft); padding: 14px 16px; display: grid; gap: 4px; color: var(--amber); font: 400 13px/1.4 var(--mono); }

.foot { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--line); display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 18px; font: 400 12px/1.5 var(--mono); color: var(--ink-3); }
.foot nav { display: flex; gap: 14px; }

@media (max-width: 1120px) {
  .obs-split, .hero, .two-col, .doc-g { grid-template-columns: 1fr; }
  .lens, .aside { position: static; }
  .two-col > div, .two-col > div:last-child { border-right: 0; padding: 18px 0; border-bottom: 1px solid var(--line); }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .kpis > div:nth-child(2n) { border-right: 0; }
  .kpis > div:nth-child(2n+1) { padding-left: 0; }
  .kpis > div:nth-child(n+3) { border-top: 1px solid var(--line-2); }
  .stage-strip { grid-template-columns: 1fr; }
  .stage { border-right: 0; border-bottom: 1px solid var(--line); border-top: 0; border-left: 2px solid transparent; margin: 0; }
  .stage.on { border-left-color: var(--teal); }
  .ana { grid-template-columns: 1fr; }
  .ana-col { border-right: 0; border-bottom: 1px solid var(--line); }
  .ana-col:last-child { border-bottom: 0; }
}
@media (max-width: 860px) {
  .shell { grid-template-columns: 1fr; }
  .rail { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); padding: 10px 14px 0; flex-direction: column; }
  .rail-brand { border-bottom: 0; padding: 0 0 8px; margin: 0; }
  .rail-nav { display: flex; gap: 0; overflow-x: auto; margin: 0 -14px; padding: 0 8px; border-top: 1px solid var(--line); scrollbar-width: none; }
  .rail-nav a { white-space: nowrap; border-left: 0; border-bottom: 2px solid transparent; padding: 10px 9px; }
  .rail-nav a.on, .rail-nav a[aria-current] { border-bottom-color: var(--red); background: transparent; }
  .rail-foot { display: none; }
  .ribbon { position: sticky; }
  .flow-wrap { overflow-x: auto; }
  .flow { min-width: 760px; }
}
@media (max-width: 640px) {
  body { font-size: 15px; }
  .ribbon .hash, .ribbon .seed, .ribbon .q-copy { display: none; }
  .hero { padding-top: 24px; }
  .hero-meta { grid-template-columns: 1fr 1fr 1fr; }
  .hero-meta > div { padding: 8px; }
  .tab { padding: 8px 10px 9px; }
  .tab-tools { margin-left: 0; width: 100%; }
  .tbl thead { display: none; }
  .tbl, .tbl tbody { display: block; }
  .tbl tr.row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px 10px; padding: 12px 6px; border-bottom: 1px solid var(--line-2); }
  .tbl tr.row td { padding: 0; border: 0; text-align: left; background: transparent !important; box-shadow: none !important; }
  .tbl tr.row:hover { background: var(--hover); }
  .tbl tr.row.active { background: var(--teal-soft); box-shadow: inset 2px 0 0 var(--teal); }
  .tbl tr.row td.c-s, .tbl tr.row td.c-b { grid-column: 1 / -1; }
  .tbl tr.row td.r { text-align: left; }
  .num::before { content: attr(data-k); display: block; font: 500 11px/1.2 var(--mono); color: var(--ink-3); margin-bottom: 2px; }
  .kpis { grid-template-columns: 1fr; }
  .kpis > div, .kpis > div:nth-child(n) { border-right: 0; padding: 11px 0; border-top: 1px solid var(--line-2); }
  .kpis > div:first-child { border-top: 0; }
  .studio-stage { padding: 12px; }
  .sec-head h2 { font-size: 23px; }
}
@media print {
  .rail, .ribbon, .js-only, .studio-bar { display: none !important; }
  .shell { display: block; }
}

.why-g { display: grid; grid-template-columns: minmax(120px, 200px) minmax(0, 70ch); gap: 32px; align-items: start; }
.why-mark .mark { width: 100%; height: auto; max-width: 200px; color: var(--ink); }
.portrait { display: block; width: 100%; max-width: 240px; height: auto; aspect-ratio: 1; object-fit: cover; margin: 0 0 22px; border: 1px solid var(--rule); }
@media (max-width: 760px) { .why-g { grid-template-columns: 1fr; } .why-mark .mark { max-width: 96px; } }
"""
