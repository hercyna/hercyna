# Decider layer and model gateway (design, fixed on Day 2)

## 1. What a decider is

A decider turns one `DecisionInput` (`deciders/base.py`) into one action name. It never sees a `Case`.
The prompt is the lifted LENS prompt, unchanged: system text, context line, "Records:", the query, the policy text and the reply rule, version `v1` (`deciders/lifted.py`: `SYSTEM`, `CONTEXT`, `POLICY`, `REPLY`, `REPLY_FORMAT`, `REMINDER`, `parse_reply`). No string in that file changes.

A reply that does not parse gets one retry with `REMINDER` appended. A second failure is `PARSE_ERROR`.

## 2. Decider config

One JSON file per decider under `configs/deciders/`. Loaded by `load_decider(path_or_dict, gateway)`.

| Key | Meaning |
| --- | --- |
| `id` | name used in metric rows, for example `haiku-5-5` |
| `transport` | `anthropic_command`, `openai_compat` or `ollama` |
| `model` | model id sent to the endpoint |
| `prompt_version` | `v1` |
| `params` | transport parameters: `max_tokens`, `temperature`, `effort`, `thinking`, `num_ctx` |
| `base_url` | for `openai_compat` and `ollama` |
| `command` | for `anthropic_command`: the wrapper argv, default `["anthropic-api"]` |
| `env_unset` | for `anthropic_command`, optional: names of environment variables removed from the wrapper's environment, for example `["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]`; the bench's own environment is unchanged and the values are never read |
| `key` | for `openai_compat`: `{"env": "NAME"}` or `{"command": [argv]}` or absent for a local endpoint |
| `price` | `{"input_per_m": x, "output_per_m": y, "as_of": "date", "source": "text"}` or null when unknown |
| `timeout_s`, `retries` | call limits |

Three configs ship on Day 2:
- `haiku-5-5.json`: `anthropic_command`, model `claude-haiku-5-5`, `max_tokens` 6000, `effort` `high`, no `thinking` field (adaptive), structured output with the reply schema. This is the LENS headline decider. Price 0.10 and 0.50 dollars per million tokens for live calls, recorded 2026-10-16 as twice the batch price.
- `gpt-oss-20b-local.json`: `ollama`, model `gpt-oss:20b`, `temperature` 0, `num_ctx` 32768, price 0.
- `openai-compat.example.json`: a template with a placeholder model and `{"env": "BENCH_API_KEY"}`.

## 3. Transports

| Transport | Call | Seed rule | Usage |
| --- | --- | --- | --- |
| `anthropic_command` | runs `<command> POST /v1/messages --body - --timeout S` with the JSON body on stdin and reads the JSON reply on stdout. Exit 0 is success. Body as the LENS code built it (`batch.decide_params` in the source export): `model`, `max_tokens`, `system`, `messages`, `output_config` with `effort` and the JSON schema format | none. A repeat is a fresh call | `usage.input_tokens`, `usage.output_tokens` |
| `openai_compat` | `POST {base_url}/chat/completions` with `urllib`. `response_format` JSON schema first; on a 400 that names `response_format`, once without it | `seed = repeat + 1`. The xAI endpoint answers 400 to seed 0, found on the first live call, 2026-11-19 | `usage.prompt_tokens`; output = the larger of `usage.completion_tokens` and `usage.total_tokens - usage.prompt_tokens`, so reasoning tokens reported outside `completion_tokens` are metered as output |
| `ollama` | the lifted `OllamaTransport` on `/api/chat`, `think` false, `format` = the reply schema, `options.num_ctx` always set | `options.seed = repeat` | `prompt_eval_count`, `eval_count` |

Keys:
- A key value is never logged, printed, written to a file, put in an exception message or returned from a function other than the one that sends the request.
- `{"command": [...]}` is run by the transport just before the call and its stdout is used as the key. Tests use a fake command.
- The `anthropic_command` transport never handles a key. The wrapper holds it.

## 4. Gateway (`memory_bench.gateway`)

Every model call goes through one `Gateway` object.

- **Cache.** The lifted `CallCache` (sqlite). Key = sha256 of decider id, model, params, prompt version, messages and repeat index. The repeat index is in the key, so repeats are separate calls and a resumed run costs nothing. The cached value holds the raw reply and its token usage. Default path `results/cache/calls.sqlite`, override per run.
- **Meter.** Per call: input tokens, output tokens, dollars from the decider's `price`, cached or not, seconds. When an endpoint returns no usage, tokens are estimated as `ceil(chars / 2.2)` and the call is marked `estimated`.
- **Cap.** Before a call that is not cached, if the dollars spent in this run plus a worst-case price for the next call (input estimate plus `max_tokens` output) would pass `cap_usd`, raise `CapReached`. The engine then writes manifest state `stopped_at_cap`. The run can be resumed with a higher cap. A cap of 0 with a priced decider refuses every live call. A decider with a null price needs `--allow-unpriced`. With the product proxy in the run, the gateway's `extra_spent` (a callable, default returns 0) adds the memory side's spent and reserved dollars to the cap check, so one cap covers both sides (`docs/product-proxy.md` section 5).
- **Concurrency.** With `--parallel N` (1 to 8) one lock guards the cache, meter and call log, and the transport call runs outside it. A live call reserves its worst-case dollars before it starts. The cap check counts spent plus reserved dollars, and the reservation is released when the call ends. A request already in flight is not sent twice: the second caller waits and reads it from the cache. `calls.jsonl` lines are then in completion order. `metrics.jsonl` and `summary.json` do not change.
- **Call log.** `calls.jsonl` in the run folder: time, decider, case id, kind, repeat, request sha256, reply sha256, tokens, dollars, cached, estimated, seconds, retries. No text and no key.
- `spend.json` is written from the meter: `cap_usd`, `total_usd`, `calls`, `cached_calls`, `input_tokens`, `output_tokens`, `by_stage` (gate, floor, view), `by_decider`, `by_adapter`.

## 5. Validity run

`bench validity --decider CONFIG [--n-per-mechanism 10] --out DIR --cap-usd X`
- Sample: the first `n` development cases of each mechanism, in file order. 60 cases by default.
- One gate decision per case on the full history.
- Output `validity.json`: accuracy overall and per mechanism, the confusion table (right action against chosen action), parse errors, the gate verdict (0.90 overall, 0.80 per mechanism), tokens and dollars, seconds per call.

## 6. Dry run

`bench dryrun` takes the same arguments as `bench run` and makes no call. It builds every prompt, counts bench tokens, and prints likely and upper-bound dollars per stage and in total, using `price` and `max_tokens`. Likely output tokens per call come from the config key `assumed_output_tokens` (default 1500).
