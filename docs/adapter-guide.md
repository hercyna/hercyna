# Adapter guide

This guide states the facts an adapter author needs.

## 1. What an adapter is

An adapter is the only code between a memory product and the bench. It implements four methods (`docs/run-files.md` section 1):

```python
def reset(self, namespace: str) -> None        # an empty, isolated store for one case
def ingest(self, records: Sequence[Record])    # dated records in history order
def retrieve(self, query: str, budget_tokens: int) -> Retrieved
def close(self) -> None
```

It sees rendered records and the query string. It never sees a `Case`, the right action, the mechanism or planted ids. `tests/test_blind_records.py` enforces this.

## 2. The rules every product adapter follows

| # | Rule | Reason |
| --- | --- | --- |
| 1 | The default config is the product's own quickstart at the pinned version | A vendor cannot say the product was tuned badly. A vendor-tuned config is a second row |
| 2 | A setting departs from the quickstart only when the bench contract forces it. Each departure has a comment in the adapter's `DEFAULTS` | The same |
| 3 | Chat and embedding calls go through the product proxy (`docs/product-proxy.md`). The adapter builds its client from `scope.base_url` and `scope.api_key` | Same model for every product. Cost is measured |
| 4 | The adapter sends placeholder model names. The proxy pins the real ones | A product's name heuristics do not fire on our model |
| 5 | One scope and one local store per case. `reset` builds both, `close` removes both | No memory crosses cases |
| 6 | Records go in as rendered text, unchanged. The line already starts with the record id and date | The product gets what the full history shows |
| 7 | `retrieve` asks the product for more than fits the budget. The engine cuts to the budget | A product default of 20 results cannot fill 1,000 tokens. Under-filling would punish a default, not a product |
| 8 | `retrieve` returns the product's own text in the product's own order. The adapter adds no prefix, summary or re-ranking | The view is the product's output |
| 9 | `Retrieved.usage` comes from `scope.usage()` | Usage is measured, never self-reported |
| 10 | Product errors are not swallowed. The engine records them in `health.json` | A silent failure would score as a bad memory |
| 11 | The product package is imported on first use and pinned in an optional extra | The core keeps no runtime dependency |
| 12 | Telemetry is switched off before import | No run data leaves the machine |
| 13 | The chunking rule (`records_per_add` or its equal) is a config value, hashed into the manifest, and its effect is measured on five cases before it is fixed | Ingest cost is the largest unknown |

## 3. Per product

| Product | Pin | Ingest path | Chat calls per case | Model needs | Store |
| --- | --- | --- | --- | --- | --- |
| Mem0 | `mem0ai==2.2.1` | one `add` per record, `infer` on | about 400 | JSON mode | Qdrant on disk, one temp folder per case |
| LangMem | `langmem==0.0.30` | memory store manager, 25 records per chunk | about 16 | tool calling | `InMemoryStore`, one per case |
| Graphiti | `graphiti-core[falkordb]==0.31.2` | `add_episode` per record, `group_id` per case | 2,400 to 4,000 (read from source, not measured) | JSON-schema output | FalkorDB in Docker |
| Letta | `letta[sqlite]==0.16.8` (end-of-life) | `passages.create` per record | 0 | none on this path | SQLite |

Sources: `docs/research/*-2026-11-19.md`.

## 4. How the engine calls a product adapter

Once per case, in the view stage, at the largest budget, on its own thread's instance: see `docs/view-stage.md`.

## 5. Checklist for a new adapter

1. Read the product's current docs for the pinned version. Write the brief under `docs/research/`.
2. Write the adapter with a `DEFAULTS` dict and a comment on every departure from the quickstart.
3. Pass the shared adapter suite and the blind-record test.
4. Run five cases. Record seconds, calls, tokens and dollars per case, and every product error.
5. Run twenty cases for a first reading. Twenty cases are a smoke test, not a result.
