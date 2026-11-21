# Product proxy: the memory-side gateway (design, fixed on 2026-11-19)

## 1. Why

Every product must call the same chat model and the same embedding model (charter 6.1), and every model call must be metered, cached and capped (charter 6). Products make their calls from inside their own SDKs. The one setting every SDK has is a `base_url`. So the bench runs one local OpenAI-compatible endpoint and every product points at it.

The decider `Gateway` (`docs/deciders.md` section 4) stays as it is. The proxy is its sibling for memory-side calls.

## 2. Shape

`memory_bench.gateway.proxy`: a `ThreadingHTTPServer` on `127.0.0.1` and a free port, run in a thread inside the bench process. Standard library only.

```python
class ProductProxy:
    def __init__(self, memory_side: dict, *, run_dir: Path | None, cache_path: Path | None,
                 cap_usd: float = 0.0, spent_elsewhere: Callable[[], float] = lambda: 0.0,
                 opener=None, runner=None, clock=time.monotonic): ...
    def start(self) -> None            # binds, starts the thread
    def stop(self) -> None
    def scope(self, adapter: str, config_label: str, case_id: str) -> "Scope"   # registers and returns a scope
    def spent_usd(self) -> float       # live dollars this run, memory side only
    def reserved_usd(self) -> float
    def spend(self) -> dict            # by adapter: calls, cached_calls, input_tokens, output_tokens, embed_calls, usd
    cap_reached: bool

class Scope:
    base_url: str                      # http://127.0.0.1:<port>/s/<scope-id>/v1
    api_key: str                       # a fixed dummy, "bench-local"
    def phase(self, name: str) -> None # "ingest" or "retrieve"; default "ingest"
    def usage(self) -> Usage           # adapters.base.Usage measured by the proxy for this scope: model_calls, input_tokens, output_tokens, embed_calls
    def close(self) -> None            # unregisters; later calls on this scope get 404
```

A scope id is `<adapter>.<config_label>.<case_id>` made URL-safe, plus a short random suffix. An adapter asks for one scope per case in `reset()`, builds its SDK client with `scope.base_url`, and reports `scope.usage()` in `Retrieved.usage`. Usage is measured, never self-reported.

## 3. Routes

| Route | Behaviour |
| --- | --- |
| `POST /s/<scope>/v1/chat/completions` | forward to the chat upstream |
| `POST /s/<scope>/v1/embeddings` | forward to the embeddings upstream |
| `GET /s/<scope>/v1/models` | `{"object": "list", "data": [...]}` with the two configured model ids |
| `GET /healthz` | `{"ok": true}` |
| anything else, or an unknown or closed scope | 404 with a JSON error that names the path |

