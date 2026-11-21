# Charter, public extract

This public file carries the sections of the project charter that the preregistration cites: the metric (section 4) and the freeze rules (section 5). The rest of the charter is private.

## 4. The headline metric, defined to code

Internal name: wrong-commit rate above floor. The public name is the excess wrong-action rate.

### 4.1 Terms

| Symbol | Meaning |
| --- | --- |
| `c` | a case: a rendered history, a query, the right action `a_star` from the rule oracle |
| `d` | a decider: one model with frozen prompt and parameters |
| `s` | a memory system: an adapter or a baseline |
| `B` | the view budget in bench tokens. Bench tokens = `ceil(characters / 2.2)`, the LENS rule |
| `R` | repeats per cell. Protocol 1 sets `R = 3` |
| `ABSTAIN` | the action `REQUEST_INFO` |
| committed | an action that is neither `ABSTAIN` nor `PARSE_ERROR` |

### 4.2 Runs per case and decider

1. **Gate run.** The decider reads the full history once. Result `a_gate[c,d]`.
2. **Floor runs.** The decider reads the full history `R` more times. Results `a_full[c,d,r]`. These are fresh calls. The cache is bypassed across repeats.
3. **View runs.** For each system `s`, the decider reads the view `R` times. Results `a_view[c,d,s,r]`. Each repeat is a fresh call on the same stored view.

The full history is scored through the same code path as any adapter. The floor is the `full` system's score. No special case exists in the scorer.

Repeat `r` uses seed `r` where the endpoint takes a seed. Temperature is 0 where the endpoint allows it. A decider that returns identical answers on every repeat gets a floor of zero and a note in the table.

### 4.3 Eligibility

`G[d] = { c : a_gate[c,d] == a_star[c] }`. Only cases the decider gets right on the full history count. The rest are excluded and counted.

A decider is usable only if it passes the validity gate on the gate run: accuracy at least 0.90 overall and at least 0.80 for every mechanism (the LENS gate of 2026-10-20).

### 4.4 The rates

For a system `s` and decider `d`, over `c` in `G[d]` and `r` in `1..R`:

```
wrong_commit(s,d) = mean over (c,r) of [ a_view[c,d,s,r] is committed  and  a_view[c,d,s,r] != a_star[c] ]
floor(d)          = mean over (c,r) of [ a_full[c,d,r]   is committed  and  a_full[c,d,r]   != a_star[c] ]

HEADLINE(s,d)     = wrong_commit(s,d) - floor(d)
```

Cases whose right action is `ABSTAIN` are included. A commit on such a case is a wrong commit.

Secondary, same shape:

```
abstention(s,d)        = mean of [ a_view == ABSTAIN and a_star != ABSTAIN ]  minus the same on a_full
false_closure_rate(s,d)= (committed a_view that != a_star) / (committed a_view), over c in G[d]     (gated FCR, as in LENS)
right_action(s,d)      = mean of [ a_view == a_star ]
parse_error(s,d)       = mean of [ a_view == PARSE_ERROR ]
```

Also reported per system: view size in bench tokens, ingest seconds and query seconds per case, ingest model calls and tokens, dollars per case.

### 4.5 Intervals

- Paired bootstrap over cases. One resample of case indices is shared by every system, the floor and every repeat. A case brings all its rows along.
- 1,000 resamples. Seed frozen in the protocol. Percentile 95 percent interval.
- `HEADLINE` is a difference of two rates on the same resample, so its interval is paired.
- Reported per decider. No pooled rank across deciders.

### 4.6 What "separates" means (the Day 13 rule)

Written before the run and frozen with the protocol. Default:
- **From the floor:** the 95 percent interval of `HEADLINE(s,d)` lies above zero.
- **From each other:** for two products, the paired-bootstrap p-value of the difference `HEADLINE(s1,d) - HEADLINE(s2,d)`, Holm-adjusted across product pairs, is below 0.05. The plain 95 percent interval of the difference is shown beside it. (Wording fixed to match the code before the freeze.)
- **The table is a leaderboard only if** at least one product pair separates on both deciders in the same direction. Otherwise the null is published and no rank is shown.

### 4.7 Why wrong commit and not any flip

The LENS run showed most harm is withheld action: 57.6 points of abstention against 8.6 of wrong commit at `recent@1000`. Wrong commit is the costly kind in account work: a credit applied or a review skipped. It also spreads systems apart in the data in hand: 3.8 for `recent@500` against 25.9 for `rag-bm25@500`. Abstention stays in the table as the second column so nobody can hide a cautious product behind a low headline.

## 5. Protocol freeze rules

1. `protocol/v1.json` holds a sha256 for each of: the cases file, generator source, renderer, decider prompt and reply schema, the policy text, every adapter config with its pinned product version, the memory-side model id, the embedding model id, decider ids and parameters, the token rule, the scoring module, the bootstrap seed and resample count, `R`, budgets, the separation rule and the validity gate.
2. The freeze happens before any table run, in two steps. Day 9: metric, deciders, adapter configs and the separation rule. Day 10: the generator, then the table seed. (Track 2 moved to protocol v2.) The engine refuses to write a table-grade result when a hash does not match.
3. After the freeze nothing hashed changes. A bug fix makes protocol v2, a changelog entry and a rerun of the affected cells. Old results stay and are labelled.
4. Hypotheses and the separation rule are committed before the run. The commit date is the registration.
5. Anything outside the frozen list is labelled exploratory in every file that holds it.
6. **Table cases come from a fresh seed** drawn at freeze time. The 300 LENS cases (seed 11) are the development set. Reason: they shaped the prompts and they are public.
7. Fresh seeds each quarter stay with the maintainer until the quarter's table is out. Then they are released.
8. Released metric files hold action names, flags, counts, timings and hashes. No view, note or prompt text. Views go only into a vendor's own pack.
9. Adapters see rendered records and the query. They never see `a_star`, the mechanism or the planted event ids. A test enforces this.

