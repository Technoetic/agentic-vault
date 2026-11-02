# Memory Trust Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use
> checkbox syntax and must be verified before completion.

**Goal:** Preserve provenance through optional memory preparation and evaluate
repair on actual model behavior without changing normal vault execution.

**Architecture:** A pure optional trust helper supports original offline controls.
A separate lifecycle model adapter reuses persisted, source-bound lifecycle
operations and existing restricted loopback transport. Existing hooks are intact.

**Tech Stack:** Python 3.10+, unittest, existing standard-library vault modules.

**Spec:** `docs/superpowers/specs/2026-11-03-memory-trust.md`.

## Global Constraints

- Standard-library runtime only, optional invocation, no real-vault/model defaults.
- Caller-attested metadata and source integrity never grant authority or truth.
- No publication, installation, company configuration, or version bump.

## Review Focus

- Forged roles inside Unicode/JSON/fenced content retain their original role.
- Duplicate IDs, changed quotes/hashes, forged observation references fail closed.
- Empty budgets never pass as selective preservation or successful repair.
- Missing/malformed/model errors count as errors, not correct outputs.
- Every live request excludes gold answers, arm labels and scoring metadata.

## Task 1: Typed source-preserving memory helper

Files: `skills/agentic-vault/scripts/vault_memory_trust.py`,
`tests/test_vault_memory_trust.py`, `docs/memory-trust.md`.
Interfaces: `prepare_context(records, *, max_tokens=1500)`;
`review_claims(records, claims, *, observations=[])`. Records have `id`, `role`
(`user|assistant|tool|external`) and `text`. Claims reference `source_id`,
`source_sha256`, exact `quote`, `kind` (`observation|user_instruction|experience`),
and optional `observation_id`. Observations have `id`, `source_id`,
`source_sha256`, `quote`, `result` (`passed|failed`), provided separately by host.
Returned assessment is advisory with `authorization_granted: false`.

- [x] Write and run failing adversarial/retention/boundary tests.
- [x] Implement minimal bounded pure functions and content-free CLI.
- [x] Re-run tests, document caller-attested limits, independent review.

## Task 2: Paired actual-model lifecycle runner

Files: `scripts/evaluate_model_memory_lifecycle.py`,
`tests/test_model_memory_lifecycle.py`, optional additive private context snapshot
in `scripts/evaluate_memory_lifecycle.py`, `docs/model-memory-lifecycle.md`.

- [x] Write failing tests for actual persisted context and model/error distinction.
- [x] Reuse fresh-process lifecycle operations; add restricted loopback opt-in.
- [x] Use known simulated safe/unsafe responses for contract tests, no fake model
  score. Record actual usage/latency and pair/source/prompt hashes.
- [x] Re-run tests and unchanged old evaluator; independent review.

## Task 3: Original laundering and poisoned-experience controls

Files: `scripts/evaluate_memory_trust.py`, `tests/test_memory_trust_evaluation.py`.
Consumes the Task 1 interfaces; supports bounded and vulnerable named controls.

- [x] Write failing source-laundering and benign-preservation tests.
- [x] Implement original bilingual scenarios, structured failures and controls.
- [x] Demonstrate vulnerable controls expose the intended errors; review.

## Task 4: Integration and verification

- [x] Add discoverable CLI/docs links, installation checks if required, offline CI
  control step and research attribution with explicit limits.
- [x] Run all new tests, full existing suite, old/new evaluators, compile and diff.
- [x] Obtain independent whole-change review and resolve material findings.
- [x] Commit and integrate locally; record exact evidence and remaining scope.

## Verified outcome

Full suite: 2789 run, 2742 passed, 47 environment skips, no failures/errors.
Fresh new functional tests: 50 passed; installed-resource tamper test passed.
Independent review: 95 run, 93 passed, 2 platform skips; material metric finding fixed.
Actual local Qwen: 16 completed plans, zero errors; strict contamination condition
not met, so repair-effect credit remains zero. Native disposable Codex resources
and both Claude manifests verified. Publication/active installation unchanged.
