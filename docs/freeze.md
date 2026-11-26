# Protocol freeze (design, fixed on Day 9)

Sources: charter section 5.

## 1. Files

| File | Written by | Holds |
| --- | --- | --- |
| `protocol/v1.spec.json` | hand, committed | What is frozen: path lists, globs and parameter values. No hashes |
| `protocol/v1.json` | `bench freeze` | The spec, plus a sha256 for every matched file, plus one sha256 per step, the step dates and the git commit at freeze time |
| `protocol/PREREGISTRATION.md` | hand | Hypotheses, the separation rule in words, the power note. Its sha256 is in `v1.json` |

## 2. The spec

```json
{
  "schema": "memory_bench.protocol_spec/1",
  "version": "v1",
  "steps": {
    "1": {
      "title": "metric, deciders, adapter configs, separation rule",
      "files": ["src/memory_bench/scoring/*.py", "src/memory_bench/deciders/lifted.py", "src/memory_bench/deciders/prompts.py",
                "src/memory_bench/deciders/model.py", "src/memory_bench/deciders/transports.py",
                "src/memory_bench/adapters/base.py", "src/memory_bench/adapters/full.py", "src/memory_bench/adapters/bm25.py",
                "src/memory_bench/adapters/recent.py", "src/memory_bench/adapters/mem0.py", "src/memory_bench/adapters/langmem.py",
                "src/memory_bench/adapters/graphiti.py", "src/memory_bench/gateway/proxy.py",
                "configs/deciders/haiku-5-5.json", "configs/deciders/grok-4-20-r.json",
                "configs/adapters/mem0-k25.json", "configs/adapters/langmem-c25.json", "configs/adapters/graphiti-e25.json",
                "configs/memory-side/grok-4-20-nr-mem.json", "pyproject.toml", "uv.lock", "protocol/PREREGISTRATION.md"],
      "values": {
        "deciders": ["haiku-5-5", "grok-4-20-r"],
        "systems": ["full", "bm25", "recent", "mem0/k25", "langmem/c25", "graphiti/e25"],
        "budgets": [1000], "repeats": 3,
        "boot_seed": 11, "boot_reps": 1000, "ci_level": 0.95,
        "validity_gate": {"overall": 0.90, "per_mechanism": 0.80},
        "token_rule": "ceil(characters / 2.2)",
        "chunk_rule": 25,
        "memory_side": {"chat_model": "grok-4.20-0309-non-reasoning", "embedding_model": "nomic-embed-text", "embedding_dims": 768},
        "product_pins": {"mem0ai": "2.2.1", "langmem": "0.0.30", "graphiti-core": "0.31.2", "falkordb_image": "falkordb/falkordb:6.0.2"},
        "separation": {"test": "paired bootstrap p-value, Holm-adjusted across product pairs", "alpha": 0.05,
                       "floor_test": "95 percent interval of HEADLINE above zero",
                       "leaderboard": "at least one product pair separates on every frozen decider, same direction"}
      }
    },
    "2": {
      "title": "generator with Track 2, table cases and table seed",
      "files": ["src/memory_bench/cases/*.py", "data/table/cases.jsonl"],
      "values": {"table_seed": 1283645051, "n_table_cases": 300, "generator_version": 3, "case_id_prefix": "t1283645051"}
    }
  }
}
```

Step 2 was filled on Day 10 (seed drawn 2026-11-19). Before that `bench freeze --step 2` refuses while any step-2 value is null or a step-2 file is missing.

## 3. `bench freeze`

- `bench freeze --step N [--spec protocol/v1.spec.json] [--out protocol/v1.json]`
  - Refuses if the git working tree has uncommitted changes in any matched file (the hash must describe a commit).
  - Refuses if step `N-1` is not in `v1.json`. Refuses to redo a step already in `v1.json` (a change makes v2, charter section 5 rule 3).
  - Expands each glob in sorted order. A glob that matches nothing is an error.
  - Writes, for the step: `frozen_at` (UTC), `git_commit`, `files` {path: sha256}, `values` (copied from the spec), and `step_sha256` = sha256 of the canonical JSON of `{files, values}` (sorted keys, no spaces).
  - `protocol_sha256` at the top = sha256 of the canonical JSON of all frozen steps. It changes when step 2 is added; that is expected and recorded.
- `bench freeze --check [--protocol protocol/v1.json]`
  - Recomputes every hash of every frozen step. Prints one line per mismatch: path, expected, actual (hashes only, never contents). Exit 0 when all match, 4 otherwise.
  - **Public export exclusion (`scripts/run_phase1_table.py`):** Protocol v2 Step 8 records the SHA-256 of `scripts/run_phase1_table.py` (`protocol/v2.json`). Because lines 24 and 678 of that frozen maintainer workstation runner contain a local `PATH` prefix and cannot be edited without changing the frozen Step 8 hash, `scripts/run_phase1_table.py` is omitted from the public export. In private checkouts (`scripts/export_public.py` present), `bench freeze --check --protocol protocol/v2.json` requires `scripts/run_phase1_table.py` and verifies its exact SHA-256; in public exports (`scripts/export_public.py` absent), `check_protocol` allows that single documented orchestrator to be missing from disk while strictly verifying its hash if present, every other frozen file hash, `step_sha256`, and `protocol_sha256`.

## 4. Run grade

`bench run` gets `--protocol PATH`. Without it every run is `"grade": "exploratory"`, as today.

With it the engine checks, before any model call:
1. `freeze --check` passes for all frozen steps.
2. The decider id is in `values.deciders`; the run's systems are a subset of `values.systems`; budgets, repeats, boot seed and reps equal the frozen values.
3. Product adapters: the installed package version equals `product_pins`; the adapter config file's sha256 equals the frozen one; the memory-side config's sha256 equals the frozen one.
4. If step 2 is frozen: the cases file sha256 equals the frozen `data/table/cases.jsonl`. If step 2 is not frozen: the run is still exploratory (no table cases yet), and the manifest says why.

Any failure: the run stops with exit 2 and a message naming the failed check. No silent downgrade when `--protocol` is passed.
All pass and step 2 frozen: `"grade": "table"`.
The manifest gets `"grade"` and `"protocol": {"path", "protocol_sha256", "steps": [..]}`. `summary.json` gets `"grade"`. The report and the site show the grade.
A resumed run must pass the same checks again.

## 5. `bench stability RUN` (S9.2)

Writes `RUN/stability.json` (text-free): per decider, per system (`full` included): gated cases, cases with the same action on every repeat, the share, and the share of later repeats that differ from repeat 1. Prints a table. Pure function `repeat_stability(rows)` in `scoring/stability.py`.

## 6. `bench power RUN [--pairs K] [--power 0.8]` (S9.4)

Per decider, from per-case headline values over gated cases: SE of each system's headline, SE of each pair's paired difference, and the minimum detectable difference `(z(1 - a/2) + z(power)) * SE` at `a = 0.05` and at the Holm worst case `a = 0.05 / K` (default K = 3). Prints, writes `RUN/power.json`. Normal approximation, labelled as such.

## 7. Tests

Temp git repo fixtures. Freeze then check passes; edit one byte, check fails naming that file; a dirty tree refuses; a re-freeze refuses; step 2 with a null value refuses; a run with `--protocol` and a wrong budget refuses; a run with a mock decider not in the list refuses; exploratory without the flag; stability and power on a small hand-made metrics file with known answers.
