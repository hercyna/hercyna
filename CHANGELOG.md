# Changelog

All notable changes to Hercyna, the agent-memory benchmark.

## v2.0.0-rc1 (2026-11-25)

Experimental results. See TERMS.md.

### Added

- **Legal & CLI terms gate (`TERMS.md`, `LEGAL.md`, `PRIVACY.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `terms.py`):** single-source-of-truth disclaimer, first-run interactive terms prompt (`bench terms`, `--accept-terms`, `HERCYNA_ACCEPT_TERMS=1`, versioned acceptance in `~/.config/hercyna/accepted.json` with zero PII), and per-page site disclaimer bar.
- **Updates & launch infrastructure (`site/assets_gen.py`, `Caddyfile`, `Dockerfile`, `railway.json`, `docs/deploy-railway.md`):** externalized CSS/JS with zero inline scripts or styles, `Get updates` panel with Atom feed (`feed.xml`), GitHub release link, and optional env-gated email form (`HERCYNA_NEWSLETTER_ACTION`), `changelog.html`, `corrections.html`, `terms.html`, `privacy.html`, `legal.html`, `404.html`, `/v2/` permalinks, neutral embeddable badges (`badge/*.svg`), 1200x630 PNG/SVG share cards, downloadable table CSV/JSON packs, and `robots.txt` / `sitemap.xml` / `.well-known/security.txt`.

- **Protocol v2 (`protocol/v2.json`, Steps 1–9):** `chat_agent` multi-session evaluation track (`300` table cases in `data/v2/chat_agent/table_cases.jsonl`, `6` causal mechanisms `H0–H3`, `HU1–HU2`, `5` discrete actions, balanced workflow slots and override lags).
- **Shared memory-side proxy & view stage (`Step 6`):** single-pass shared ingestion across deciders (`deepseek-v4.1-flash` + `bge-m3`), per-view audit (`view-audit.json`), and shared view reuse.
- **Released v2 `chat_agent` table (`results/table/v2/chat_agent/`):** `40` verified, text-free cells (`5` gate-passing deciders across Anthropic Haiku 5.5 and OpenCode DeepSeek V4.1 Flash, GLM 5.3 Flash, MiniMax M3, and Qwen 3.8 Flash x `8` systems at `1,000`-token budget, `3` repeats), plus pooled verdict (`results/verdicts/v2-chat-agent.json`).
- **Step 9 append-only ledger adjustments (`ledger_v3.py`, `ledger_adjustments.jsonl`):** reconciles every uncached call-log row against `spend.json` within `\$0.005`.
- **Exploratory v2 Chat UI (`chat.html`):** full-row separating product pairs with `Lower wrong action` / `Higher wrong action` labels (no rank numbers), inclusion-exclusion gated-case split line (`validity fails + ingest errors − both → gated`), decider floors in table headers, Chow decision-cost grid, and ACBI conflict hazard panel.
- **Offline cause-split diagnostic (`diagnostics/cause_split.py`):** zero-cost cache-replay store dump (`Mem0`, `LangMem`, `Graphiti`) and 4-way cause attribution (`not_stored`, `stored_not_returned`, `returned_not_used`, `conflict_returned`) with paired-bootstrap 95% intervals.
- **Release metadata:** `CITATION.cff`, `CONTRIBUTING.md`, GitHub Actions workflow (`.github/workflows/ci.yml`), and v2 submission auto-detection in `submit/verify.py`.

### Fixed

- Updated `bench verify` (`submit/verify.py`) to accept valid cumulative v2 step prefixes (Steps 1–8 under Step 9 freeze) and auto-select `protocol/v2.json` and `data/v2/chat_agent/table_cases.jsonl` on v2 packs.
- Eliminated all em dashes and generated-media phrasing across built site pages (`chat.html`, `index.html`).

## v1.0.0-rc1 (2026-11-21)

### Added

- Protocol v1 freeze with hashed metric, deciders, adapters and table cases.
- Adapter contract for memory products, with one config file per adapter.
- Mem0, LangMem and Graphiti adapters, each with pinned package versions.
- Baselines: full, bm25, recent and empty.
- Two deciders: Claude Haiku 5.5 and Grok 4.20 reasoning, each with a validity check.
- Memory-side model config: non-reasoning Grok, 25 records per write.
- Product proxy: one OpenAI-compatible endpoint for every product, with a call cache and a text-free call log.
- View stage: product ingest runs once per case, in parallel, and resumed runs reuse stored views.
- Scoring with the floor, excess wrong-action rates and the paired bootstrap.
- Separation rule, applied to product pairs on each decider.
- Table cases from a fresh seed, plus a 20-case quickstart set.
- `bench quickstart`, `bench run`, `bench report`, `bench freeze` and `bench dryrun` commands.
- `bench stability` and `bench power` reports for repeat noise and detectable effects.
- Text-free run files, with a scanner that checks for model text.
- `bench verdict` pools table runs that share cases, protocol and calls, then applies the frozen rule.
- `bench verify` with seven checks, and `bench submit pack` for the submission bundle.
- Pull request template with submission checklist items.
- Static results site with index, method and submit pages.
- Health report: product log warnings counted per adapter, so dropped memories show.
- View audit and health rebuild.
- v1 table results in `results/table/v1`, released text-free, with empty baseline rows.
- Hercyna mark and favicon.

### Changed

- Site board uses two axes and shows no rank numbers. Baselines are unranked.
- Site rebuilt with a findings block and a limits section.
- Each product runs in its own environment, because product dependencies conflict.
- Quickstart prints a smoke-test line and no longer prints a validity verdict.

### Fixed

- Site: per-case cost now includes the memory-side model.
- Site: product cost includes the ingest paid in another run.
- Site: decider tabs stay in one scrolling row on phones.
- Site: anchor links jump to their section at once.
- Decider seeds start at 1, because the Grok API refuses seed 0.
- Verify: the label check uses table cases only for table-grade submissions.
