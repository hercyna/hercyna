"""The public site's look: the Observatory layout in light, prototype 05's palette in dark.

The dark tokens are
written twice: once under prefers-color-scheme (unless the visitor chose light) and once for an explicit
dark choice from the header toggle.
"""

# Fonts ship with the site (site/brand/fonts, SIL OFL 1.1, licence files beside them), so a page makes
# no third-party request. The build copies them to <out>/fonts/.
FONT_LINK = (
    '<link rel="preload" href="fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>'
    '<link rel="preload" href="fonts/ibm-plex-sans-regular-latin1.woff2" as="font" type="font/woff2" crossorigin>'
)

FONT_FACES = "\n".join((
    "@font-face { font-family: \"JetBrains Mono\"; font-style: normal; font-weight: 400 800; font-display: swap; src: url(\"fonts/jetbrains-mono-latin-ext.woff2\") format(\"woff2\"); unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF; }",
    "@font-face { font-family: \"JetBrains Mono\"; font-style: normal; font-weight: 400 800; font-display: swap; src: url(\"fonts/jetbrains-mono-latin.woff2\") format(\"woff2\"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }",
    "@font-face { font-family: \"Newsreader\"; font-style: italic; font-weight: 400; font-display: swap; src: url(\"fonts/newsreader-italic-latin-ext.woff2\") format(\"woff2\"); unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF; }",
    "@font-face { font-family: \"Newsreader\"; font-style: italic; font-weight: 400; font-display: swap; src: url(\"fonts/newsreader-italic-latin.woff2\") format(\"woff2\"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }",
    "@font-face { font-family: \"Newsreader\"; font-style: normal; font-weight: 200 800; font-display: swap; src: url(\"fonts/newsreader-latin-ext.woff2\") format(\"woff2\"); unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF; }",
    "@font-face { font-family: \"Newsreader\"; font-style: normal; font-weight: 200 800; font-display: swap; src: url(\"fonts/newsreader-latin.woff2\") format(\"woff2\"); unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }",
    "@font-face { font-family: \"IBM Plex Sans\"; font-style: normal; font-weight: 400; font-display: swap; src: url(\"fonts/ibm-plex-sans-regular-latin1.woff2\") format(\"woff2\"); }",
    "@font-face { font-family: \"IBM Plex Sans\"; font-style: normal; font-weight: 500; font-display: swap; src: url(\"fonts/ibm-plex-sans-medium-latin1.woff2\") format(\"woff2\"); }",
    "@font-face { font-family: \"IBM Plex Sans\"; font-style: normal; font-weight: 600; font-display: swap; src: url(\"fonts/ibm-plex-sans-semibold-latin1.woff2\") format(\"woff2\"); }",
    "@font-face { font-family: \"IBM Plex Sans\"; font-style: normal; font-weight: 700; font-display: swap; src: url(\"fonts/ibm-plex-sans-bold-latin1.woff2\") format(\"woff2\"); }",
)) + "\n"


LIGHT = {
    "bg": "#f7f6f2", "paper": "#efece5", "surface": "#ffffff", "shape": "#e7e3da", "hover": "#ebe8e0",
    "ink": "#121716", "ink-2": "#4b5653", "ink-3": "#545c59", "line": "#d6d2c8", "line-2": "#e6e2d9",
    "teal": "#0b666d", "teal-soft": "#e1f0f1", "red": "#c92026", "verdict": "#b81d23", "verdict-soft": "#fbe6e6",
    "rust": "#a84127", "rust-soft": "#f8e8e2", "slate": "#c7c4bd", "slate-hatch": "#858179",
    "amber": "#855206", "amber-soft": "#f8efdc", "sel": "#bfe0e2", "on-teal": "#ffffff", "h-accent": "#0b666d",
}
DARK = {
    "bg": "#0c1117", "paper": "#101720", "surface": "#161f2b", "shape": "#1d2938", "hover": "#233245",
    "ink": "#f4f8fa", "ink-2": "#cad7df", "ink-3": "#96abb8", "line": "#2c3c4f", "line-2": "#1f2c3b",
    "teal": "#4be3eb", "teal-soft": "rgba(75, 227, 235, .14)", "red": "#ff7b5c", "verdict": "#f8c45e",
    "verdict-soft": "rgba(248, 196, 94, .12)", "rust": "#ff7b5c", "rust-soft": "rgba(255, 123, 92, .15)",
    "slate": "#5c7180", "slate-hatch": "#a8becb", "amber": "#f8c45e", "amber-soft": "rgba(248, 196, 94, .14)",
    "sel": "#245a62", "on-teal": "#061518", "h-accent": "#4be3eb",
}


def _vars(d: dict) -> str:
    return " ".join(f"--{k}: {v};" for k, v in d.items())


TOKENS = (":root { color-scheme: light; " + _vars(LIGHT) + " }\n"
          "@media (prefers-color-scheme: dark) { :root:not([data-theme=\"light\"]) { color-scheme: dark; "
          + _vars(DARK) + " } }\n"
          ":root[data-theme=\"dark\"] { color-scheme: dark; " + _vars(DARK) + " }\n")

