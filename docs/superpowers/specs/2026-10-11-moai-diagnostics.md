# MoAI-inspired opt-in diagnostics

The accepted scope is three additions to the existing Doctor, memory and evidence workflows.
Users need to locate instruction growth, preview completed-index consolidation without losing
original records, and identify required verification that was never submitted.

Implement in Python 3.10+ standard library. Reuse existing path policy, stable reads and
provenance helpers. No source code is copied from Apache-2.0 MoAI; inspiration is attributed
to immutable upstream source references in the guide and README.

1. Doctor's opt-in instruction inventory prefers root AGENTS.md, falls back to CLAUDE.md,
   follows local @ references, and inventories nested rules. Separate always/conditional/
   host-unconfirmed candidates and report bounded unique character counts. Static files
   are not proof of host loading, exact provider tokens, billing or speed.
2. Index fold is a read-only, explicit-manifest preview. Stable completed item IDs, exact
   original rows and expected source hashes identify selected records. Preserve original
   bytes/lines and existing destination links. Diagnose stale inputs, missing destinations,
   overlapping rows and unreachable links. No apply, archive read, rename or deletion.
3. Coverage declares requirements, acceptance conditions and required checks independently
   of reported checks. Diagnose missing mappings, absent/nonpassing checks and stale
   evidence using the existing evidence receipt interface. Mapping is not semantic proof;
   caller observations are not execution or verifier authentication.

All inputs are bounded. Reject unsafe/secret/denied paths before opening source files,
cycles, malformed manifests and changing file identities. Existing default behavior and
evidence callers remain compatible. No network/model invocation or production-vault
migration is added. Diagnostics are point-in-time observations.

Release/install completion requires local regressions, independent review, exact-source
CI, immutable ZIP/blob checks, public re-download verification and installed-host checks.
Existing NS engine, paused production workflows, hook trust and unrelated plugins are preserved.
