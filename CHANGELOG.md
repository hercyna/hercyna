# Changelog

All notable changes to Hercyna, the agent-memory benchmark.

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
