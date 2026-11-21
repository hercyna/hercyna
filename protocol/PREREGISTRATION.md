# Preregistration, protocol v1

Status frozen by `bench freeze --step 1`. The commit date counts as registration (charter section 5, rule 4).
Definitions are inside `CHARTER.md` section 4. Values are inside `protocol/v1.spec.json`. This file states what gets tested and how it gets read.

## 1. Question

Do memory products for agents differ on how often an agent, reading what a product returns, chooses a wrong action beyond the decider's own repeat noise?

## 2. Units

| Item | Value |
| --- | --- |
| Cases | Table cases from a fresh seed, frozen in step 2 (D7). Development cases (seed 11) are never scored for the table |
| Deciders | `haiku-5-5`, `grok-4-20-r`. Both passed the validity gate (58 of 60 each) |
| Systems | `full` (floor), `bm25`, `recent`, Mem0 2.2.1 (`k25`), LangMem 0.0.30 (`c25`), Graphiti 0.31.2 (`e25`) |
| Budget, repeats | 1000 bench tokens, `R = 3` |
| Memory side | `grok-4.20-0309-non-reasoning` for every product, `nomic-embed-text` embeddings, 25 records per write |

## 3. Primary measure

`HEADLINE(s,d) = wrong_commit(s,d) - floor(d)` over gated cases and repeats (charter 4.4).

## 4. Hypotheses and decision rules

| # | Hypothesis | Test | Reads as supported when |
| --- | --- | --- | --- |
| H1 | A product's wrong-commit rate is above the floor | Paired bootstrap, 1000 resamples, seed 11, percentile 95 percent interval of `HEADLINE(s,d)` | The lower bound is above zero, on each decider separately |
| H2 | Two products differ in wrong-commit rate | Two-sided paired-bootstrap p-value of `HEADLINE(s1,d) - HEADLINE(s2,d)`, Holm-adjusted across the three product pairs (D8) | Adjusted p below 0.05 |
| L | The table is a leaderboard | H2 holds for at least one pair on both deciders, in the same direction | Otherwise the null is published and no rank is shown |

These secondary items get intervals in the report and get no test for rank: withheld action above floor, right action, gated false-closure rate, parse errors, view size, seconds for ingest and query, dollars per case and product warnings.

## 5. What is known before the run (development cases)

- The two deciders on 300 development cases separate `bm25` from `recent` on wrong commit in opposite directions (+16.2 points for Haiku, -4.3 for Grok, Holm p below 0.05 for both). On both, withheld and right action keep that direction. That pair gives no rank under rule L. As written, the rule stays (Varma, 2026-11-19).
- Haiku floors are 1.7 percent wrong commit and 1.3 percent withheld. The Grok wrong commit floor is 1.2 percent and the Grok withheld floor is 0.0 percent.
- Repeat stability, gated case share where all three repeats gave same action: Haiku `full` 93.5, `bm25` 59.9, `recent` 89.4 percent. Grok 97.6, 72.3, 77.4.

## 6. Power

A normal approximation from development runs targets 80 percent power.

| Decider | SE of a pair difference (bm25 vs recent) | Smallest detectable gap, alpha 0.05 | Holm worst case, alpha 0.05/3 | Smallest detectable headline vs zero |
| --- | --- | --- | --- | --- |
| Haiku | 2.41 points | 6.7 points | 7.8 points | 3.6 to 5.6 points |
| Grok | 1.91 points | 5.3 points | 6.2 points | 3.3 to 4.3 points |

Reading: two products will usually not separate with about 300 cases when wrong-commit rates differ by a gap less than about 6 to about 8 points. 20-case product readings lie so far at most 6.6 points apart. A null outcome on Day 13 is likely and publishable.

## 7. Exploratory, labelled as such wherever shown

- Prompt wording v2 (S9.3) appears. Haiku 300 development cases had gate 95.3 percent (v1 97.3), floor wrong commit 2.1 percent [0.9, 3.4] (v1 1.7 [0.8, 2.8]), `recent` above floor 5.8 points [3.0, 8.7] (v1 4.3 [1.7, 6.8]). Grok first 60 cases gave gate 59 of 60 on both wordings, floor 0.0 on both, `recent` wrong commit 9.0 percent (v1 8.5), withheld 60.5 (v1 62.1). Changing wording leaves floor unchanged. Prompt v1 stays frozen.
- For view-dependent repeat noise, the floor gets measured on `full` without a measure of noise on a degraded view.
- Per-mechanism breakdowns, budgets apart from 1000, vendor-tuned rows and any decider not in the two above.

## 8. Changes

Nothing hashed changes after the freeze. A fix yields protocol v2, a changelog entry and a rerun for the affected cells. Results under v1 remain and are labelled.
