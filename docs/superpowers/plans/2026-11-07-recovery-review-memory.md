# Recovery, review planning and generated memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Publish v0.42.0 with three opt-in improvements and actual reference evidence.
**Architecture:** Independent bundle journal, read-only review planner and isolated generated-memory evaluator reuse existing policy/provenance/transport primitives. Default runtime and seven certificate-bound checkers remain unchanged.
**Tech Stack:** Python3.10+ standard library, unittest, existing CI and Codex/Claude packaging.
**Spec:** `docs/superpowers/specs/2026-11-07-recovery-review-memory.md`

## Global Constraints

- Existing defaults/hooks/config/checker certificate bytes unchanged; no active installation/company vault changes.
- C1 forward recovery only; no multi-file atomic visibility or universal power-loss guarantee.
- C2 declared graph only, data-only and no automatic editing/recapture.
- C3 synthetic checksum vault only; no default model/network call or real-vault/dataset path.
- Fresh actual verification counts; authorized GitHub release/Pages, no Sites.

## Review Focus

- An interrupted replace before receipt must recover by exact observed bytes, never guessed progress.
- Stale/denied/unknown target state must prevent further writes even in recovery.
- Unchanged downstream Markdown must still receive inherited review-required state.
- Later model requests must depend on actual persisted generated memory, not a cached fixture.
- Scripted results, missing adoption and unsent/error plans must never count as actual model repair.

### Task1: Recoverable bundles

**Files:** `vault_bundle.py`, `tests/test_vault_bundle.py`, `docs/recoverable-bundles.md`.
**Interfaces:** prepare_bundle/apply_bundle/bundle_status/recover_bundle per spec; no other production file edits.

- [ ] Write and run focused failing interruption/conflict/guard tests.
- [ ] Implement explicit journal and deterministic cooperating lease protocol.
- [ ] Run focused regression and relevant existing state tests; self-review, commit owned files.
- [ ] Independent spec+quality review and fix concrete findings.

### Task2: Review planning

**Files:** `vault_review_plan.py`, `tests/test_vault_review_plan.py`, `docs/review-planning.md`.
**Interfaces:** plan_review(vault,inventory,graph)->data-only bounded output.

- [ ] Write and run failing chain/diamond/cycle/input guard tests.
- [ ] Implement explicit graph propagation and deterministic bounded worklist.
- [ ] Run focused plus unchanged impact tests, self-review and owned commit.
- [ ] Independent spec+quality review and fix concrete findings.

### Task3: Generated-memory lifecycle

**Files:** new evaluator/helpers, focused tests, `docs/generated-memory-lifecycle.md`.
**Interfaces:** evaluate_generated_lifecycle per spec; use only new modules unless a documented tiny shared-helper extraction is necessary and root agrees.

- [ ] Write and run failing real persistence/fresh-worker/authorship/adaptive/repair-control tests.
- [ ] Implement strict authored action memory, production lesson persistence/compile and bounded coordinator.
- [ ] Run focused offline/mock-HTTP tests; self-review and owned commit.
- [ ] Independent adversarial/spec review and fix concrete findings.

### Task4: Integration, verification, publication and catalog

**Files:** root-owned README/SKILL/commands/references/THIRD_PARTY/CHANGELOG/version/manifests/installers/release docs as required by current packaging.

- [ ] Add consistent opt-in command docs and exact pinned reference-use differences.
- [ ] Run complete final source suite, install/certificate/native checks and broad independent review.
- [ ] Integrate master, pass exact master/tag required CI, independently verify archive/draft; publish immutable Latest and verify fresh public bytes and serialized attestations.
- [ ] Update/independently verify/publish Reference Atlas without duplicate sources/images or Sites.
- [ ] Record NS actual evidence, local-only commit/mirror and MemoryHub closure.
