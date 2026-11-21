Anybody can add a memory system. Metric files from one run form a submission folder. Inside it are counts, actions and hashes. It lacks record text.

## Write an adapter

Four methods belong to an adapter. For each case it resets an empty store. In order it ingests the records. Within a token budget it returns a view for a query. It closes the store. The right action is never seen by the adapter.

## Run it

Run the bench on the table cases with both deciders, using your own keys. In version 1 that makes your run exploratory, because table grade needs the frozen configs. The product points at the local model endpoint of the bench so every product uses that same model. The run writes all its files into one folder.

## Pack and verify

The `bench submit pack` command copies released files into the submission folder. Your name and your adapter source must be filled in. The `bench verify` command checks each hash, recomputes its summary and checks labels against the cases.

## Open a pull request

Open a pull request with the folder. Paste the last line of the verify output into the request. A vendor may also send a tuned configuration. The maintainer runs it on the table cases and publishes it as its own row, labelled exploratory.

## How a row is labelled

A row drawn from a maintainer run gets labelled maintainer run. A verified outside submission is shown labelled exploratory. Once the maintainer reruns this same system under this same protocol, its label becomes maintainer-reproduced.
