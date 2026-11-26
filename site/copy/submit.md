Anybody can add a memory system. Metric files from one run form a submission folder. Inside it are counts, actions and hashes. It lacks record text.

## Write an adapter

The adapter has four methods: reset, ingest, retrieve and close. For each case, reset starts an empty store; ingest reads the records in order. retrieve returns a view for the request within a token budget. close shuts the store. The adapter never sees the right action.

## Run it

Run the bench on the 300 chat table cases with all five deciders, using your own keys. An outside run is labelled exploratory, because table grade needs the frozen configs. Point the product at the bench's shared memory-side model, so every product uses the same model. The run writes all its files into one folder.

## Pack and verify

The `bench submit pack` command copies released files into the submission folder. Your name and your adapter source must be filled in. The `bench verify` command checks each hash, recomputes its summary and checks labels against the cases.

## Open a pull request

Open a pull request with the folder. Paste the last line of the verify output into the request. A vendor may also send a tuned configuration. The maintainer runs it on the table cases and publishes it as its own row, labelled exploratory.

## How a row is labelled

A row drawn from a maintainer run gets labelled maintainer run. A verified outside submission is shown labelled exploratory. Once the maintainer reruns this same system under this same protocol, its label becomes maintainer-reproduced.
