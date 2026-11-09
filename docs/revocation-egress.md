# Configured revocation at Jev and Jarvis context boundaries

v0.44.0 extends the existing opt-in source-span registry to Jev's selected vault
excerpts and Jarvis's direct hot, handoff and optional activity-log context. The
registry and declared derivation semantics are described in
[memory revocation](memory-revocation.md). No configuration is enabled by an
update, and no new runtime dependency is required.

Jev projects the **complete raw UTF-8 source before removing BOM or normalizing
CRLF**. An excerpt must exist in that projected source; a withdrawn excerpt is
unavailable rather than replaced with another quote. Reported source hashes still
identify the original raw bytes. Jarvis projects whole sources before byte,
character, token and log-line output limits. Its selected recall snippets are
rebuilt from current projected sources bound to the same envelope.

An internal `ContextGuard` retains the original `Store`, `RevocationPolicy` and
selected raw source hashes. Jev carries that guard from preparation to
`request_judgments`; Jarvis carries it with its prompt to `run_with_stdin`. Both
recheck immediately before dispatch and after completion. A changed source,
configuration or registry, or a malformed configured registry, rejects the
request/result without an unfiltered fallback. A check is a point-in-time
observation, not a filesystem lock. Changes during an external call withhold its
answer; they cannot retract bytes already sent.

The compatibility `_generation_prompt` API still returns a string. Actual Jarvis
generation retains the envelope and checks its original guard, rather than
constructing a new policy after generation. With revocation absent or disabled,
the existing prompt format and direct-prefix behavior remain unchanged. Every
envelope still binds the original raw configuration identity or its absence;
enabling revocation or creating a vault configuration before dispatch rejects the
old prompt rather than authorizing it under the newly enabled policy.

Configured whole-source reads retain the existing 4 MiB file bound and 32 MiB
cumulative revocation-reader budget. The guard's additional selected-source reads
and hash rechecks have their own 32 MiB cumulative bound; existing consumer input
and output limits also apply. Errors and empty results are availability outcomes,
never evidence of useful selective preservation.

## Original synthetic lifecycle checks

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

The evaluator executes actual fresh `recall`, `compile_units`, Jev preparation
and Jarvis envelope construction in a temporary vault. It prepares an envelope,
records an in-memory simulated send, revokes the root, refuses the old envelope,
rebuilds current context and records a later simulated send. The earlier sink
entry remains present. There is no persistent engine index or real outbox, and
no model, network, database, company input or real message delivery is used.

The generated state machine varies revoke, authored derivative declaration,
fresh retrieval, compile rebuild, prepare, simulated send, source/configuration/
registry changes and authored synthetic rebind orders. Direct execution requires
Hypothesis and errors if it is missing; ordinary unittest discovery can skip the
optional generated module when the development dependency is absent.
Each generated trace finishes with mandatory actual fresh retrieval, compile,
Jev/Jarvis preparation and a guarded simulated-send checkpoint. Missing context
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
supplied git activity, arbitrary host reads, unregistered copies, already-sent
information and model paraphrases are outside this boundary. These checks do not
reproduce a published benchmark or establish real-model safety or utility.
