# Tower-inspired reliability implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent components and one final integrated review. The user has authorized implementation; routine design choices remain with the implementation team.

**Goal:** Implement all five approved reliability improvements on the existing file-based engine.

**Architecture:** Add explicit stdlib helpers for frozen runs, connector diagnostics and operation receipts. Extend the existing single-note proposal lifecycle with conflict-aware undo. Reuse current path, configuration, evidence and cooperative-write contracts.

**Tech Stack:** Python 3.10+ standard library, existing Markdown/JSON records, unittest and Git.

**Spec:** `docs/superpowers/specs/2026-10-10-tower-hardening.md`

## Global Constraints

The spec's Global constraints bind every task. No new dependencies, ambient network, automatic promotion/rollback, secret output, weakened vault path policy, or host-wide execution claims. Existing startup/hook behavior and settings stay compatible.

## Review Focus

- A candidate version changes while the scenario stays fixed: evaluation remains comparable only with matching actual test/model/budget coverage.
- Another writer changes a note between preview and rollback: preserve the later writer's content and fail stale.
- A remote action succeeds but its receipt write fails: uncertainty must prevent automatic replay.
- A credential is present but invalid or never tested: diagnostics must remain failed/unknown rather than claim working authentication.
- Inputs point to denied/link/hardlink/reserved paths or contain secret/oversized/nonfinite/duplicate data: reject before reading or persisting sensitive contents.

## Task 1: Frozen runs and behavioral comparisons

Files: new `skills/agentic-vault/scripts/vault_runs.py`, `tests/test_vault_runs.py`, feature-specific fixtures if needed.
Interfaces: prepare/record/inspect/compare Python APIs and CLI documented in the implementation report. No sibling helper dependency; use existing state/evidence/path helpers.
- [x] Write and run red tests for different candidate/same fixture compatibility, immutable identity, stale sources and hard-fail veto.
- [x] Implement bounded source-bound run receipts and repeated outcome comparison.
- [x] Run all tests for the helper, including CLI and unsafe-input controls; save red/green commands/results and exact API/schema.

## Task 2: Single-proposal undo

Files: `vault_proposals.py`, `tests/test_vault_proposals.py` and/or a focused rollback test module.
Interfaces: rollback-preview and explicit rollback operations; retain existing proposal/apply receipts.
- [x] Write and run red tests for preview, exact-byte restoration, later edits, stale preview and interrupted rollback.
- [x] Implement under existing proposal locks/path policy with history retained and no automatic execution.
- [x] Run proposal/metrics regression tests; report CLI/schema and mutation boundaries.

## Task 3: Connector stage diagnostics

Files: new `vault_connectors.py`, `tests/test_vault_connectors.py`. Root handles doctor/skill documentation integration.
Interfaces: offline diagnose and explicit supported probe; installed/configured/credential/authentication/operation states and next action.
- [x] Write and run red tests for present-but-unprobed credentials and invalid/unsupported provider states.
- [x] Implement bounded, redacted existing-provider checks; no .env reads or Jarvis launch.
- [x] Run new tests and existing doctor/adapters tests; report exact CLI/schema.

## Task 4: Scoped operation receipts

Files: new `vault_operations.py`, `tests/test_vault_operations.py`.
Interfaces: begin/finish/uncertain/inspect (and explicit reconciliation if needed), canonical caller-owned intent and parameter identity.
- [x] Write and run red tests for duplicate concurrent begin, result reuse, stale owner, reordered parameters and lost result after success.
- [x] Implement a bounded journal atop existing vault runtime path policy and cooperative locks.
- [x] Run subprocess/CLI, uncertain-state and redaction tests; report exact caller contract.

## Task 5: Integration and independent verification

Files: feature guide, SKILL/reference/command links and README, evaluation entrypoint if needed; user-owned vault records handled separately by root.
- [x] Document functional CLI examples, existing-approval meaning, source adaptation and precise limits.
- [x] Integrate all components; run full unittest, existing recall/workflow gates and compileall once.
- [x] Supply final diff/spec/plan to a separate read-only reviewer; fix concrete findings and rerun covering tests.
- [x] Commit only owned work, record six-field handoff and exact verification evidence. Do not leave own changes uncommitted.
