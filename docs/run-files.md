# Run files and the adapter contract (design, fixed on Day 1)

## 1. Adapter contract (`memory_bench.adapters.base`)

```python
@dataclass(frozen=True)
class Record:
    id: str      # opaque record id, as it appears at the start of the rendered line
    date: str    # ISO date
    text: str    # the full rendered line, exactly as the full history shows it

@dataclass
class Usage:
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embed_calls: int = 0

@dataclass
class Retrieved:
    text: str                                   # what the decider will read as its records
    record_ids: list[str] = field(default_factory=list)   # ids of records quoted verbatim; empty for rewritten memory
    units: list[str] = field(default_factory=list)        # the text units in order (lines or sentences)
    usage: Usage = field(default_factory=Usage)

class MemoryAdapter(Protocol):
    name: str               # "bm25", "mem0"
    product_version: str    # pinned product version; "builtin" for baselines
    config_label: str       # "default" or "vendor-tuned"
    reference: bool         # True only for `full`: the budget is ignored

    def reset(self, namespace: str) -> None: ...
    def ingest(self, records: Sequence[Record]) -> None: ...
    def retrieve(self, query: str, budget_tokens: int) -> Retrieved: ...
    def close(self) -> None: ...
```

Rules:
- `records_of(case) -> list[Record]` is the only bridge from a case to an adapter. It returns the rendered lines in history order. Nothing else from the case reaches an adapter: not `a_star`, `mechanism`, `d_star`, `deleted_ids`, `knobs`, `cls` or the event objects.
- The query is `case.query_text`, a string.
- One `reset(namespace)` per case. The namespace is the case id. No state crosses cases.
- `enforce_budget(retrieved, budget_tokens) -> Retrieved` is applied by the engine, not the adapter. It cuts `text` with the shared rule (`truncate_tokens`, bench tokens = `ceil(chars / 2.2)`). For a `reference` adapter it returns the input unchanged.
- `count_tokens(units)` is the shared count for `view_tokens`.
- Adapters are built by a registry: `get_adapter(name, **config)`. Baselines: `full`, `bm25`, `recent`.

## 2. `metrics.jsonl`: one row per decision call

Canonical JSON per line: sorted keys, compact separators, ASCII. No view, note or prompt text.

| Field | Type | Meaning |
| --- | --- | --- |
| `case_id` | str | |
| `track` | str | `account_review` for the lifted generator |
| `mechanism` | str | M0, M1, M2, M3, U1, U2 |
| `a_star` | str | the right action |
| `decider` | str | decider id, for example `mock-oracle` |
| `kind` | str | `gate`, `floor` or `view` |
| `system` | str | `full` for gate and floor rows; the adapter name for view rows |
| `config_label` | str | `default` or `vendor-tuned` |
| `budget` | int or null | null for gate and floor rows |
| `repeat` | int | 0 for the gate row; 1..R for floor and view rows |
| `action` | str | one of the five actions or `PARSE_ERROR` |
| `view_tokens` | int | shared token count of the text the decider read |
| `view_units` | int | number of units |
| `view_sha256` | str | sha256 of the text the decider read |

Row key (unique): `case_id, decider, kind, system, config_label, budget, repeat`. A resumed run skips keys already present.

A **cell** is `(decider, system, config_label, budget)`.

## 3. Other run files, under `results/runs/<run-id>/`

