# Track 2, regulated account operations (design for protocol v2)

Status: design only. Not built, not in protocol v1.
Vocabulary: generic words only.

## 1. Why it waits for v2

Track 2 asks a different question from Track 1: may the agent take a requested step now, given earlier records? It needs its own action names, policy text, reply schema and parser enum. Step 1 of protocol v1 froze `deciders/lifted.py`, `deciders/prompts.py`, `deciders/model.py` and `deciders/transports.py`, which hold all four. Changing them makes protocol v2. The freeze design should have kept the prompt per track; v2 fixes that (section 6).

## 2. The question and the actions

Query: "A request on this account asks for `<step>` on `<date>`. Choose one action."
`<step>` is one of: a service change, a collection step, a closure. Generic words, drawn per case.

| Action | Meaning | Committed |
| --- | --- | --- |
| `PROCEED` | No record forbids or delays the step | yes |
| `HOLD` | An earlier record forbids the step on this date | yes |
| `SEND_NOTICE` | An earlier record requires a notice first, and none was sent early enough | yes |
| `REQUEST_INFO` | The records do not settle it, or two rules conflict | no (abstain) |

A wrong commit here is the costly kind: `PROCEED` against a hold or a missing notice is a compliance breach. `HOLD` when nothing forbids the step is a wrong commit too, a service failure.

## 3. Mechanisms

Each case carries three slots, one per pattern, planted or decoyed with the same (type, subtype) counts, as in Track 1, so event counts never reveal the mechanism.

| Id | Planted (rule applies on the request date) | Decoy (same events, rule does not apply) | Right action |
| --- | --- | --- | --- |
| R0 | none | all three slots decoyed | `PROCEED` |
| R1 protected status | A protected status registered before the request date, valid through it, never withdrawn | The status expired, or was withdrawn by a later record, before the request date | `HOLD` |
| R2 payment arrangement | An arrangement covers the balance and every instalment due so far is paid | One instalment missed and the arrangement marked ended before the request date | `HOLD` (collection and closure steps only; a service change proceeds) |
| R3 notice period | The step needs a notice N days before. No notice, or a notice sent fewer than N days before the request date | A notice sent N or more days before | `SEND_NOTICE` |
| RU unresolved | Two slots planted with conflicting outcomes (a status says hold, an arrangement ended) | – | `REQUEST_INFO` |

The step type is crossed with the mechanism, so R2's rule depends on which step was asked. That is the memory test: the deciding record can be far back in the history, and a later record can cancel it.

Mix: R0 half, the four others share the rest evenly, like Track 1's default mix. Lengths 100 and 400. The distance knob places the deciding record near the query or far from it.

## 4. Oracle

Pure function of the events and the request (step, date), like Track 1's `oracle`. Rules in order: count the active rules on the date. None gives `PROCEED`. One gives its action. Two that disagree give `REQUEST_INFO`. Tests: every planted case gets its action, every decoy case gets `PROCEED`, and a shuffled history gives the same answer.

## 5. Gates before any score

- Validity gate per decider on 60 Track 2 cases (10 per mechanism plus 10 R0): accuracy 0.90 overall, 0.80 per mechanism. A mechanism that fails on either decider is dropped and noted.
- Blind-record test: adapters never see the request's right action or the mechanism.
- Generic words checked by a word list in the renderer tests (no employer, client or field-specific term).

## 6. Protocol v2 changes

- The prompt pieces move to one table per track: `SYSTEM`, `POLICY`, `REPLY`, the action enum and the parser enum, keyed by track. `DecisionInput` gets a `track` field. Track 1's v1 strings stay byte for byte, so Track 1 cases build the same messages and the v1 results stay comparable (a test proves the bytes).
- Freeze step 1 of v2 hashes the per-track prompt tables. Step 2 hashes the generator with both tracks and the table cases of both tracks.
- Metric rows already carry `track`. Summaries and the table show each track as its own section. No pooled number across tracks.

## 7. Cost, from Day 9 prices

| Item | Estimate |
| --- | --- |
| Validity, 60 cases, both deciders | about 2 USD |
| Baselines, 60 dev cases, both deciders | about 9 USD |
| 100 table cases, three products, two deciders | about 75 USD |

## 8. Build order when v2 starts

1. Generator and oracle, with the slot and count-balance tests.
2. Renderer lines and the generic-word test.
3. Per-track prompt tables and the byte-equality test for Track 1.
4. Validity on both deciders, then baselines (the maintainer reads the results).
5. v2 freeze, then the table run.
