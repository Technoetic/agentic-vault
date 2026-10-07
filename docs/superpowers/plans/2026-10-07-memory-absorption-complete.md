# Complete memory-pattern absorption implementation plan

> For agentic workers: execute through subagent-driven-development with disjoint source ownership and TDD, then independent whole-tree review.

Goal: complete the remaining concrete patterns, preserving existing boundaries and proving their behavior.
Architecture: optional advanced recall and independently runnable source-bound compilation/observation tools; shared host workflows wire their outputs.
Tech stack: Python 3.10+ stdlib, Markdown/Git.
Spec: ../specs/2026-10-07-memory-absorption-complete.md

## Global constraints
Follow all constraints in spec. Work only in the existing isolated branch; no remote operations, model downloads or live-vault mutations. Tests first, observe RED, implement, observe GREEN. Parent stages/commits explicit owned files.

## Review focus
- Temporal intervals, malformed/duplicate metadata and current-vs-historical queries.
- Out-of-policy external candidate paths, adversarial text, stale files and Unicode filenames.
- Ambiguous/multi-hop/cyclic wikilinks and strict read/token budgets.
- Capsule quote/hash drift, conflicting conditions, unsafe input JSON and dedup information loss.
- Observation evidence stale/unknown, concurrent writes and poisoning/rollback state leakage.

## Task A: advanced recall
Files: new vault_retrieval.py, tests/test_vault_retrieval.py, tests/fixtures/recall_advanced/**; modify vault_recall.py and its tests only.
- [x] Write adversarial temporal/graph/BM25/fusion/sidecar tests; verify RED.
- [x] Implement the retrieve signature in spec and legacy-compatible wrapper/CLI.
- [x] Verify focused tests and original benchmark; report explicit limits and source hashes.

## Task B: structured units
Files: new vault_memory.py, tests/test_vault_memory.py, tests/fixtures/memory_units/** only.
- [x] Write quote-binding, dedup/conflict, date/entity and budget/no-write tests; verify RED.
- [x] Implement compile_units signature and safe explicit JSON CLI.
- [x] Verify focused tests; report exact schemas and examples for host integration.

## Task C: lesson outcomes
Files: new vault_lesson_metrics.py and tests/test_vault_lesson_metrics.py only.
- [x] Write integrity/evidence/outcome/locking/no-autopromotion tests; verify RED.
- [x] Implement observation store + record/summarize API and CLI; preserve existing receipts.
- [x] Verify focused tests; report exact evidence semantics and call interface.

## Task D: host integration and workflow evaluation
Owner: parent. Files: scripts/evaluate_memory_workflows.py/tests, scripts/evaluate_recall.py, installer resource tests, commands/docs.
- [x] Test genuine task-state completion, three memory conditions, poison and rollback; verify RED.
- [x] Implement runner; connect advanced evaluator flags and actual command usage.
- [x] Compare original vs advanced/source capsule/effect patterns, disclose observed failures and optional provider status.
- [x] Run complete suite/native disposable check; independent rerun/adversarial review; commit all owned changes.