- `manifest.json`: `run_id`, `created`, `state` (`running`, `complete`, `failed`), `grade` (`exploratory` or `table`), `protocol` (`{version, sha256}` or null), `cases` (`{path, sha256, n}`), `deciders` (list of `{id, model, params}`), `adapters` (list of `{name, product_version, config_label, config_sha256}`), `budgets`, `repeats`, `code` (`{version, git_commit}`), `memory_side` (`{id, chat_model, embeddings_model, config_sha256}` when the run used the product proxy, else null), `files` (`{name: sha256}`, filled when the run completes; includes `calls.jsonl` and `memory_calls.jsonl` when the run wrote them).
- `summary.json`: the scorer's output (section 4).
- `spend.json`: `cap_usd`, `total_usd`, `calls`, `input_tokens`, `output_tokens`, `by_stage`, `by_decider`, `by_adapter`. All zero for a mock run. With the product proxy (`docs/product-proxy.md`): `by_adapter[name].memory_side` holds the proxy's measured `calls`, `cached_calls`, `embed_calls`, `input_tokens`, `output_tokens`, `usd`; `total_usd` includes the memory side; `memory_side_usd` is its total.
- `memory_calls.jsonl`: written only by a run with the product proxy. One line per memory-side model call (`docs/product-proxy.md` section 3 rule 8). No text. Part of the released, text-free set.
- `health.json`: per adapter: `name`, `product_version`, `ok`, `cases_done`, `errors`, `last_error`, `ingest_seconds_mean`, `retrieve_seconds_mean`, `last_ok_at`, `product_warnings` (`total` and `by_logger`: WARNING-and-above log records the product emitted during its calls, counted by logger name, no text; a product that drops a memory because the model's reply did not parse says so only there).
- `timings.jsonl`: one row per adapter call with seconds. Kept out of `metrics.jsonl` so the metric file is deterministic. A product adapter (`needs_proxy`) runs in the view stage (`docs/view-stage.md`): one `reset`, one `ingest` and one `retrieve` row per case, with `budget` null, and `warnings`, the WARNING-and-above records attributed to that call. Since Day 14 a row whose `warnings` is not zero also has `warnings_by_logger` (counts by logger name, no text), so `bench health rebuild` can split the total. A record is attributed to the case running on the thread that emitted it. A record from another thread goes to the one case in flight; with several in flight it is counted in `health.json` under `unattributed_warnings` (`total`, `by_logger`), a key written only when that count is not zero.
- `health.json` rebuilt (Day 14, review B3). The engine writes `health.json` from the attempt in memory, so a resumed run's file covers only its last attempt. `bench health rebuild RUN` recomputes it from every attempt: `cases_done` from `metrics.jsonl` (budgets and repeats from the manifest), the two seconds means and `last_ok_at` from every ingest and retrieve row of `timings.jsonl`, and `product_warnings.total` as the sum of the rows' `warnings`. `by_logger` sums the rows' `warnings_by_logger`. Rows written before Day 14 carry only a total: their split is taken from the stored view of the same attempt in `private/views.jsonl` (counts only), and the rest goes under the key `_unknown`. No row records errors, so `errors`, `last_error` and `unattributed_warnings` are kept from the existing file. A product entry adds `attempts` (`items`: reset rows, `stored_views`). A top-level `rebuilt` (`from`, `timing_rows`) marks the file. The manifest's `files` hash for `health.json` is updated, as `bench report` does for the summary.
- `view-audit.json` (Day 14, review B3; `bench audit views RUN`, schema `memory_bench.view_audit/1`, written only on request). For each stored view (the newest line per key in `private/views.jsonl`) the ingest window runs from `at - ingest_seconds - retrieve_seconds - margin` (margin 5 seconds) to `at`. A memory-side call in `memory_calls.jsonl` of the same case, system and config label with a status other than 200 whose `at` falls inside the window marks the view. Counts and ids only: `margin_seconds`, `memory_calls`, `views_checked`, `views_with_failed_call`, `failed_calls`, `failed_calls_by_status` (status as a string, `none` for no status), `cases_failed_without_view`, and per `system/config_label` in `systems`: the same counts, `case_ids` of the marked views, `failed_calls_in_window`, `cases_failed_without_view` (ids) and `views_unchecked` (no readable time). Text-free and in the released set; not in the manifest's `files`, like `stability.json`.
- `private/views.jsonl`: written only by a run with a product adapter. One line per `(case_id, system, config_label)`: the product's view before the budget cut (`text`, `units`, `record_ids`), its `usage`, `ingest_seconds`, `retrieve_seconds`, `warnings`, `at`, and the keys that decide reuse (`config_sha256`, `memory_side_id`, `product_version`). The decision loop cuts each budget from this one view, and a resumed run reuses it instead of ingesting again. It holds view text on purpose, so it is never released: it is not in the manifest's `files`, not in the released set, and the text-free scanner does not look there. Views go only into a vendor's own pack (charter section 5 rule 8).

## 4. Scoring (`memory_bench.scoring`), from charter section 4

Input: metric rows. Output: `summary.json`.

- Case order = order of first appearance of `case_id` in the rows. The bootstrap indexes cases in that order.
- Per decider: `G` = cases whose gate row has `action == a_star`. Validity gate on gate rows: accuracy at least 0.90 overall and at least 0.80 per mechanism.
- committed = action not in (`REQUEST_INFO`, `PARSE_ERROR`).
- Per cell, over cases in `G` and that cell's view rows:
  - `wrong_commit` = rows with a committed action that is not `a_star`, divided by the number of gated (case, repeat) slots.
  - `abstention` = rows with `action == REQUEST_INFO` and `a_star != REQUEST_INFO`, same denominator.
  - `parse_error` = rows with `PARSE_ERROR`, same denominator.
  - `harmful` = rows with `action != a_star`, same denominator. It equals the sum of the three above.
  - `right_action` = rows with `action == a_star`, same denominator.
  - `false_closure_rate` = committed rows with `action != a_star` divided by committed rows (gated).
- The floor for a decider is the same set of rates computed on its `floor` rows. `headline = wrong_commit - floor.wrong_commit`. `abstention_above_floor` likewise. When a decider has no floor rows, `floor` and `headline` are null.
- Intervals: the LENS bootstrap, unchanged. `Boot(n = number of cases, reps = 1000, seed)` seeded with `random.Random(f"boot|{seed}")`, one shared list of resamples for every metric, case-level features summed, each rate a ratio of resampled sums, percentile 95 percent interval with linear interpolation, values rounded to six decimals. With one view row per case and cell this must reproduce the LENS `rate_gated` values and intervals exactly.
- Paired differences (headline, product against product) are computed on the same resamples.

## 5. Grade, protocol and the two power-note files (added Day 9, from `docs/freeze.md`)

Manifest keys, in detail:
- `grade`: `exploratory` for every run without `bench run --protocol PATH`. With the flag, `table` when the four checks of `docs/freeze.md` section 4 pass and step 2 is frozen; `exploratory` when they pass and step 2 is not frozen yet. A failed check stops the run with exit 2 before any file or model call. The `grade` value is never downgraded silently.
- `protocol`: null without the flag. With it: `{path, version, protocol_sha256, steps}`, where `steps` lists the frozen step numbers (`["1"]` or `["1", "2"]`). It also holds `exploratory_reason` when the run is exploratory under a protocol. This shape replaces the `{version, sha256}` placeholder in section 3.
- A resume keeps the `grade` and the `protocol_sha256` of the first attempt, and it runs the checks again.

`summary.json` gets a top-level `grade` key, copied from the manifest, also when `bench report` rebuilds it. Apart from this key a run without the flag writes the same bytes as before.

Two more files, written only on request and never by `bench run`. Both are text-free (tokens, counts and rates only) and belong to the released set that the text-free scanner checks.
- `stability.json` (`bench stability RUN`, schema `memory_bench.stability/1`). Per decider: `n_cases`, `n_gated` and `systems`. Per system (`full` read from its floor rows, other systems from their view rows; key `name` for a default config, `name/label` otherwise, `@budget` added when a system has several budgets): `gated_cases`, `identical_cases` (the same action on every repeat), `identical_share`, `repeats`, `later_repeats`, `later_differ` (repeats after the first whose action differs from the first) and `later_differ_share`.
- `power.json` (`bench power RUN [--pairs K] [--power P]`, schema `memory_bench.power/1`). It uses a normal approximation over gated cases. A case's headline is the mean wrong-commit over its repeats on the view minus the same mean on the floor. SE is the population standard deviation of the per-case values over sqrt(n). MDE = (z(1 - a/2) + z(power)) * SE, at `alpha` and at `alpha_holm` = alpha / K. Per decider: `systems` (`n`, `mean`, `se`, `mde`, `mde_holm`; null without floor rows) and `pairs` keyed `a:b` (the same fields for a minus b, plus `a`, `b`). Rates are 0..1, rounded to six decimals.
