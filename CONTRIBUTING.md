# Contributing to Hercyna

Hercyna scores agent memory products by the actions an agent takes from what the memory returns (right, withheld, or wrong), measured against each decider's own full-history noise floor.

You can add and verify a new adapter inside its own folder (`adapters/<name>/adapter.py`) without editing any file in `src/`, `configs/`, or `protocol/`.

## Submitting a New Memory Adapter

### 1. Install and check your environment (zero cost)

```bash
uv sync
uv run bench quickstart --mock --accept-terms
```

Review `TERMS.md` and `LEGAL.md` before running or submitting (`--accept-terms` or interactive `accept` once per version).

### 2. Create your adapter folder (`adapters/<name>/adapter.py`)

Create `adapters/<name>/adapter.py` (for example `adapters/my_store/adapter.py`). The CLI discovers any adapter under `./adapters/<name>/adapter.py` automatically by `<name>`. No edits to `src/memory_bench/` are needed.

Every adapter implements the four contract methods and five class attributes (`docs/adapter-guide.md`):

- `name`: short token matching `[A-Za-z0-9_.:/@+-]{1,64}` (must match your folder `<name>`)
- `product_version`: string version (not `"builtin"`, e.g. `"0.1.0"`)
- `config_label`: short config slug (e.g. `"default"`)
- `reference`: `False` (`True` is reserved for `full`)
- `needs_proxy`: `False` for pure local/algorithmic stores; `True` if the adapter calls an LLM or embedder via `proxy`

Only `Record` sequences (`id`, `date`, `text`) and the query string may enter your adapter. Never read `Case` metadata, `a_star`, `d_star`, or ground-truth fields. Always return verbatim `Record.id` and `Record.text` strings in `Retrieved(text=..., record_ids=..., units=...)` with `"\n".join(units) == text` and ascending history order.

Minimal zero-cost working template (`adapters/my_store/adapter.py`):

```python
from __future__ import annotations

from collections.abc import Sequence

from memory_bench.adapters.base import MemoryAdapter, Record, Retrieved


class MyStoreAdapter(MemoryAdapter):
    name = "my_store"
    product_version = "0.1.0"
    config_label = "default"
    reference = False
    needs_proxy = False

    def __init__(self) -> None:
        self._order: list[str] = []
        self._docs: dict[str, str] = {}

    def reset(self, namespace: str) -> None:
        self._order = []
        self._docs = {}

    def ingest(self, records: Sequence[Record]) -> None:
        for r in records:
            self._order.append(r.id)
            self._docs[r.id] = r.text

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        terms = {w.strip(".,?!:;").lower() for w in query.split() if w.strip(".,?!:;")}
        rank = {rid: i for i, rid in enumerate(self._order)}
        scored = [
            (sum(1 for t in terms if t in self._docs[rid].lower()), rank[rid], rid)
            for rid in self._order
        ]
        hits = [x for x in scored if x[0] > 0]
        hits.sort(key=lambda x: (-x[0], x[1]))
        chosen = sorted([rid for _, _, rid in hits[:25]], key=rank.__getitem__)
        units = [self._docs[rid] for rid in chosen]
        return Retrieved(text="\n".join(units), record_ids=chosen, units=units)

    def close(self) -> None:
        self._order = []
        self._docs = {}
```

If your product calls an LLM or embedder (`needs_proxy = True`), accept `proxy=None, config_label="default", **config` in `__init__` and point its client at `scope.base_url` and `scope.api_key` (`docs/product-proxy.md`, reference adapters in `src/memory_bench/adapters/mem0.py`, `langmem.py`, `graphiti.py`).

### 3. Smoke-test locally at zero cost, pack, verify, and preview the site

Run on 20 cases with the mock oracle (no key, $0), pack as exploratory, verify, and build the site preview:

```bash
uv run bench run --mock --n 20 --cases data/v2/chat_agent/table_cases.jsonl \
  --adapters my_store --budgets 1000 --repeats 3 --out results/runs/my_store-mock --accept-terms
uv run bench submit pack results/runs/my_store-mock --out submissions --exploratory --accept-terms
uv run bench verify submissions/my_store/<YYYY-MM-DD>-default --accept-terms
uv run bench site build --out site/dist --accept-terms
```

Notes:
- Outside runs are exploratory (do NOT pass `--protocol protocol/v2.json` to `bench run`, because table protocol checks allow only the frozen reference systems and deciders).
- When verifying a partial exploratory subset (like `--n 20`), omit `--cases` on `bench verify`; pass `--cases data/v2/chat_agent/table_cases.jsonl` only when your run covers all 300 cases in that file.
- Verify that `bench verify` exits `0` (`verify: pass, exploratory ...`) and that `site/dist/index.html` renders your `my_store` row labelled `exploratory`.

### 4. Price and run with a real decider (optional, paid)

When running a real model decider (and `--memory-side` if `needs_proxy = True`), price first with `bench dryrun --decider ...` before setting `--cap-usd`:

```bash
uv run bench dryrun --decider configs/v2/deciders/haiku-5-5.json \
  --cases data/v2/chat_agent/table_cases.jsonl --adapters my_store --n 20
uv run bench run --decider configs/v2/deciders/haiku-5-5.json \
  --cases data/v2/chat_agent/table_cases.jsonl --adapters my_store \
  --budgets 1000 --repeats 3 --cap-usd 5 --out results/runs/my_store-haiku
uv run bench submit pack results/runs/my_store-haiku --out submissions --exploratory
uv run bench verify submissions/my_store/<YYYY-MM-DD>-default \
  --cases data/v2/chat_agent/table_cases.jsonl
```

### 5. Open a pull request

- Before packing, verify zero private or text leaks: `private/` files (`private/views.jsonl`) and raw record text must never enter `submissions/`.
- Fill `submitter`, `organisation`, and `adapter_source` in `submissions/my_store/<YYYY-MM-DD>-default/submission.json`.
- Paste the final line of `bench verify` (`verify: pass, ...`) into the PR checklist (`.github/pull_request_template.md`).
- Confirm `uv run bench freeze --check --protocol protocol/v2.json` passes untouched and tests pass (`uv run pytest -q`).
- Do not include em dashes or employer/client names in public copy.
