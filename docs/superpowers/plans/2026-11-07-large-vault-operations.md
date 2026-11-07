# Large-vault operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Publish v0.43.0 with opt-in large-vault and shared-worktree tools, the defect fixes they exposed and actual reference evidence.
**Architecture:** Each unit adds its own module or option and reuses existing state, recall, deny-zone and lesson primitives. Default hook bindings, the configuration template and seven certificate-bound checkers remain unchanged.
**Tech Stack:** Python3.10+ standard library, unittest, existing CI and Codex/Claude packaging.
**Spec:** `docs/superpowers/specs/2026-11-07-large-vault-operations.md`

## Global Constraints

- Bound files, `hooks/hooks.json` and configuration templates unchanged; no active installation or company vault changes.
- New behavior is opt-in except listed defect fixes, each with its trade stated in the release notes.
- Synthetic numbers stay labeled synthetic. No unmeasured effect is claimed.
- Fresh actual verification counts; authorized GitHub release and Pages, no Sites.

## Review Focus

- Absent settings must leave recall matches, ranking and digests unchanged.
- A fold must refuse before writing when its output does not rebuild the original.
- Diagnostics must print closed words and counts, never commands or note text.
- A cue hook must never answer `allow` or `ask`, and a failure must let the call run.
- A linked tree of another repository must stay an ordinary folder.
- `vault_commit.py` must check the commit Git named, never a fresh look at `HEAD`.
- The rule wording must keep prohibitions first and must not be reported as a gain.

### Task1: Recall scope

**Files:** `vault_recall.py`, `vault_retrieval.py`, `vault_query_expansion.py`, `scripts/evaluate_recall.py`, tests, `docs/recall-scope.md`.

- [ ] Write failing bound, priority and diagnostic tests with fixed digests.
- [ ] Implement the optional block and shared reader budget.
- [ ] Run focused and retrieval regressions; commit owned files.

### Task2: State, capacity and staged growth

**Files:** `vault_state.py`, `hooks/session_checkpoint.py`, `vault_capacity.py`, `vault_doctor.py`, `vault_healthcheck.py`, tests, `docs/capacity.md`.

- [ ] Write failing code, checkpoint, estimate and growth tests.
- [ ] Implement distinct codes, hash-only checkpoint reads and read-only reporting.
- [ ] Run focused regressions; commit owned files.

### Task3: Handoff folding

**Files:** `vault_handoff_fold.py`, `scripts/evaluate_handoff_fold.py`, fixture, tests, `docs/handoff-fold.md`, `commands/vault-harden.md`.

- [ ] Write failing byte rebuild, drift, framing and evaluator control tests.
- [ ] Implement analyze, apply and the offline loss alarm.
- [ ] Run focused regressions; commit owned files.

### Task4: Session evidence and plugin scope

**Files:** `vault_session_evidence.py`, `vault_plugin_scope.py`, `vault_doctor.py`, tests, `docs/reliability.md`.

- [ ] Write failing output, pairing, scope and host failure tests.
- [ ] Implement closed-word reports without reading hook commands.
- [ ] Run focused regressions; commit owned files.

### Task5: Lesson recurrence and cues

**Files:** `vault_lesson_clusters.py`, `vault_lesson_cues.py`, `vault_lesson_delta.py`, `vault_adapters.py`, `assets/adapters/lesson-cues/`, tests, `docs/lesson-recurrence.md`.

- [ ] Write failing marker byte, ignored row, class and hook decision tests.
- [ ] Implement the report, compiler and standalone adapter.
- [ ] Run focused regressions; commit owned files.

### Task6: Verifier fidelity

**Files:** `vault_compile_quality.py`, `scripts/evaluate_verifier_fidelity.py`, `scripts/env_vary.py`, tests, `docs/verifier-fidelity.md`.

- [ ] Write failing fence, zero-fact and unit tests, then the mutation table.
- [ ] Implement the fixes and state the false alarm trade.
- [ ] Run focused regressions; commit owned files.

### Task7: Linked worktrees

**Files:** `vault_worktrees.py`, walkers, direct-path callers, `assets/adapters/deny-guard/`, tests, `docs/hardening.md`.

- [ ] Write failing tests on real linked trees, submodules and foreign trees.
- [ ] Implement identification, walks, refusals and the guard view.
- [ ] Run one synthetic host check; commit owned files.

### Task8: Declared-path commits

**Files:** `vault_commit.py`, tests, `docs/concurrent-commits.md`, `commands/vault-session-end.md`.

- [ ] Write failing hash, state, retry and multi-process tests.
- [ ] Implement default mode and `--own-lines`.
- [ ] Run focused regressions; commit owned files.

### Task9: Constraints note, rule and sample

**Files:** `hooks/session_start.py`, `assets/templates/rules/vault-workflow.md`, lifecycle evaluators, deny-guard guide, tests, `docs/hardening.md`.

- [ ] Write failing rendering and budget tests.
- [ ] Choose rule wording by prohibition retention on a synthetic contrast and report it plainly.
- [ ] Run focused regressions; commit owned files.

### Task10: Integration, verification, publication and catalog

**Files:** root-owned README/SKILL/commands/references/version/manifests/installers/release docs as required by current packaging.

- [ ] Add consistent opt-in docs, package lists and offline CI steps, with exact pinned reference differences.
- [ ] Run complete final source suite, install/certificate/native checks and broad independent review.
- [ ] Integrate master, pass exact master/tag required CI, independently verify archive/draft; publish immutable Latest and verify fresh public bytes and serialized attestations.
- [ ] Update, independently verify and publish Reference Atlas without duplicate sources or Sites.
- [ ] Record actual evidence in the private project notes.
