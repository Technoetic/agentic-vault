# Configured revocation for file-grounded judgments

The file-grounded judgment adapter applies the opt-in revocation registry to selected vault excerpts. The
registry and declared derivation semantics are described in
[memory revocation](memory-revocation.md). No configuration is enabled by an
update, and no new runtime dependency is required.

The adapter projects the **complete raw UTF-8 source before removing BOM or normalizing
CRLF**. An excerpt must exist in that projected source; a withdrawn excerpt is
unavailable rather than replaced with another quote. Reported source hashes still
identify the original raw bytes.

An internal `ContextGuard` retains the original `Store`, `RevocationPolicy` and
selected raw source hashes. The judgment adapter retains that guard during preparation,
checks it before dispatch to the selected provider, and checks it again after the response.
Changes to a selected source, the configuration or the registry invalidate the request or its result.
A malformed configured registry also stops the request. The checks take place at specific points and do not lock the files.
The host withholds a response if the inputs change during a call. Bytes already sent remain at the destination.
The request binds the original raw configuration identity or its absence. Enabling
revocation or creating a configuration after preparation invalidates the old request.

Configured whole-source reads retain the existing 4 MiB file bound and 32 MiB
cumulative revocation-reader budget. The guard's additional selected-source reads
and hash rechecks have their own 32 MiB cumulative bound; existing consumer input
and output limits also apply. Errors and empty results are availability outcomes,
never evidence of useful selective preservation.

## Synthetic lifecycle checks

From a clean checkout, with the existing development requirements installed:

```console
python -B -m unittest tests.test_revocation_egress -v
python -B tests/test_stateful_revocation_egress.py --seed 20261109
python -B scripts/evaluate_revocation_egress.py
```

The public fixture is
`tests/fixtures/revocation_egress/pack.json`. It declares a root, a transformed
hot-note child, a two-line fractured handoff descendant and an activity-log child.
It also contains an undeclared transformed copy that remains explicitly outside
the guarantee. UTF-8 BOM and CRLF are deliberately preserved in the temporary
source bytes. Reports include fixture, raw-source, registry, output and trace
fingerprints, consumer names, phase/session, observed poison and benign flags.

The evaluator records four consumers in a temporary vault: fresh `vault_recall.recall`, `vault_memory.compile_units`, `vault_judge._prepare`, and an in-memory sink for the prepared evidence.
It records a simulated send, revokes the root, and calls the original Jev guard’s `recheck`, which rejects the stale evidence. It then
prepares current evidence and records a second simulated send. The first entry is retained;
the test uses no persistent outbox, network, database, model or real message delivery.

The generated state machine varies revoke, authored derivative declaration,
fresh retrieval, compile rebuild, prepare, simulated send, source/configuration/
registry changes and authored synthetic rebind orders. Direct execution requires
Hypothesis and errors if it is missing; ordinary unittest discovery can skip the
optional generated module when the development dependency is absent.
Each generated trace finishes with mandatory actual fresh retrieval, compile,
Jev preparation and a guarded simulated-send checkpoint. Missing context
and invalid/stale evidence are recorded as unavailable/refused observations and
do not earn useful safe success.

These two negative controls intentionally exit nonzero:

```console
python -B scripts/evaluate_revocation_egress.py --negative-control
python -B tests/test_stateful_revocation_egress.py --negative-control --seed 20261109
```

They use the real unfiltered direct-prefix reader in the test adapter. The
generated failure shrinks to a revoke followed by a direct read. Missing/invalid
observations do not count as safe useful success, and benign retention is
required in addition to absence of declared withdrawn markers.

This is declared read-context withdrawal, not poison detection, semantic
descendant discovery or validation of model-generated answers. User prompts,
arbitrary host reads, unregistered copies, already-sent
information and model paraphrases are outside this boundary. These checks do not
reproduce a published benchmark or establish real-model safety or utility.
