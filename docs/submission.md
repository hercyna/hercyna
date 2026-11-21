# Submission, verify and the static site (design, fixed on Day 11)

Sources: charter sections 4 to 6, `docs/run-files.md`, `docs/freeze.md`.

## 1. A submission folder

`submissions/<system>/<YYYY-MM-DD>-<slug>/`, where `<system>` is the adapter name (`mem0`) and `<slug>` is short and lowercase.

| File | Required | Source |
| --- | --- | --- |
| `manifest.json`, `metrics.jsonl`, `summary.json`, `spend.json`, `health.json` | yes | the run folder, copied unchanged |
| `timings.jsonl`, `calls.jsonl`, `memory_calls.jsonl`, `stability.json`, `power.json`, `view-audit.json` | when the run wrote them | the run folder, copied unchanged |
| `submission.json` | yes | written by `bench submit pack`, then completed by hand |

`submission.json`:

```json
{"schema": "memory_bench.submission/1",
 "system": "mem0", "config_label": "k25", "product_version": "2.2.1",
 "submitter": "name as it should appear", "organisation": "or null", "adapter_source": "URL or null",
 "run_id": "<the manifest's run_id>", "protocol_sha256": "<the manifest's>",
 "reproduced": null}
```

`reproduced` is null when submitted. Only the maintainer sets it, to `{"run_id": "<maintainer run>", "at": "<date>"}`.

Views (`private/views.jsonl`) and any text never go into a submission. The text-free scan enforces it.

## 2. `bench submit pack RUN --out DIR [--slug S]`

- Copies the released files of RUN into DIR, never `private/`.
- Writes `submission.json` with every field it knows from the manifest. `submitter` and `organisation` are left as `null` for the submitter to fill.
- Refuses a run whose state is not `complete`, and a run whose grade is not `table` unless `--exploratory` is passed. The flag writes `"grade": "exploratory"` into `submission.json`.

## 3. `bench verify DIR [--protocol protocol/v1.json] [--cases data/table/cases.jsonl]`

Checks run in order. Each failure prints one line naming the check and the file (hashes and keys only, never text). Exit 0 when all pass, 5 otherwise.

1. **Files.** Every file named in `manifest.files` exists and its sha256 matches. No file outside the allowed list (section 1) is present.
2. **Text-free.** `submit.textfree` finds no violation in any released file.
3. **Rows.** `metrics.jsonl` parses. Row keys are unique. Every row's `decider` is in the manifest. Each case in the manifest's case set has one gate row per decider, `R` floor rows and `R` view rows per cell. No row for a case outside the set.
4. **Labels.** With `--cases` (default: `data/table/cases.jsonl` when the grade is `table`), the manifest's case hash equals the file's hash, and every row's `a_star` and `mechanism` equal the case file's values. A relabelled row fails here.
5. **Summary.** `summarise(rows, seed, reps)` with the manifest's bootstrap seed and reps, plus `grade`, gives exactly the bytes of `summary.json` (canonical JSON as `engine.files.write_json` writes it).
6. **Protocol.** For grade `table`: the manifest's `protocol.protocol_sha256` equals the one in `--protocol`, `bench freeze --check` passes on the verifying machine, and deciders, systems, budgets, repeats and bootstrap values are the frozen ones. For grade `exploratory`: this check is skipped and the verdict says "exploratory".
7. **Submission.** `submission.json` parses. Its `run_id`, `protocol_sha256`, `system`, `config_label` and `product_version` match the manifest. If `reproduced` is set, the named run exists under `results/runs/`, is complete and table-grade, and has the same system, config label and product version.

Tamper tests (each must fail at the named check): an edited action in one row (5, and 1 through the hash), a flipped `a_star` with the hash updated (4), a deleted row with hashes updated (3), a view line added to `metrics.jsonl` (2), a summary rebuilt with another seed (5), a protocol hash changed (6), a `reproduced` pointing at a missing run (7).

## 4. Where the table reads from

- Maintainer runs: `results/runs/<id>/` with grade `table`. Row label: **maintainer run**.
- Submissions: `submissions/**/` that pass `bench verify`. Row label: **self-reported**, or **maintainer-reproduced** when `reproduced` is set and check 7 passes.
- A system that appears in both is shown once, from the maintainer run, with the submission listed under it.
- `--include-exploratory` (local builds only) adds exploratory runs, each row labelled **exploratory**. The public build never passes it.

## 5. `bench site build [--out site/dist] [--include-exploratory]`

Static, offline, no script needed to read the table, no tracking, no external request except fonts with a system fallback. Pages:

| Page | Content |
| --- | --- |
| `index.html` | One section per decider: a table with system, excess wrong-action rate and its 95 percent interval, withheld above floor, right action, view tokens, dollars per case, row label. Then the rule-L verdict in one line: "leaderboard" with the separating pairs, or "no rank" with the reason. Then the floor per decider. |
| `method.html` | Copy from `site/copy/method.md`, plus a generated block of frozen values from `protocol/v1.json` (deciders, budgets, repeats, seed, resamples, protocol hash). |
| `submit.html` | Copy from `site/copy/submit.md`, plus the command lines. |

- Copy files are Markdown with `#` headings, paragraphs, lists and code spans. The builder renders that subset only.
- A missing copy file renders a visible "copy pending" block, never placeholder prose.
- Numbers come only from verified files. The site never computes a rate itself: it reads `summary.json` and `separation` output.
- No rank order when rule L gives no rank: rows are then in a fixed order (baselines first, then products alphabetically) and the page says so.
- The page is light and dark aware, readable at 400 px, tables scroll inside their own box.

## 6. Pull request template

`.github/pull_request_template.md` (in the repo, no remote yet): the submission checklist. Run `bench verify` and paste its last line, fill `submission.json`, no views or text, and state the product version. Copy for it comes from the writer like any public text; until then it holds the checklist items only.
