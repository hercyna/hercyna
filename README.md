# Hercyna

Hercyna scores agent memory products by the decisions an agent makes from what they return. Each case is a dated history and one request. A memory product reads the history and returns a short view. A decider model reads only that view and picks one of five discrete actions. A rule oracle knows the right action.

Every metric is shown side by side: **wrong action**, **withheld action**, and **right action**, paired with the decider's own **full-history noise floor** and the count of **ingest-error cases left out**. The frozen separation rule tests product pairs against each other across gate-passing deciders (`Lower wrong action` vs. `Higher wrong action`, shown with the full row and no rank numbers).

All cases in protocol v1 and protocol v2 are synthetic.

## Disclaimer

Experimental results. See [TERMS.md](TERMS.md), [LEGAL.md](LEGAL.md), and [PRIVACY.md](PRIVACY.md).

1. Hercyna is an experimental research benchmark. Results are provided as is, with no warranty of any kind.
2. Results describe a fixed protocol, fixed synthetic cases and fixed deciders on a given date. They are not a certification, endorsement, ranking of fitness for any use, or a claim about any product in production.
3. The person using or citing the results is solely responsible for their interpretation and any decision made from them.
4. No liability for any loss arising from use of the software, data, site or results, to the extent the law allows.
5. Product names are trademarks of their owners, used only to identify what was tested. No affiliation or endorsement is implied.
6. Vendors may request a correction or a rerun through `.github/ISSUE_TEMPLATE/correction_request.md` or `corrections.html`.

First-time `bench` CLI runs display this notice and ask you to type `accept` once (`~/.config/hercyna/accepted.json`, storing only version and date). For scripts and CI, pass `--accept-terms` or set `HERCYNA_ACCEPT_TERMS=1`. Run `bench terms` to read the full text.

## Install

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/hercyna/hercyna
cd hercyna
uv sync
```

## Run the quickstart

```bash
uv run bench quickstart --mock --accept-terms
```

This runs 20 cases with the rule oracle as the decider. It needs no key, costs nothing, and checks the install.

## Verify the v2 `chat_agent` protocol and released table

The released, text-free files for the 40-cell v2 `chat_agent` sweep (`5` gate-passing deciders x `8` systems, budget `1,000`, `3` repeats) live under `results/table/v2/chat_agent/`. The frozen v2 specification and hashes live in `protocol/v2.json` (`protocol/v1.json` stays on the `main` branch for v1 `account_review`).

```bash
uv run bench freeze --check --protocol protocol/v2.json --accept-terms
uv run bench verify results/table/v2/chat_agent/haiku-5-5/bm25 --accept-terms
uv run bench verdict results/table/v2/chat_agent/*/* --accept-terms
```

- `freeze --check --protocol protocol/v2.json` confirms every frozen code, config, and dataset hash matches `protocol/v2.json` (Steps 1–9).
- `bench verify` runs all seven submission and protocol checks against `protocol/v2.json` and `data/v2/chat_agent/table_cases.jsonl` (you can also pass `--protocol protocol/v2.json --cases data/v2/chat_agent/table_cases.jsonl` explicitly). Mem0 has 2 ingest-error cases and Graphiti has 13; `bench verify` reads `health.json` and reports those documented missing rows as `"ingest error, documented"` with their count while still failing any other missing row.
- `bench verdict` pools all 40 released v2 cells and applies the frozen separation rule.

## What this does not show

- **One chat track only:** the v2 released table covers synthetic multi-session chat (`chat_agent`). Coding-agent Mode A sweeps are held.
- **Five deciders:** v2 chat claims hold only for the five gate-passing deciders tested; other models may read the same views differently.
- **Synthetic cases:** all 300 v2 chat cases and all 300 v1 table cases come from deterministic generators, not production logs.
- **Ingest-error cases left out:** Mem0 has 2 ingest-error cases and Graphiti has 13 out of 300; left-out counts are excluded from decision rates and shown on every table row.
- **The cause split is exploratory:** `src/memory_bench/diagnostics/cause_split.py` replays dumped stores offline (`Graphiti` high-fidelity `99.8%` view-unit match; `Mem0` `38.9%` and `LangMem` `59.0%` marked `low-fidelity`).

See [docs/positioning.md](docs/positioning.md) for how Hercyna compares with LongMemEval, LoCoMo, MemoryAgentBench, MemBench, and GoodAI LTM Benchmark.

### Rebuild a v2 chat cell from shipped packs

You can deterministically recompute `summary.json` (rates and 95% paired-bootstrap intervals) for any released v2 cell from its shipped `metrics.jsonl` with zero API calls:

```bash
cp -R results/table/v2/chat_agent/haiku-5-5/bm25 /tmp/rebuild-bm25
uv run bench report /tmp/rebuild-bm25
diff -u results/table/v2/chat_agent/haiku-5-5/bm25/summary.json /tmp/rebuild-bm25/summary.json
```

### Rebuild the static results site

```bash
uv run bench site build --out site/dist
uv run bench site serve --port 8795
```

This builds `site/dist/` (all core pages, product detail pages, offline fonts, and packed JSON summaries) and serves it locally on `http://127.0.0.1:8795/`.

## Rebuild the v1 table

Released files of the ten v1 runs (`8` table runs plus `2` exploratory empty-baseline runs) live in `results/table/v1/`:

```bash
uv run bench verdict results/table/v1/day13-table-*
```

## Score your own memory

1. Write an adapter with four methods: `reset`, `ingest`, `retrieve`, `close`. See `CONTRIBUTING.md`, `docs/adapter-guide.md`, and the Mem0, LangMem, and Graphiti adapters in `src/memory_bench/adapters/`.
2. Point your product's model calls at the bench's local endpoint (`docs/product-proxy.md`) so every product shares the same memory-side model.
3. Price the run first with `uv run bench dryrun`, then run with a dollar cap: `uv run bench run --protocol protocol/v2.json --cap-usd <n> ...`.
4. Pack and verify the result (passing `--exploratory` unless running with frozen v2 table configs), then open a pull request:

```bash
uv run bench submit pack <run> --out submissions --exploratory
uv run bench verify submissions/<system>/<date>-<slug>
```

## Product environments

Each product's package pins conflicting dependencies, so each runs in its own virtual environment:

```bash
uv sync --extra mem0
UV_PROJECT_ENVIRONMENT=.venv-langmem uv sync --extra langmem
UV_PROJECT_ENVIRONMENT=.venv-graphiti uv sync --extra graphiti
```

Graphiti requires FalkorDB 6.0.2: `docker run -d --name bench-falkordb -p 127.0.0.1:6390:6379 falkordb/falkordb:6.0.2`.

## Layout

| Path | What |
| --- | --- |
| `src/memory_bench/` | engine, adapters, deciders, scoring, diagnostics, site, submission tools |
| `protocol/` | frozen v1 and v2 protocols and preregistration |
| `data/` | development and table cases (v1 and v2 `chat_agent`) |
| `results/table/v1/` | released files of the v1 table runs |
| `results/table/v2/chat_agent/` | released files of the 40-cell v2 `chat_agent` runs |
| `docs/` | design specifications for each subsystem |
| `site/` | site copy, brand assets, and media |

## Licence

Code under Apache-2.0 (`LICENSE`). Data, cases, and result files under CC BY 4.0 (`LICENSE-DATA`). See `CITATION.cff` for citation metadata.
