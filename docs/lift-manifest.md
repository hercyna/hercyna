# Lift manifest

Source: the author's LENS replication bundle, commit `97a622f`, read from a `git archive` export of that commit. Only import paths changed in copied code. No string constant, prompt text, vocabulary, seeding rule or numeric constant was edited.

| Source path | Source commit | sha256 of source file | Target path | Change |
| --- | --- | --- | --- | --- |
| `src/lens/flip/schema.py` | `97a622f` | `fa4f1f395de27f29acf0b738362f6c43e6a973ac4379abad0c2cb2e09fee38f1` | `src/memory_bench/cases/schema.py` | copied, imports changed |
| `src/lens/flip/generator.py` | `97a622f` | `8878e1b4e1e854bfaa7bb82831dc5915a21592b2a00bbf2778c36a5a3076570a` | `src/memory_bench/cases/generator.py` | copied, imports changed |
| `src/lens/flip/render.py` | `97a622f` | `46567cebd52980297247f58f1c46ad315eac94ff3736427fe092bcb85dc6dfaf` | `src/memory_bench/cases/render.py` | copied, imports changed |
| `src/lens/flip/cache.py` | `97a622f` | `16cb0ecd0b195e41d8fc80492df04667ee151d0c87346bca5b83934db68203cd` | `src/memory_bench/gateway/cache.py` | copied, imports changed |
| `src/lens/flip/metrics.py` | `97a622f` | `6aa24134f5d177e89a5b7b1e6e16c9aebefbc672555b856787199dfcc5f55414` | `src/memory_bench/scoring/gates.py` | copied, imports changed |
| `src/lens/flip/decider.py` | `97a622f` | `118ab74f863ea5496a76c342a545a63a63b53d0b0b949e4489227537c13db944` | `src/memory_bench/deciders/lifted.py` | copied, imports changed |
| `src/lens/flip/backends.py` | `97a622f` | `d1a06b4f546684171bd0731b679e6d9ea14ca11d3ff8c08399c85b2bb2648dd1` | `src/memory_bench/deciders/backends.py` | copied, imports changed |
| `src/lens/flip/systems/base.py` | `97a622f` | `2bc3abdd00cc8a031c75ac6bcdad787b1fcd5c95111cbd0a79b4e50e7d74d885` | `src/memory_bench/adapters/legacy/base.py` | copied, imports changed |
| `src/lens/flip/systems/full.py` | `97a622f` | `65f2acb96668887c887851866c4b031bb2117364769720bfd5bebc67d65877eb` | `src/memory_bench/adapters/legacy/full.py` | copied, imports changed |
| `src/lens/flip/systems/recent.py` | `97a622f` | `548f64211f589b0c1c1ef4ffa86d667ae55945f2c5e849a6a06c600f5d79acb1` | `src/memory_bench/adapters/legacy/recent.py` | copied, imports changed |
| `src/lens/flip/systems/rag_bm25.py` | `97a622f` | `180df8682aa4edf72254b833ad3eeff54ac8cecc71ad8b47cbd8f69e397682e1` | `src/memory_bench/adapters/legacy/rag_bm25.py` | copied, imports changed |
| `src/lens/flip/headline/engine.py` | `97a622f` | `193bdad8b2afa9e4b91192ecdf1e537affd46b0a735a49ebdca717242fb87443` | `src/memory_bench/cases/sample.py` | extracted: sample_mix, u2_pair_for, make_headline_sample, LENGTHS (constant) |
| `src/lens/flip/headline/merge.py` | `97a622f` | `e8c3afd4021e4cbd9d81d828189f9e4b22e4841d7eeb5203095785d742b408ec` | `src/memory_bench/cases/sample.py` | extracted: case-selection rule only (non-U2 base cases kept in base order, then the U2 run's cases), written as dev_cases(); the rest of merge.py is not lifted |
| `src/lens/flip/systems/__init__.py` | `97a622f` | `7490454ddda58bc6252f2eed44b04537c276a6a81ef69435b2f0fa56b2fe3544` | `src/memory_bench/adapters/legacy/__init__.py` | rewritten, not copied: get_system limited to full, recent, rag-bm25 |
| `results/headline-300-v3/summary_v3.json` | `97a622f` | `2a987dc6a3bd8b63a1a7db8b78f4c0e6deb3d591a7df59981d0d69620398b7a8` | `tests/fixtures/summary_v3.json` | copied unchanged (expected values) |
| `results/headline-300-v3/tables_v3.txt` | `97a622f` | `b129cb4503aaf3bf0460e2ba46b8423ab88951bf31cb43d26065d71acecda3f5` | `tests/fixtures/tables_v3.txt` | copied unchanged (expected values) |
| `LICENSE` | `97a622f` | `b0e22da7557fd78cca720e84420380ef4b037841ea0c25d7f3fb6ff9b4419384` | `LICENSE` | copied unchanged |

Import changes: `from .schema` became `from ..cases.schema` (or `...cases.schema` under `adapters/legacy/`), likewise for `generator`, `render`, `cache` (now `gateway.cache`), `decider` (now `deciders.lifted`) and `systems.base`. `data/dev/cases.jsonl` is produced by `bench cases dev`; its sha256 equals the released `results/headline-300-v3/cases.jsonl` (R1).

## Tests lifted, trimmed and dropped

| Source test file | Target | What changed and why |
| --- | --- | --- |
| `tests/flip/test_generator.py` | `tests/test_generator.py` | Dropped `test_cli_same_seed_same_bytes` (covers the `lens flip gen` CLI, not lifted). Banned-word file path now `parents[1]` (tests sit one level higher); the two word checks still skip when the optional file is absent. |
| `tests/flip/test_systems.py` | `tests/test_systems.py` | Kept in full: it covers full, recent and rag-bm25 only. The unused `SYSTEMS` import was removed. |
| `tests/flip/test_decider.py` | `tests/test_decider.py` | Dropped `test_policy_identical_in_ollama_and_batch_requests` (needs `batch`, not lifted). |
| `tests/flip/test_headline.py` | `tests/test_sample.py` | Only `test_sample_mix_lengths_and_determinism`, rewritten against `cases.sample`. The rest covers the headline engine, requests, tasks and metrics. |
| `tests/flip/test_protocol_v3.py` | `tests/test_sample.py` | Only the byte-for-byte v2 regeneration check (hash `74a1e3a4...1374`, from `headline/protocol.py`), the v2 versus v3 U2-only difference, and the unchanged-defaults check. The rest covers protocol hashes, `init_run` and the validity CLI. |
| `test_batch`, `test_ddmin`, `test_explore4`, `test_group_flip`, `test_headline_v2`, `test_live`, `test_omission`, `test_reader_plausibility`, `test_runner`, `test_step5`, `test_summary_fix` | not lifted | They test modules that were left behind (batch, ddmin, flip, residue, omission, explore4, summary, rag-embed, headline engine and metrics). |
| new | `tests/test_dev_cases.py`, `tests/test_fixture_shape.py` | R1 hash test and the decision fixture shape test. |