Rules for a forwarded call:
1. **The model is pinned.** Whatever `model` the product sends, the proxy replaces it with the configured upstream model. The requested name is logged. A product's default model name never reaches the upstream.
2. **The rest of the body passes through unchanged**, with one exception: `"stream": true` is refused with 400 and the message `streaming is not supported by the bench proxy; set stream to false`. Adapters turn streaming off.
3. **Cache.** Key = sha256 of the upstream id, the route and the canonical JSON body after the model rewrite. A hit returns the stored upstream response body with status 200 and makes no upstream call. The cache is the shared sqlite file (`results/cache/calls.sqlite` by default), under a lock. Only 2xx responses are stored.
4. **Meter.** Chat: input = `usage.prompt_tokens`; output = the larger of `usage.completion_tokens` and `usage.total_tokens - usage.prompt_tokens` (reasoning tokens count as output). Embeddings: input = `usage.prompt_tokens` or `usage.total_tokens`, output 0. Missing usage is estimated as `ceil(chars / 2.2)` and the call is marked `estimated`. Dollars come from the upstream's `price`.
5. **Cap.** Before a live call: worst case = input estimate plus `max_tokens` or `max_completion_tokens` from the body (else the config's `assumed_max_output_tokens`, default 4000) at the output price. If `spent_elsewhere() + spent + reserved + worst > cap_usd`, nothing is sent: the proxy answers 400 with error type `bench_cap_reached` and sets `cap_reached`. The worst case is reserved while the call is in flight. A cap of 0 with a priced upstream refuses every live call.
6. **Upstream errors.** 429 and 5xx are retried with backoff, `retries` times. The final status and the upstream's JSON error body pass through to the product. No header is ever copied into a body, a log or an exception.
7. **Keys.** The upstream key comes from the config's `key` (`{"env": NAME}`, `{"command": [argv]}` or absent), read just before each live call exactly as `OpenAICompatTransport._headers` does. The dummy `api_key` the product sends is ignored. A key value never reaches a log line, a file, an exception or a response.
8. **Log.** One line per call in `<run_dir>/memory_calls.jsonl`: `at`, `adapter`, `config_label`, `case_id`, `phase`, `route` (`chat` or `embeddings`), `upstream`, `model`, `requested_model`, `request_sha256`, `reply_sha256`, `input_tokens`, `output_tokens`, `usd`, `cached`, `estimated`, `status`, `seconds`, `retries`. No text. The file joins the released, text-free set.

## 4. Memory-side config

One JSON file under `configs/memory-side/`:

```json
{
  "id": "grok-4-20-nr-mem",
  "chat": {"base_url": "https://api.x.ai/v1", "model": "grok-4.20-0309-non-reasoning",
           "key": {"command": ["/usr/bin/security", "find-generic-password", "-s", "xai-headless", "-w"]},
           "price": {"input_per_m": 1.25, "output_per_m": 2.50, "as_of": "2026-11-19", "source": "docs.x.ai/developers/pricing"},
           "assumed_max_output_tokens": 4000},
  "embeddings": {"base_url": "http://127.0.0.1:11434/v1", "model": "nomic-embed-text", "dims": 768,
                 "price": {"input_per_m": 0, "output_per_m": 0, "as_of": "2026-11-19", "source": "local Ollama"}},
  "timeout_s": 300,
  "retries": 3
}
```

A null `price` needs `--allow-unpriced`, as for deciders.

## 5. Engine and CLI

- `RunConfig.memory_side: str | dict | None`. CLI: `bench run --memory-side CONFIG` and the same flag on `quickstart` is not added.
- An adapter class that needs the proxy sets `needs_proxy = True` and takes `proxy: ProductProxy` as a constructor keyword. The engine starts one proxy per run when at least one such adapter is requested, passes it in, and stops it at the end. A product adapter without `--memory-side` is an error before any call.
- **One cap.** The decider `Gateway` gets an optional `extra_spent: Callable[[], float]` (default returns 0) that its cap check adds to spent dollars. The engine wires `gateway.extra_spent = proxy.spent_usd + proxy.reserved_usd` and `proxy.spent_elsewhere = gateway.spent_usd + gateway.reserved_usd`. After each adapter call the engine checks `proxy.cap_reached` and stops the run with state `stopped_at_cap`.
- `spend.json`: `by_adapter[name].memory_side` holds the proxy's measured `calls`, `cached_calls`, `embed_calls`, `input_tokens`, `output_tokens`, `usd`. `total_usd` includes the memory side. New top-level key `memory_side_usd`.
- `manifest.json`: new key `memory_side`: `{id, chat_model, embeddings_model, config_sha256}` or null.
- `bench proxy serve --memory-side CONFIG [--port N] [--cap-usd X] [--log DIR] [--cache PATH]`: runs the proxy alone for adapter development and prints a `base_url` for one scope named `dev`.
- `bench dryrun` with a product adapter prints one extra line: `memory side: not estimated, run a five-case probe`.

## 6. Tests (no network)

A fake upstream (`ThreadingHTTPServer` in the test) answers chat and embeddings with canned bodies and counts calls.

- model rewrite and requested-model logging; body otherwise unchanged
- cache hit makes no upstream call and logs `cached: true`
- meter: tokens, dollars, reasoning tokens outside `completion_tokens`, estimated usage
- cap: refusal with `bench_cap_reached`, nothing sent, `cap_reached` set; `spent_elsewhere` counted
- scopes: attribution by adapter and case, `phase`, `usage()`, unknown and closed scope 404
- stream refusal; `/models`; `/healthz`
- retry on 429 then success; final error passes through
- the key from a fake key command reaches the upstream `Authorization` header and appears in no log line, file or exception
- `memory_calls.jsonl` passes the text-free scanner
- engine: a test-only product adapter that calls chat and embeddings through its scope runs end to end in `run(...)`; `spend.json` and the manifest carry the memory side; the one-cap rule stops the run
