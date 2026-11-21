Hercyna tests agent memory products by actions an agent takes based on what those products return. It does not grade question answering. It counts decisions.

## The question

Every case is one dated account history plus one request. The agent must pick one of five actions. A rule oracle knows the correct one. The memory product picks what the agent sees.

## Cases

Each case is synthetic. A seeded generator writes each history. Each history has about 100 or 400 records. Half the cases need only ordinary reasoning. In the other half one of three things, a rare pattern, a removed record or else two conflicting patterns, changes the right action. The table uses 300 cases from a fresh seed after the protocol was frozen.

## Deciders

A decider is the model reading the view and picking the action. Version 1 uses two deciders taken from two different vendors. Each one must pass the full history validity gate first. The gate requires 90 percent overall accuracy and 80 percent on every pattern.

## Memory systems

A memory system reads history and returns a view that holds at most 1000 bench tokens. Three baselines run beside products. The full baseline shows the history in full. The keyword baseline returns best matching records. The recent baseline returns latest records. Every product runs at one pinned version with the same model plus the same embedder.

## The floor

A decider can change its own answer when the same text has a second read. That noise is measured by the floor. Three more times the decider reads the full history. On those reads, its wrong actions are the floor.

## The excess wrong-action rate

The headline is the excess wrong-action rate: the share of decisions that take a wrong action, minus the floor. Only cases the decider gets right on the full history count. The interval comes from a paired bootstrap over cases with 1000 resamples.

## Withheld action

A product can appear safe by inaction. Hence the table shows withheld action too. This is the share of decisions seeking more information even when records sufficed. A low wrong-action rate with a high withheld rate is not a good result.

## The frozen rule and the board

The frozen rule asks whether two products separate on both deciders in the same direction. It uses a paired bootstrap test with a Holm correction across product pairs. In v1 two pairs met the rule.

The preregistration planned to print a rank when that happened. A pre-release review then showed that a product can lower its wrong action by withholding. So the board prints no rank numbers. It shows wrong, withheld and right side by side. It reports the rule's result as it came out.

## What it does not claim

The bench measures, under fixed rules, a controlled difference. Harm rates in production are not measured by it. The deciders are not ranked by it. All cases in version 1 are synthetic.

## Frozen before the run

Before any table run, the metric, the deciders, adapter settings, the separation rule and also the cases were hashed. If a hash does not match, the engine refuses to label the run a table result. A change yields a new protocol version.
