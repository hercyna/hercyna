# UI Reference Notes (Round 12)

Surveyed three public benchmark interfaces on 2026-11-25 to extract interaction patterns for Hercyna while keeping Hercyna's own layout, typography, palette, naming, and domain vocabulary distinct.

## 1. Stanford CRFM HELM (`https://crfm.stanford.edu/helm/`)
- **Pattern worth using 1: Multi-metric table with raw artifact drill-down.** Every score connects down to scenario runs and exported JSON artifacts instead of dead-ending at an opaque composite number. In Hercyna, every summary rate links directly to its text-free pack `summary.json` and full per-product breakdown pages.
- **Pattern worth using 2: Dedicated track and leaderboard directory.** HELM clearly separates distinct benchmark tracks from a persistent landing hub so visitors understand scope at a glance. In Hercyna, every page keeps the unified left rail (`Board`, `Chat`, `Coding`, `Method`, `Limits`, `Submit`, `Why Hercyna`, `About`).
- **Pattern to avoid: Dense horizontal grids on narrow screens.** Wide multi-scenario tables with many fixed columns force horizontal page scrolling on mobile devices. Hercyna pairs compact mobile stacking and decider selection with interactive column sorting and zero `375 px` viewport overflow.

## 2. SWE-bench Leaderboards (`https://www.swebench.com/`)
- **Pattern worth using 1: Harness vs. standardized-baseline separation.** SWE-bench visually separates standardized baseline views from custom agent scaffolds so apples-to-apples comparisons stay explicit. Hercyna separates gate-passing product systems from algorithmic baselines (`bm25`, `dense_raw`, `hybrid_bm25_dense`, `recent`, `empty`) with explicit baseline toggles and badges.
- **Pattern worth using 2: Compact developer-first reproduction and trace metadata.** Linking directly from table views to evaluation configs and run artifacts gives engineers immediate trust signals. Hercyna pairs every table with a `"Reproduce this table"` command block and direct pack links.
- **Pattern to avoid: Headline ranking by one metric alone.** Ordering rows solely by a single solve rate encourages tuning for aggregate score without surfacing failure modes. Hercyna refuses single-number rank numbers (`No "Rank #"`) and prints wrong action, withheld action, right action, floor noise, and left-out error count together on every row.

## 3. Hugging Face Open LLM Leaderboard (`https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard`)
- **Pattern worth using 1: Inline visual distribution bars next to numeric rates and intervals.** Compact horizontal fill bars help visitors scan proportional differences rapidly before reading bracketed confidence intervals. Hercyna uses inline right/withheld/wrong share bars beside exact 95% interval cells and per-product stacked cause-split bars.
- **Pattern worth using 2: Explicit client-side sorting and theme parity.** Instant header sorting plus a crisp light/dark toggle makes exploratory inspection frictionless. Hercyna adds client-side sorting on wrong, withheld, and right action plus mobile sticky filter controls while preserving server-free static HTML readability without JavaScript.
- **Pattern to avoid: Heavy SPA bundle boot and aggregated average rankings.** Large client-side frameworks hurt mobile Lighthouse performance and accessibility. Hercyna ships zero framework runtime, pre-rendered HTML tables, and compact inline CSS/JS so every page stays fast on mobile and desktop.
