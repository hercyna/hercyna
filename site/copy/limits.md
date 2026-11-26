## What this does not show

- One chat track only: the v2 tables cover synthetic multi-session `chat_agent` cases only (`coding_agent` runs remain held).
- Five deciders: v2 chat results hold only for the five gate-passing deciders tested; other models may read the same views differently.
- Synthetic cases: all 300 v2 chat cases and all 300 v1 account-review cases come from deterministic generators, not production histories.
- Ingest-error cases left out: Mem0 drops 2 cases and Graphiti drops 13 cases during ingestion out of 300; every row prints the count of left-out cases beside wrong, withheld, and right action.
- The cause split is exploratory: the failure cause split replays dumped stores offline and marks Mem0 and LangMem as low-fidelity because store-vs-retrieval replay match is below 90 percent.

## Limits

- All three products share one memory-side model built with DeepSeek V4.1 Flash and the bge-m3 embedder. DeepSeek V4.1 Flash is also one of the five deciders. Leaving its rows out moves no range by more than 0.2 points.
- Each product reads each history once and stores the same views for every decider. All five deciders judge the same stored views, so they are five readings of one ingest.
- Baselines can beat products: BM25 keyword search acts right more often than any product on every decider. Baselines are reference rows. The separation rule does not test them so they carry no rank.
- LangMem against Mem0 separates only on Qwen 3.8 Flash, so it is reported as not separated. On Haiku its Holm-adjusted p is 0.056, just above 0.05. Graphiti against LangMem separates on MiniMax M3 only.

## Version 1 limits (archive)

These limits apply to the version 1 account-review table on the [v1 archive](v1.html).

- Bench configuration, not vendor defaults. Every product writes 25 records at a time and uses the same memory-side model and embedder. A product's own quickstart may behave differently. A vendor can send a tuned configuration. The maintainer runs it on the table cases and publishes it as its own row, labelled exploratory.
- One shared memory-side model. All three products build their memories with Grok 4.20 non-reasoning. Its vendor also makes one of the two deciders. Products that rely on tool calls depend on how well this model makes them.
- The same stored views for both deciders. Each product reads each history once. Both deciders then judge the same stored views. The two deciders are two readings of one ingest, not two separate ingests.
- Wrong and withheld trade against each other. A memory that returns little makes the agent withhold more and act wrong less. That is why the board shows wrong, withheld and right side by side and prints no single order. The empty row shows what returning nothing scores.
- Baselines can beat products. Keyword retrieval is right more often than any product on both deciders. The two deciders also order the two baselines differently, so the baselines carry no rank.
- Synthetic cases. All 300 table cases come from one generator for one kind of task: account review. Half need only ordinary reasoning. Results may differ on real histories and other tasks.
- Two deciders. Every claim holds for these two deciders. Other models may read the same views differently.
- Close calls. One pair sits near the line: Graphiti against Mem0 on Haiku, at a Holm-adjusted p of 0.0599. It is reported as not separated. With 10000 resamples over five seeds it stays between 0.052 and 0.055.
- Gate close call. Haiku passed the validity gate on one pattern at exactly the 80 percent minimum, 24 of 30.
- Health counts include stopped attempts. The model account ran out of credit during the table run. The stopped attempts left 836 failed memory calls in the logs. An audit shows that no stored view saw a failed call. The warning counts include every attempt.
- Engine changes during the run. The table runs span several versions of the run engine. The engine files are not in the v1 freeze list. The scoring, prompts, adapters, configs and cases are. Protocol v2 freezes the engine too.
- Cost to reproduce. Rebuilding the table from the released files is free. Rerunning it with live models cost about 257 dollars.

## Related benchmarks

Several benchmarks test agent memory. LoCoMo scores question answering over very long conversations. LongMemEval scores question answering over long chat histories. It also scores when to abstain. MemoryAgentBench runs many memory mechanisms through one framework. It scores retrieval, learning, long-range understanding and conflict resolution.

Hercyna asks a narrower question. It scores the action an agent takes from a memory's view. It splits wrong action from withheld action and measures both above the decider's own noise. It reports a product pair as separated only when every decider agrees.
