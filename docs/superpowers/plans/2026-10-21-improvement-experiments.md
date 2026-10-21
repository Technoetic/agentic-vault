# Improvement experiments implementation plan

> For agentic workers: use superpowers:subagent-driven-development. The preceding design and user implementation request authorize this scoped local work; no repeat approval gate.

**Goal:** connect candidate lineage, bounded comparison and evidence-aware reflections to existing AV run receipts.

**Architecture:** a small opt-in CLI reuses `vault_runs` policy, storage and observations. The host executes tasks; the coordinator validates linkage and emits review-only recommendations.

**Tech stack:** Python3.10+, stdlib, unittest.

**Spec:** `docs/superpowers/specs/2026-10-21-improvement-experiments-design.md`.

## Global constraints

- No hooks/default changes, network/model calls, arbitrary command runner, apply/promotion, new dependencies or version/release bump.
- Source-bound immutable receipts under existing runtime policy; secret-safe errors; explicit baseline and abstain.
- Preserve original/main worktrees and concurrent changes; isolated feature branch based on public v0.26.0.

## Review focus

- Recomputed receipt checksum cannot detach input/source/run bindings.
- Missing/failed runs cannot disappear from comparison denominator.
- Score gain cannot waive hard failures or turn advisory output into permission.
- A nonempty narrative cannot turn confounded or single-observation evidence into durable knowledge.
- Definition/report/evidence/config changes between initial read and publish fail closed.

## Task1: coordinator and behavioral tests

Files: create `skills/agentic-vault/scripts/vault_improvements.py`, `tests/test_vault_improvements.py`.

- [ ] Write temp-vault failing tests using real native run receipts; missing module is an explicit assertion failure.
- [ ] Observe RED; save command/result to this plan's local progress record.
- [ ] Implement the five public functions and CLI exactly as spec; reuse native validation/storage.
- [ ] Test pending/keep_base/abstain/candidate, invalid lineage/contracts and reflection evidence boundaries, unsafe/stale/corrupt input and content-free CLI errors.
- [ ] Run scoped tests GREEN; self-review and report no hidden global changes.

## Task2: offline example and discoverable documentation

Files: create `scripts/evaluate_improvement_experiments.py`, `docs/improvement-experiments.md`; update `README.md`, `docs/README.md`, skill and Codex routing references; ensure new helper distribution checks if needed.

- [ ] Use exact Task1 function signatures and JSON fields from spec.
- [ ] Build deterministic synthetic temporary-vault example, real four-arm receipts, assertions for compare and NO_ENTRY/lesson record.
- [ ] Describe defaults, limitations, full input schema, next existing proposal review path and source credit.
- [ ] Run example and help/doc checks. No API keys, external models or real vault content.

## Task3: integration verification and independent review

- [ ] Run full unittest discovery, existing required offline evaluation commands and compile/diff/plugin validation.
- [ ] Independent reviewer re-runs scoped tests and example, checks all five review-focus boundaries.
- [ ] Fix concrete failures, rerun affected checks, preserve initial failure records.
- [ ] Commit only feature files locally; no push or publication. Record result in vault with six handoff fields and partial shared-note changes.
