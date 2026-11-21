# View stage: ingest once, in parallel, and keep the views (design, fixed on 2026-11-19)

## 1. Why

Measured on live cases (2026-11-19): product ingest takes 61 s (Mem0), 128 s (Graphiti) and 310 s (LangMem) per case. Three hundred cases, one at a time, is 5 to 26 hours per product. Three things are wrong with the engine as it is:

1. It ingests one case at a time.
2. `_fetch_view` runs `reset`, `ingest` and `retrieve` once per budget, so a second budget would ingest twice.
3. A resumed run ingests again. Where a product's calls are not byte-identical (Graphiti sends temperature 1 and uses a random group id), the proxy cache does not help and the money is spent twice.

Views must also be kept for the vendor packs (charter section 5 rule 8: views go only into a vendor's own pack).

## 2. Shape

Product adapters (`needs_proxy`) get a **view stage** that runs before the decision loop. Builtin adapters (`full`, `bm25`, `recent`) stay exactly as they are: computed inline, no store, so a run without a product adapter writes the same bytes as today and no new file.

### 2.1 The view store

`<run_dir>/private/views.jsonl`, append-only, one line per `(case_id, system, config_label)`:

```
{"case_id", "system", "config_label", "config_sha256", "memory_side_id", "product_version",
 "text", "units", "record_ids",
 "usage": {"model_calls", "input_tokens", "output_tokens", "embed_calls"},
 "ingest_seconds", "retrieve_seconds", "warnings", "at"}
```

- `text`, `units` and `record_ids` are the adapter's `Retrieved` **before** the budget cut. The engine cuts per budget at decision time with `enforce_budget`, as today.
- The adapter is called with `budget_tokens = max(budgets)`.
- On open the store loads the lines already there. A line is reused only when `config_sha256`, `memory_side_id` and `product_version` match the current run. Otherwise it is ignored and the view is computed again; the newest matching line for a key wins. A partial last line is skipped.
- Each line is written whole and flushed under a lock.
- `private/` is never released: it is not in the manifest's `files`, not in `RELEASED_FILES`, and the text-free scanner does not look there. It holds text on purpose.

### 2.2 The stage

`_view_stage(cases, product_specs, proxy, store, health, workers, ...)`:

- Work items are `(case, product adapter)` pairs with no reusable line in the store, in case order.
- A thread pool of `ingest_workers` threads (default 1, 1 to 8). **Each worker thread owns its own adapter instances**, built from the same specs with the same proxy. An adapter instance is never shared between threads. One item = `reset(case_id)`, `ingest(records)`, `retrieve(query, max(budgets))`, then one store line. `close()` each worker's adapters when the stage ends.
- With `ingest_workers = 1` the stage runs on the calling thread, in order.
- **Errors.** A product exception on one item is recorded with `health.error` and that pair has no view. The run goes on. Its cells are incomplete, exactly as a failed adapter call is treated today.
- **Cap.** When `proxy.cap_reached` is set or an item raises `CapReached`, no new item starts, items in flight finish or fail, their views are stored, and the run ends `stopped_at_cap`. A resume with a higher cap continues from the store.
- **Health and timings** are updated under a lock. `timings.jsonl` gets one `reset`, one `ingest` and one `retrieve` row per item (budget null for these rows). The warning counter attributes a log record to the item running on the thread that emitted it (`record.thread`), so parallel items do not count each other's warnings.
- Progress: after every finished item the stage rewrites `health.json` and `spend.json`, so a long ingest can be watched.

### 2.3 The decision loop

Unchanged, except that for a product adapter it reads the view from the store and applies `enforce_budget` per budget instead of calling the adapter. Row order, row keys, `metrics.jsonl` and `summary.json` formats do not change. `usage` totals for `spend.json` come from the stored lines used in this run (reused lines included).

## 3. CLI

`bench run --ingest-workers N` (default 1). `RunConfig.ingest_workers: int = 1`.

## 4. Docs

`docs/run-files.md` gains the `private/views.jsonl` paragraph and the timing-row note. `docs/adapter-guide.md` section 4 (the known engine limit) is replaced by one line that points here.

## 5. Tests (no network; the test-only product adapter in `tests/proxy_fakes.py`)

- one ingest and one retrieve per case with two budgets; both budgets' rows are cut from the same stored view
- `ingest_workers` 1 and 4 give byte-identical `metrics.jsonl` and `summary.json`; with 4, at least two items overlap in time; no adapter instance is used by two threads
- resume: a second `run` on the same folder makes no adapter call and no memory-side call, and writes the same metrics
- a store line with another `config_sha256` or `memory_side_id` is not reused
- a product error on one case: that case's product cells are missing, the other cases complete, `health.json` shows the error
- cap reached mid-stage: state `stopped_at_cap`, finished views are in the store, a resume with a higher cap completes without repeating them
- warnings are attributed to the right item under parallel work
- released files: nothing outside `private/` holds a view line; `scan_run` passes; the manifest's `files` has no `private` entry
- a run with only builtin adapters creates no `private/` folder and its files are byte-identical to the same run before this change (compare against the existing mock-run tests)
