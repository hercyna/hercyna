# Hercyna

Hercyna scores agent memory products by the decisions an agent makes from what they return. Each case is a dated account history and one request. A memory product reads the history and returns a short view. A decider model reads only that view and picks one of five actions. A rule oracle knows the right one.

The headline is the **excess wrong-action rate**: the share of decisions that take a wrong action, minus the decider's own repeat noise on the full history. Withheld action is always shown beside it, so a product cannot look safe by never acting. The frozen rule reports a product pair as separated only when it separates on two deciders from two vendors, in the same direction. The board prints no rank numbers: it shows wrong, withheld and right side by side.

All cases in protocol v1 are synthetic.

## Install

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/hercyna/hercyna
cd hercyna
uv sync
```

## Run the quickstart

```bash
uv run bench quickstart --mock
```

This runs 20 cases with the rule oracle as the decider. It needs no key, costs nothing and checks the install. Twenty cases say nothing about a product.

## Rebuild the v1 table

The released files of the eight table runs are in `results/table/v1/`. They hold actions, counts, timings and hashes, never record text.

```bash
uv run bench freeze --check
uv run bench verdict results/table/v1/day13-table-*
uv run bench site build --out site/dist
```

The folder holds ten run folders: the eight table runs plus two exploratory empty-baseline runs. The verdict pools only the table runs.

- `freeze --check` confirms every frozen file matches `protocol/v1.json`.
- `verdict` pools the eight runs and applies the frozen separation rule. It prints the excess wrong-action rate per product and decider, the pair tests and the verdict.
- `site build` writes the results site from the same files.

## Score your own memory

1. Write an adapter with four methods: `reset`, `ingest`, `retrieve`, `close`. See `docs/adapter-guide.md` and the Mem0, LangMem and Graphiti adapters in `src/memory_bench/adapters/`.
2. Point your product's model calls at the bench's local endpoint, so every product uses the same memory-side model (`docs/product-proxy.md`).
3. Price the run first with `uv run bench dryrun`, then run it with a dollar cap: `uv run bench run --cap-usd <n> ...`.
4. Pack and verify the result, then open a pull request:

```bash
uv run bench submit pack <run> --out submissions
uv run bench verify submissions/<system>/<date>-<slug>
```

The maintainer runs a vendor-tuned configuration on the table cases and publishes it as its own row, labelled exploratory.

## Deciders and keys

Decider configs are in `configs/deciders/`. The frozen v1 configs call the maintainer's own key wrappers. To run a live decider yourself, copy `configs/deciders/openai-compat.example.json` and give it your endpoint and an environment variable for the key. A run with a changed config is labelled exploratory, because table grade requires the frozen config hashes.

## Table grade and outside runs

- In protocol v1, table grade requires the frozen decider, adapter and memory-side config hashes. The frozen configs read the maintainer's own keys.
- A run you make with your own keys therefore runs with a changed config and is labelled exploratory. That is expected and does not reduce its value as a comparison.
- To compare with the table, run the same cases, deciders, budget and repeats, and compare your row with the released one.
- Protocol v2 will read keys from environment variables so outside runs can be table grade.

## Product environments

Each product's package pins different shared libraries, so each runs in its own environment:

```bash
uv sync --extra mem0
UV_PROJECT_ENVIRONMENT=.venv-langmem uv sync --extra langmem
UV_PROJECT_ENVIRONMENT=.venv-graphiti uv sync --extra graphiti
```

Graphiti needs FalkorDB 6.0.2: `docker run -d -p 127.0.0.1:6390:6379 falkordb/falkordb:6.0.2`. Embeddings use `nomic-embed-text` through a local Ollama.

## What is frozen

`protocol/v1.json` holds a hash of the metric code, the decider prompt and configs, the adapter configs and product pins, the memory-side model, the separation rule, the generator and the 300 table cases. The commit date of the freeze is the preregistration date (`protocol/PREREGISTRATION.md`). A change makes protocol v2.

## Layout

| Path | What |
| --- | --- |
| `src/memory_bench/` | engine, adapters, deciders, scoring, site, submission tools |
| `protocol/` | frozen protocol and preregistration |
| `data/` | development and table cases |
| `results/table/v1/` | released files of the v1 table runs |
| `docs/` | design files for each part |
| `site/` | site copy, brand and media |

## Licence

Code under Apache-2.0 (`LICENSE`). Data, cases and result files under CC BY 4.0 (`LICENSE-DATA`).
