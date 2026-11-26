Hercyna tests agent memory products by actions an agent takes based on what those products return. It does not grade question answering. It counts decisions.

## The question

Every case is one multi-session chat history plus one new request from the user. The agent must pick one of five actions: EXECUTE_OR_ANSWER, USE_UPDATED_PREF, CHECK_OR_RENEW, REFUSE_OR_GUARD or REQUEST_INFO. A rule oracle knows the right action. The memory product decides what part of the history the agent sees.

## Cases

Every case is synthetic, written by a seeded generator. The table uses 300 cases from table seed 2026112302, drawn after the protocol was frozen. Each history has 55 to 190 dated turns over simulated weeks. Of 300 cases, 150 need ordinary reasoning; the other 150 come in five kinds of 30 each. They are later preference replacing an earlier one, expired approval, standing prohibition, unstated-rule request, two unresolved conflicting rules.

## Deciders

A decider is the model that reads the view and picks the action. Version 2 uses five deciders. One is Claude Haiku 5.5. Four are open-weight models served through OpenCode: DeepSeek V4.1 Flash, GLM 5.3 Flash, MiniMax M3 and Qwen 3.8 Flash. Each decider first passed a validity gate on 60 sampled cases, reading the full history with no truncation. The gate needs 90 percent accuracy overall and 80 percent on each of the six case kinds. A model that fails the gate is left out of the table.

## Memory systems

A memory system reads the history and returns a view of at most 1,000 tokens. The table has three products at pinned versions: Mem0 2.2.1, LangMem 0.0.30 and Graphiti 0.31.2. Five baselines are BM25 keyword search, dense embedding search, a hybrid, most recent turns and an empty view. Every product writes 25 records at a time. All three products use one shared memory-side model, DeepSeek V4.1 Flash, with the bge-m3 embedder. DeepSeek V4.1 Flash is also one of five deciders; the chat page shows little change without it.

## Rates and intervals

Each row reports three rates side by side: wrong action, withheld action and right action. Withheld action means the agent asked for more information when the records were enough. The headline is excess wrong action: the wrong-action rate minus the decider's floor. Intervals are 95 percent paired bootstrap intervals over cases with 1000 resamples. Failed ingests are left out and counted on the product's row: 2 for Mem0 and 13 for Graphiti. A product may leave out at most 5 percent of cases.

## The frozen rule and the board

The frozen rule asks whether two products separate on wrong action. It uses a paired bootstrap test with a Holm correction across product pairs, at alpha 0.05. A pair counts only if it separates in the same direction on every one of the five deciders. In version 2, one pair met the rule: Graphiti makes fewer wrong actions than Mem0. The page prints no rank, because a product can lower its wrong actions by withholding. Baselines are reference rows; the rule does not test them.

## What it does not claim

The bench measures a controlled difference under fixed rules. It does not measure harm rates in production and it does not rank the deciders. All cases are synthetic and version 2 covers one track: multi-session chat. The coding agent track is in preparation.

## Frozen before the run

Protocol v2 hashed inputs before any table run. These were the metric, the deciders, the adapter settings, the memory-side model, the cases and the scoring code. The protocol grew in 10 append-only steps; its hash starts 68af8d8e; a change makes a new protocol version. If a hash does not match, the engine refuses to label a run a table result.

## Version 1 (archive)

The text below describes version 1, the account-review track. Its results are on the [v1 archive](v1.html).

### The question

Every case is one dated account history plus one request. The agent must pick one of five actions. A rule oracle knows the correct one. The memory product picks what the agent sees.

### Cases

Each case is synthetic. A seeded generator writes each history. Each history has about 100 or 400 records. Half the cases need only ordinary reasoning. In the other half one of three things, a rare pattern, a removed record or else two conflicting patterns, changes the right action. The table uses 300 cases from a fresh seed after the protocol was frozen.

### Deciders

A decider is the model reading the view and picking the action. Version 1 uses two deciders taken from two different vendors. Each one must pass the full history validity gate first. The gate requires 90 percent overall accuracy and 80 percent on every pattern.

### Memory systems

A memory system reads history and returns a view that holds at most 1000 bench tokens. Three baselines run beside products. The full baseline shows the history in full. The keyword baseline returns best matching records. The recent baseline returns latest records. Every product runs at one pinned version with the same model plus the same embedder.

### The floor

A decider can change its own answer when the same text has a second read. That noise is measured by the floor. Three more times the decider reads the full history. On those reads, its wrong actions are the floor.

### The excess wrong-action rate

The headline is the excess wrong-action rate: the share of decisions that take a wrong action, minus the floor. Only cases the decider gets right on the full history count. The interval comes from a paired bootstrap over cases with 1000 resamples.

### Withheld action

A product can appear safe by inaction. Hence the table shows withheld action too. This is the share of decisions seeking more information even when records sufficed. A low wrong-action rate with a high withheld rate is not a good result.

### The frozen rule and the board

The frozen rule asks whether two products separate on both deciders in the same direction. It uses a paired bootstrap test with a Holm correction across product pairs. In v1 two pairs met the rule.

The preregistration planned to print a rank when that happened. A pre-release review then showed that a product can lower its wrong action by withholding. So the board prints no rank numbers. It shows wrong, withheld and right side by side. It reports the rule's result as it came out.

### What it does not claim

The bench measures, under fixed rules, a controlled difference. Harm rates in production are not measured by it. The deciders are not ranked by it. All cases in version 1 are synthetic.

### Frozen before the run

Before any table run, the metric, the deciders, adapter settings, the separation rule and also the cases were hashed. If a hash does not match, the engine refuses to label the run a table result. A change yields a new protocol version.