CSS = FONT_FACES + TOKENS + r"""
:root {
  --serif: "Newsreader", Georgia, "Times New Roman", serif;
  --sans: "IBM Plex Sans", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px; --sp-7: 48px; --sp-8: 64px;
  --fs-xs: 11px; --fs-sm: 12px; --fs-md: 13.5px; --fs-base: 15.5px; --fs-lg: 18px; --fs-xl: 24px; --fs-2xl: 32px;
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
:focus-visible, a:focus-visible, button:focus-visible, input:focus-visible, summary:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--teal); outline-offset: 2px; }
.btn.solid:focus-visible { outline: 2px solid var(--teal); outline-offset: 3px; }
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
[hidden], .btn[hidden] { display: none !important; }
.btn:hover { background: var(--shape); text-decoration: none; }
.btn.solid { background: var(--ink); color: var(--bg); }
.btn.solid:hover { background: var(--teal); border-color: var(--teal); color: var(--on-teal); }
.btn svg { width: 13px; height: 13px; flex: none; }
.btn.done { background: var(--teal); border-color: var(--teal); color: var(--on-teal); }
.copy .i-ok, .copy.done .i-copy { display: none; }
.copy.done .i-ok { display: block; }

.wrap { max-width: 1320px; margin: 0 auto; padding: 0 clamp(18px, 3vw, 40px) 64px; }

/* hero */
.hero { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 24px 36px; align-items: end;
  padding: 34px 0 28px; border-bottom: 1.5px solid var(--ink); }
.hero h1 { font: 400 clamp(2.3rem, 4.4vw, 3.6rem)/1.03 var(--serif); letter-spacing: -.025em; margin: 0 0 16px; max-width: 17ch; text-wrap: balance; }
.hero h1 em { font-style: italic; color: var(--red); }
.hero-lead { margin: 0; font-size: 16.5px; color: var(--ink-2); max-width: 56ch; line-height: 1.58; }
.hero-right { display: grid; gap: 10px; min-width: 0; max-width: 100%; }
.hero-meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 0; min-width: 0; max-width: 100%; }
.hero-meta > div { background: var(--paper); padding: 9px 11px; min-width: 0; }
.hero-meta dd { margin: 0; font: 700 13px/1.2 var(--mono); color: var(--ink); overflow-wrap: anywhere; }
.hero-meta dt { font: 400 11px/1.3 var(--mono); color: var(--ink-3); margin-top: 3px; }
.hero-meta > div { display: flex; flex-direction: column-reverse; justify-content: flex-end; }
.cli-box { background: var(--surface); border: 1px solid var(--ink); padding: 14px 16px; min-width: 0; max-width: 100%; }
.cli-top { display: flex; justify-content: space-between; gap: 10px; font: 600 11px/1.2 var(--mono); color: var(--ink-3); margin: 0 0 9px; }
.hero > div, .hero-right, .lens, .lens-sec, .dpanel, .studio, .verdict, .findings, .aside, .col { min-width: 0; max-width: 100%; }
.cli-cmd { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: var(--paper);
  border: 1px solid var(--line); padding: 8px 8px 8px 11px; font: 500 13px/1.3 var(--mono); color: var(--ink); margin: 0; min-width: 0; max-width: 100%; }
.cli-cmd code { overflow-x: visible; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; flex: 1 1 0; min-width: 0; }
.aside .cli-cmd, .two-col .cli-cmd { align-items: flex-start; flex-wrap: wrap; }
.aside .cli-cmd code, .two-col .cli-cmd code { white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: visible; flex: 1 1 16ch; }
.cli-cmd .btn { padding: 6px 9px; }
.cli-note { margin: 9px 0 0; font-size: 13px; color: var(--ink-2); line-height: 1.45; }

/* stat strip */
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--line); margin: 0; }
.kpis[data-n="1"] { grid-template-columns: minmax(0, 1fr); }
.kpis[data-n="2"] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.kpis[data-n="3"] { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.kpis[data-n="4"] { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.kpis[data-n="5"] { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.kpis[data-n="6"] { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.mt-14 { margin-top: 14px; }
.row-labels { font-size: 12.5px; }
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
.sec-head .sub { font: 400 12px/1.4 var(--mono); color: var(--ink-2); max-width: 70ch; text-wrap: balance; }

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
.tbl tr.tbl-floor-row th { background: var(--paper); color: var(--ink-2); font: 500 11.5px/1.45 var(--mono); letter-spacing: 0; padding: 8px 10px; border-bottom: 1px solid var(--ink); }
.tbl tr.tbl-floor-row th b { color: var(--ink); font-weight: 700; }
.tbl th.r, .tbl td.r { text-align: right; }
.tbl td { padding: 12px 8px; border-bottom: 1px solid var(--line-2); vertical-align: middle; transition: background .12s; }
.tbl tbody tr.row { cursor: pointer; }
.tbl tbody tr.row:hover td { background: var(--hover); }
.tbl tbody tr.row.active td { background: var(--teal-soft); }
.tbl tbody tr.row.active td:first-child { box-shadow: inset 2px 0 0 var(--teal); }
.tbl td.empty { padding: 26px 8px; color: var(--ink-2); font: 400 13px/1.5 var(--mono); }
.sys-t { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 7px; }
.sys { font: 700 15px/1.2 var(--sans); color: var(--ink); }
.sys[role="button"]:focus-visible { outline-offset: 3px; }
.rank { display: inline-grid; place-items: center; min-width: 20px; height: 20px; padding: 0 4px; background: var(--ink); color: var(--bg); font: 700 11px/1 var(--mono); }
.sys-m { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; font: 400 11.5px/1.3 var(--mono); color: var(--ink-3); margin-top: 4px; }
.lab { font: 600 11px/1 var(--mono); padding: 3px 5px; border: 1px solid var(--line); color: var(--ink-2); background: var(--paper); }
.lab.x { border: 1px dashed var(--amber); color: var(--amber); background: var(--amber-soft); }
.bar { display: flex; height: 11px; border: 1px solid var(--ink); background: var(--bg); overflow: hidden; min-width: 150px; }
.bar i + i { border-left: 1px solid var(--bg); }
.bar-svg { display: block; width: 100%; min-width: 150px; height: 11px; border: 1px solid var(--ink); background: var(--bg); }
.bar-svg .p-r { fill: var(--teal); }
.bar-svg .p-h { fill: url(#hercyna-hatch); }
.bar-svg .p-w { fill: var(--rust); }
.bar-svg .bar-sep { stroke: var(--bg); stroke-width: .7; }
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
.frontier { width: 100%; max-width: 100%; height: auto; display: block; overflow: hidden; }
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
.flow .lethe circle:nth-child(1) { animation-delay: 0.0s; }
.flow .lethe circle:nth-child(2) { animation-delay: 0.3s; }
.flow .lethe circle:nth-child(3) { animation-delay: 0.6s; }
.flow .lethe circle:nth-child(4) { animation-delay: 0.9s; }
.flow .lethe circle:nth-child(5) { animation-delay: 1.2s; }
.flow .lethe circle:nth-child(6) { animation-delay: 1.5s; }
.flow .lethe circle:nth-child(7) { animation-delay: 1.8s; }
.flow .lethe circle:nth-child(8) { animation-delay: 2.1s; }
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
.rec.drop { border-style: dashed; color: var(--ink-3); }
.rec.drop .drop-t { text-decoration: line-through; text-decoration-color: var(--ink-3); }
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
.params > div { display: grid; grid-template-columns: 10.5em minmax(0, 1fr); gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line-2); font: 400 12px/1.45 var(--mono); }
.params dt { color: var(--ink-3); }
.params dd { margin: 0; color: var(--ink); font-weight: 600; overflow-wrap: anywhere; }
.v2-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.v2-card { border: 1px solid var(--ink); background: var(--surface); padding: 18px; min-width: 0; }
.v2-card h3 { margin: 0 0 8px; font: 600 16px/1.25 var(--sans); color: var(--ink); }
.v2-card p { margin: 0 0 10px; font-size: 14px; color: var(--ink-2); line-height: 1.55; }
.v2-card ul { margin: 0; padding-left: 20px; font-size: 13.5px; color: var(--ink-2); line-height: 1.55; }
.v2-card li { margin-bottom: 4px; }
.v2-card-wide { grid-column: 1 / -1; }
.v2-tbl-wrap { overflow-x: auto; max-width: 100%; margin: 10px 0 6px; border: 1px solid var(--line); }
.v2-tbl { width: 100%; border-collapse: collapse; font: 400 12px/1.4 var(--mono); font-variant-numeric: tabular-nums; }
.v2-tbl th, .v2-tbl td { padding: 8px 9px; border-bottom: 1px solid var(--line-2); text-align: left; vertical-align: middle; }
.v2-tbl th { background: var(--paper); color: var(--ink-3); font-weight: 600; border-bottom: 1px solid var(--line); }
.v2-tbl tr.tbl-floor-row th { background: var(--paper); color: var(--ink-2); font: 500 11.5px/1.45 var(--mono); border-bottom: 1px solid var(--ink); padding: 9px 10px; }
.v2-tbl tr.tbl-floor-row th b { color: var(--ink); font-weight: 700; }
.v2-tbl td.r, .v2-tbl th.r { text-align: right; }
.v2-tbl tbody tr:last-child td, .v2-tbl tbody tr:last-child th[scope="row"] { border-bottom: 0; }
.v2-tbl th[scope="row"] { background: transparent; color: var(--ink); font-weight: 600; border-bottom: 1px solid var(--line-2); }
.v2-tbl.v2-wrap-cells td { white-space: normal; overflow-wrap: anywhere; }
.v2-tbl.v2-wrap-cells th { white-space: nowrap; overflow-wrap: normal; }
.js .v2-dpanel + .v2-dpanel:not([data-ready]) { display: none; }
.v2-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; margin: 8px 0 0; font: 400 12px/1.45 var(--mono); color: var(--ink-3); }

/* v2 full-row ranked & exploratory tables (A2 + A4) */
.v2-head-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; margin-bottom: 10px; }
.v2-head-bar h3 { margin: 0; font: 600 16px/1.25 var(--sans); color: var(--ink); display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.v2-tab-rail { display: flex; flex-wrap: wrap; border-bottom: 1px solid var(--line); margin-bottom: 12px; align-items: stretch; gap: 0 2px; }
.v2-tab { appearance: none; background: transparent; border: 0; border-bottom: 2px solid transparent; padding: 8px 12px 9px;
  margin-bottom: -1px; cursor: pointer; text-align: left; font: 600 13px/1.2 var(--sans); color: var(--ink-2); }
.v2-tab:hover { color: var(--ink); background: var(--shape); }
.v2-tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--ink); }
.v2-tab span { display: block; font: 400 11px/1.2 var(--mono); color: var(--ink-3); margin-top: 2px; }
.v2-dhead { font: 600 14px/1.3 var(--sans); margin: 14px 0 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.v2-full-wrap { border: 1px solid var(--ink); background: var(--surface); margin: 8px 0 10px; max-width: 100%; }
table.v2-full-tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.v2-full-tbl thead tr.tbl-floor-row th { background: var(--paper); color: var(--ink-2); font: 500 11.5px/1.5 var(--mono);
  padding: 9px 12px; border-bottom: 1px solid var(--ink); text-align: left; }
.v2-full-tbl thead tr.tbl-floor-row th b { color: var(--ink); font-weight: 700; }
.v2-full-tbl thead tr.tbl-cols th { background: var(--bg); color: var(--ink-3); font: 600 11px/1.3 var(--mono);
  letter-spacing: .03em; padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: bottom; }
.v2-full-tbl th.r, .v2-full-tbl td.r { text-align: right; }
.v2-full-tbl tbody th[scope="row"], .v2-full-tbl tbody td { padding: 11px 10px; border-bottom: 1px solid var(--line-2); vertical-align: middle; text-align: left; }
.v2-full-tbl tbody td.r { text-align: right; }
.v2-full-tbl tbody tr.has-split th[scope="row"], .v2-full-tbl tbody tr.has-split td { border-bottom: 0; padding-bottom: 5px; }
.v2-full-tbl tbody tr.v2-row-split td.gate-split { padding: 0 10px 10px; font: 400 11.5px/1.45 var(--mono); color: var(--ink-3); border-bottom: 1px solid var(--line-2); text-align: left; }
.v2-full-tbl tbody tr.v2-row-split td.gate-split b { color: var(--ink-2); font-weight: 600; }
.v2-full-tbl tbody tr:last-child th[scope="row"], .v2-full-tbl tbody tr:last-child td { border-bottom: 0; }
.v2-full-tbl tbody tr:hover th[scope="row"], .v2-full-tbl tbody tr:hover td { background: var(--hover); }
.v2-full-tbl tbody tr.off { display: none; }
.rank-tag { display: inline-flex; align-items: center; padding: 2px 6px; font: 700 11px/1.1 var(--mono); border: 1px solid var(--ink); background: var(--ink); color: var(--bg); }
.rank-tag.r2 { background: var(--paper); color: var(--ink); border-color: var(--ink); }
.rank-tag.unranked { background: var(--bg); color: var(--ink-3); border-color: var(--line); font-weight: 500; }
.ci-cell b { display: block; font: 700 13px/1.25 var(--mono); color: var(--ink); white-space: nowrap; }
.ci-cell small { display: block; font: 400 11px/1.3 var(--mono); color: var(--ink-3); margin-top: 2px; white-space: nowrap; }
.ci-cell.wrong b { color: var(--rust); }
.ci-cell.right b { color: var(--teal); }
.floor-head-flex { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px; }
.floor-metrics { display: flex; flex-wrap: wrap; gap: 4px 12px; }

@media (max-width: 860px) { .v2-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 820px) {
  /* Stack full rows cleanly on mobile (375px) with zero page-wide or inner horizontal scroll */
  .v2-full-wrap, .v2-tbl-wrap.v2-no-scroll { overflow-x: visible; }
  table.v2-full-tbl, .v2-full-tbl thead, .v2-full-tbl tbody { display: block; width: 100%; }
  .v2-full-tbl thead tr.tbl-floor-row, .v2-full-tbl thead tr.tbl-floor-row th { display: block; width: 100%; }
  .v2-full-tbl thead tr.tbl-cols { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .v2-full-tbl tbody tr { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; padding: 12px; border-bottom: 1px solid var(--line); }
  .v2-full-tbl tbody tr.has-split { border-bottom: 0; padding-bottom: 6px; }
  .v2-full-tbl tbody tr.v2-row-split { display: block; padding: 0 12px 12px; border-bottom: 1px solid var(--line); }
  .v2-full-tbl tbody tr.v2-row-split td.gate-split { display: block; padding: 6px 0 0; border-top: 1px dashed var(--line-2); font: 400 11px/1.45 var(--mono); color: var(--ink-3); }
  .v2-full-tbl tbody tr:last-child { border-bottom: 0; }
  .v2-full-tbl tbody th[scope="row"], .v2-full-tbl tbody td, .v2-full-tbl tbody td.r { padding: 0; border: 0; text-align: left; background: transparent !important; min-width: 0; }
  .v2-full-tbl tbody th[scope="row"], .v2-full-tbl tbody td.c-share { grid-column: 1 / -1; }
  .v2-full-tbl tbody td[data-k]::before { content: attr(data-k); display: block; font: 600 10.5px/1.2 var(--mono); color: var(--ink-3); margin-bottom: 3px; }
  .ci-cell b, .ci-cell small { white-space: normal; }

  table.v2-stack-mobile, .v2-stack-mobile thead, .v2-stack-mobile tbody { display: block; width: 100%; }
  .v2-stack-mobile thead tr.tbl-floor-row, .v2-stack-mobile thead tr.tbl-floor-row th { display: block; width: 100%; }
  .v2-stack-mobile thead tr.tbl-cols { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .v2-stack-mobile tbody tr { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; padding: 11px 10px; border-bottom: 1px solid var(--line); }
  .v2-stack-mobile tbody tr:last-child { border-bottom: 0; }
  .v2-stack-mobile tbody th[scope="row"], .v2-stack-mobile tbody td, .v2-stack-mobile tbody td.r { padding: 0; border: 0; text-align: left; min-width: 0; white-space: normal; overflow-wrap: anywhere; }
  .v2-stack-mobile tbody th[scope="row"], .v2-stack-mobile tbody td.c-wide { grid-column: 1 / -1; }
  .v2-stack-mobile tbody td[data-k]::before { content: attr(data-k); display: block; font: 600 10.5px/1.2 var(--mono); color: var(--ink-3); margin-bottom: 2px; }
}
.pending { border: 1px dashed var(--amber); background: var(--amber-soft); padding: 14px 16px; display: grid; gap: 4px; color: var(--amber); font: 400 13px/1.4 var(--mono); }

.foot { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--line); display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 18px; font: 400 12px/1.5 var(--mono); color: var(--ink-3); }
.foot nav { display: flex; gap: 14px; }

@media (max-width: 1120px) {
  .obs-split, .hero, .two-col, .doc-g { grid-template-columns: minmax(0, 1fr); }
  .lens, .aside { position: static; }
  .two-col > div, .two-col > div:last-child { border-right: 0; padding: 18px 0; border-bottom: 1px solid var(--line); }
  .kpis, .kpis[data-n] { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
  .shell { grid-template-columns: minmax(0, 1fr); }
  .rail { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); padding: 10px 14px 0; flex-direction: column; }
  .rail-brand { border-bottom: 0; padding: 0 0 8px; margin: 0; }
  .rail-nav { display: flex; flex-wrap: wrap; gap: 0 2px; overflow-x: visible; margin: 0 -14px; padding: 2px 10px; border-top: 1px solid var(--line); }
  .rail-nav a { white-space: nowrap; border-left: 0; border-bottom: 2px solid transparent; padding: 8px 7px 7px; font-size: 12.5px; }
  .rail-nav a.on, .rail-nav a[aria-current] { border-bottom-color: var(--red); background: transparent; }
  .rail-foot { display: none; }
  .ribbon { position: sticky; }
  .flow-wrap { overflow-x: auto; max-width: 100%; }
  .flow { min-width: 760px; }
}
@media (max-width: 640px) {
  body { font-size: 15px; }
  .ribbon .hash, .ribbon .seed, .ribbon .q-copy { display: none; }
  .hero { padding-top: 24px; }
  .hero h1 { max-width: 100%; font-size: clamp(2rem, 8.5vw, 2.5rem); }
  .hero-lead { max-width: 100%; }
  .cli-cmd { flex-wrap: wrap; align-items: flex-start; }
  .cli-cmd code { white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: visible; flex: 1 1 100%; }
  .hero-meta { grid-template-columns: 1fr 1fr 1fr; }
  .hero-meta > div { padding: 8px; min-width: 0; }
  .tab { padding: 8px 10px 9px; }
  .tab-tools { margin-left: 0; width: 100%; }
  .tbl, .tbl thead, .tbl tbody { display: block; width: 100%; }
  .tbl thead tr.tbl-floor-row, .tbl thead tr.tbl-floor-row th { display: block; width: 100%; }
  .tbl thead tr.tbl-cols { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .tbl tr.row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 6px; padding: 12px 6px; border-bottom: 1px solid var(--line-2); }
  .tbl tr.row td { padding: 0; border: 0; text-align: left; background: transparent !important; box-shadow: none !important; min-width: 0; }
  .tbl tr.row:hover { background: var(--hover); }
  .tbl tr.row.active { background: var(--teal-soft); box-shadow: inset 2px 0 0 var(--teal); }
  .tbl tr.row td.c-s, .tbl tr.row td.c-b { grid-column: 1 / -1; }
  .tbl tr.row td.r { text-align: left; }
  .bar, .bar-svg { min-width: 0; width: 100%; }
  .wrap { padding-inline: 18px; }
  .hero-meta dd { white-space: normal; overflow-wrap: anywhere; }
  .num b { font-size: 13px; }
  .num small { font-size: 10.5px; }
  .num::before { content: attr(data-k); display: block; font: 500 11px/1.2 var(--mono); color: var(--ink-3); margin-bottom: 2px; }
  .why table { display: block; overflow-x: auto; max-width: 100%; }
  .kpis, .kpis[data-n] { grid-template-columns: 1fr; }
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
.portrait { display: block; width: 100%; max-width: 240px; height: auto; aspect-ratio: 1; object-fit: cover; margin: 0 0 22px; border: 1px solid var(--line); }
@media (max-width: 760px) { .why-g { grid-template-columns: 1fr; } .why-mark .mark { max-width: 96px; } }
/* polish 2026-11-20: navigation, anchors, links and the small-screen shell */
html { scroll-padding-top: 58px; }
.sec { scroll-margin-top: 0; }
.rail-nav a:focus-visible, .tab:focus-visible { outline-offset: -2px; }
.col p a, .col li a { text-decoration: underline; text-decoration-color: color-mix(in srgb, currentColor 40%, transparent); }
.col p a:hover, .col li a:hover { text-decoration-color: currentColor; }
.col > h2:first-child { margin-top: 0; padding-top: 0; border-top: 0; }
.foot nav { flex-wrap: wrap; gap: 6px 14px; }
.foot nav a[aria-current] { color: var(--ink); }
@media (max-width: 860px) {
  html { scroll-padding-top: 76px; }
  .shell { grid-template-columns: minmax(0, 1fr); }
  .rail { display: contents; }
  .rail-brand { background: var(--paper); padding: 10px 14px 8px; }
  .rail-nav { position: sticky; top: 0; z-index: 30; margin: 0; padding: 2px 10px; background: var(--paper);
    border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap;
    overflow-x: visible; gap: 0 2px; }
  .rail-nav a { padding: 8px 7px 7px; font-size: 12.5px; }
  .ribbon { position: static; }
}
@media (max-width: 1120px) {
  .doc-g { grid-template-columns: minmax(0, 1fr); }
}
.col code { overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .tab-rail .tab { flex: 1 1 0; min-width: 0; }
}
.col pre { overflow-x: auto; max-width: 100%; }
@media (max-width: 640px) {
  .hero-meta { grid-template-columns: 1fr 1fr; }
  .hero-meta > div:nth-child(3):last-child { grid-column: 1 / -1; }
}
.touch-only { display: none; }
@media (hover: none) { .touch-only { display: inline; } .hover-only { display: none; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; }
}

/* R10 C2 + C4: exploratory cause-split takeaways, replay fidelity header, and honest limits */
.cause-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px; }
.cause-card { border: 1px solid var(--ink); background: var(--surface); padding: 14px 16px; min-width: 0; }
.cause-card-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 8px; margin-bottom: 8px; }
.cause-card-top h3 { margin: 0; font: 700 15px/1.2 var(--sans); color: var(--ink); }
.cause-takeaway { margin: 0 0 8px; font: 600 14px/1.45 var(--sans); color: var(--ink); }
.cause-detail { margin: 0; font: 400 12px/1.45 var(--mono); color: var(--ink-3); }
.cause-fid-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px;
  padding: 10px 12px; margin: 4px 0 8px; background: var(--paper); border: 1px solid var(--line); font: 400 12.5px/1.45 var(--mono); color: var(--ink-2); }
.cause-fid-bar b { color: var(--ink); font-weight: 700; }
.cause-fullrow { font: 400 12px/1.5 var(--mono); color: var(--ink-2); margin: 0 0 10px; padding: 8px 12px; background: var(--bg); border: 1px solid var(--line-2); }
.cause-fullrow b { color: var(--ink); }
.limits-box { border: 1px solid var(--line); background: var(--surface); padding: 16px 20px; }
.limits-list { margin: 0; padding-left: 20px; display: grid; gap: 8px; font-size: 14.5px; line-height: 1.55; color: var(--ink-2); max-width: 78ch; }
.limits-list li strong { color: var(--ink); }
@media (max-width: 960px) { .cause-cards { grid-template-columns: minmax(0, 1fr); } }

/* R12 Flagship UI additions: above-the-fold CTA, sortable sticky tables, stacked cause bars, product & submit pages */
.hero-finding { margin: 12px 0 0; font: 500 15px/1.55 var(--sans); color: var(--ink); max-width: 58ch; padding: 10px 12px; border-left: 3px solid var(--teal); background: var(--surface); }
.hero-cta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.pack-link { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--line); }
.pack-link:hover, .pack-link:focus-visible { color: var(--teal); border-bottom-color: var(--teal); text-decoration: none; }
.sys-link { color: var(--ink); text-decoration: none; border-bottom: 1px solid transparent; }
.sys-link:hover, .sys-link:focus-visible { color: var(--teal); border-bottom-color: var(--teal); text-decoration: none; }

.prod-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin: 12px 0 14px; font: 500 12px/1.3 var(--mono); color: var(--ink-2); }
.prod-pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border: 1px solid var(--line); background: var(--paper); color: var(--ink); font: 600 12px/1.2 var(--mono); }
.prod-pill:hover { border-color: var(--teal); color: var(--teal); text-decoration: none; background: var(--surface); }
.prod-meta-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; margin: -6px 0 18px; font: 500 12.5px/1.4 var(--mono); color: var(--ink-2); }

.sort-btn { appearance: none; background: transparent; border: 0; padding: 0; margin: 0; font: inherit; color: inherit;
  cursor: pointer; display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px; text-align: right; }
.sort-btn:hover, th[aria-sort="ascending"] .sort-btn, th[aria-sort="descending"] .sort-btn { color: var(--teal); }
.sort-ind { font-size: 11px; opacity: .7; }
th[aria-sort="ascending"] .sort-ind, th[aria-sort="descending"] .sort-ind { opacity: 1; font-weight: 700; }

.v2-sticky-bar { display: none; }
.decider-select { font: 600 12.5px/1.2 var(--mono); background: var(--surface); color: var(--ink); border: 1px solid var(--ink); padding: 6px 8px; max-width: 100%; min-width: 0; }
.btn-mini { appearance: none; border: 1px solid var(--line); background: var(--surface); color: var(--ink); font: 600 11.5px/1.2 var(--mono); padding: 5px 8px; cursor: pointer; }
.btn-mini:hover { background: var(--shape); }
.btn-mini[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }

@media (min-width: 821px) {
  .v2-full-tbl thead tr.tbl-cols th { position: sticky; top: 48px; z-index: 6; }
}
@media (max-width: 860px) {
  .v2-sticky-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px;
    position: sticky; top: 0; z-index: 16; background: var(--paper); border: 1px solid var(--line); padding: 8px 10px; margin: 0 0 10px; }
  .v2-sticky-bar label { display: inline-flex; align-items: center; gap: 6px; font: 600 11.5px/1.2 var(--mono); color: var(--ink-2); max-width: 100%; }
  .mob-sort { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font: 500 11.5px/1.2 var(--mono); color: var(--ink-2); }
}

.cause-legend { display: flex; flex-wrap: wrap; gap: 8px 14px; font: 500 12px/1.3 var(--mono); color: var(--ink-2); margin: 0 0 14px; }
.cause-sw { width: 11px; height: 11px; border: 1px solid var(--ink); display: inline-block; flex: none; }
.c-ns { background: var(--rust); fill: var(--rust); }
.c-sn { background: var(--amber); fill: var(--amber); }
.c-rn { background: var(--teal); fill: var(--teal); }
.c-cf { background: var(--verdict); fill: var(--verdict); }
.c-br { background-color: var(--slate); background-image: repeating-linear-gradient(135deg, transparent 0 2px, var(--slate-hatch) 2px 3.5px); fill: url(#hercyna-hatch); }
.cause-svg-wrap { border: 1px solid var(--line); background: var(--bg); padding: 10px 12px; margin: 8px 0 12px; }
.cause-svg { width: 100%; height: auto; display: block; }
.cause-svg text { font-family: var(--mono); font-size: 11px; fill: var(--ink); font-weight: 600; }
.cause-svg .cause-sub { font-size: 10px; fill: var(--ink-2); font-weight: 500; }
.cause-svg .cause-pct { fill: #ffffff; stroke: #121716; stroke-width: 2.5px; paint-order: stroke; }

.repro-box { border: 1px solid var(--ink); background: var(--surface); padding: 16px 18px; display: grid; gap: 10px; }
.repro-box p { margin: 0; font-size: 14px; color: var(--ink-2); }
.step-list { display: grid; gap: 16px; margin-top: 18px; }
.step-card { border: 1px solid var(--line); background: var(--surface); padding: 16px 18px; min-width: 0; }
.step-card h3 { margin: 0 0 6px; font: 600 16px/1.3 var(--sans); color: var(--ink); }
.code-block { border: 1px solid var(--ink); background: var(--paper); margin: 10px 0 0; min-width: 0; max-width: 100%; }
.code-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 10px; border-bottom: 1px solid var(--line); font: 600 11.5px/1.2 var(--mono); color: var(--ink-2); }
.code-block pre { margin: 0; padding: 12px; overflow-x: auto; font: 400 12px/1.5 var(--mono); color: var(--ink); max-width: 100%; }
.excused-list { list-style: none; padding: 0; margin: 10px 0 0; display: flex; flex-wrap: wrap; gap: 6px; }
.excused-list li { font: 500 12px/1.2 var(--mono); padding: 4px 8px; border: 1px solid var(--line); background: var(--paper); color: var(--ink); }

/* R14 Launch readiness: disclaimer bar, version switcher, updates drawer, terms gates, badges, data bar, explainer */
.disc-bar { background: var(--paper); border-bottom: 1px solid var(--line); padding: 7px clamp(18px, 3vw, 40px);
  font: 400 12.5px/1.45 var(--sans); color: var(--ink-2); display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 16px; }
.disc-bar strong { color: var(--ink); font-weight: 600; }
.disc-links { display: inline-flex; flex-wrap: wrap; gap: 6px 12px; font: 500 12px/1.2 var(--mono); }
.ver-switch { display: inline-flex; border: 1px solid var(--line); background: var(--surface); }
.ver-switch a { padding: 4px 8px; font: 600 11.5px/1.1 var(--mono); color: var(--ink-2); border-left: 1px solid var(--line); text-decoration: none; }
.ver-switch a:first-child { border-left: 0; }
.ver-switch a[aria-current="page"], .ver-switch a.active { background: var(--ink); color: var(--bg); }
.ver-switch a:hover { background: var(--shape); color: var(--ink); }
.ver-switch a[aria-current="page"]:hover, .ver-switch a.active:hover { background: var(--teal); color: var(--on-teal); }

.updates-box { border: 1px solid var(--ink); background: var(--surface); padding: 14px 18px; margin-top: 32px; }
.updates-box summary { cursor: pointer; font: 600 14px/1.3 var(--mono); color: var(--ink); list-style: none;
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.updates-box summary::-webkit-details-marker { display: none; }
.updates-sub { font: 400 12.5px/1.3 var(--sans); color: var(--ink-2); }
.updates-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.upd-col { border: 1px solid var(--line); background: var(--bg); padding: 12px 14px; display: grid; gap: 8px; align-content: start; }
.upd-col h3 { margin: 0; font: 600 14px/1.25 var(--sans); color: var(--ink); }
.upd-col p { margin: 0; font-size: 13px; line-height: 1.45; color: var(--ink-2); }
.upd-form { display: grid; gap: 8px; }
.upd-form input[type="email"] { width: 100%; padding: 8px 10px; border: 1px solid var(--ink); background: var(--surface); color: var(--ink); font: 400 13px/1.2 var(--mono); border-radius: 0; }
.consent-lbl { display: flex; align-items: flex-start; gap: 7px; font: 400 12px/1.35 var(--sans); color: var(--ink-2); cursor: pointer; }
.consent-lbl input { margin-top: 2px; flex: none; accent-color: var(--teal); }
.hp-field { position: absolute; left: -9999px; width: 1px; height: 1px; opacity: 0; overflow: hidden; }
.upd-status { font: 500 12px/1.35 var(--mono); color: var(--verdict); margin: 0; }
@media (max-width: 820px) { .updates-grid { grid-template-columns: 1fr; } }

.terms-gate { border: 1px solid var(--line); background: var(--paper); padding: 12px 14px; margin: 10px 0 12px; display: grid; gap: 8px; }
.terms-gate.terms-warn { border-color: var(--red); background: var(--verdict-soft); }
.terms-gate h3, .terms-gate p { margin: 0; font-size: 13px; line-height: 1.45; color: var(--ink-2); }
.terms-gate h3 { font: 600 13px/1.3 var(--mono); color: var(--ink); }
.terms-list { margin: 0; padding-left: 18px; display: grid; gap: 4px; font-size: 12.5px; line-height: 1.4; color: var(--ink-2); }
.terms-check { display: inline-flex; align-items: center; gap: 8px; font: 600 12.5px/1.3 var(--mono); color: var(--ink); cursor: pointer; }
.terms-check input { accent-color: var(--teal); width: 15px; height: 15px; }

.data-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 14px;
  border: 1px solid var(--ink); background: var(--paper); padding: 12px 16px; margin: 14px 0 18px; }
.data-bar-btns { display: flex; flex-wrap: wrap; gap: 8px; }
.badge-box { border: 1px solid var(--line); background: var(--surface); padding: 16px 18px; display: grid; gap: 10px; }
.badge-preview { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.badge-preview img { height: 22px; width: auto; display: block; }
.coming-next { border: 1px dashed var(--line); background: var(--surface); padding: 14px 18px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px; }
.coming-next p { margin: 4px 0 0; font-size: 14px; color: var(--ink-2); }
.explainer-box { border: 1px solid var(--ink); background: var(--surface); padding: 14px 18px; display: grid; gap: 12px; }
.explainer-media { width: 100%; max-width: 820px; border: 1px solid var(--line); background: var(--paper); aspect-ratio: 16 / 9; display: block; }
.explainer-transcript summary { cursor: pointer; font: 600 13px/1.3 var(--mono); color: var(--teal); }
.doc-table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13.5px; }
.doc-table th, .doc-table td { border-bottom: 1px solid var(--line); padding: 9px 10px; text-align: left; }
.doc-table th { font: 600 12px/1.2 var(--mono); color: var(--ink-2); background: var(--paper); }
.err-hero { border: 1px solid var(--ink); background: var(--surface); padding: 28px 24px; max-width: 66ch; }
"""
