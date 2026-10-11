# MoAI Diagnostics Implementation Plan

> **For agentic workers:** Apply test-driven-development per task and independent review before release.

**Goal:** Add three source-bound, opt-in diagnostics and publish/install the verified release.

**Architecture:** Small independent Python helpers reuse the existing safe path and evidence
interfaces. Doctor integration is opt-in; index fold and coverage expose read-only CLIs.

**Tech Stack:** Python 3.10+ standard library, unittest, Git/GitHub, existing plugin CLIs.

**Spec:** `docs/superpowers/specs/2026-10-11-moai-diagnostics.md` (accepted three-feature scope).

## Global Constraints

- No default network/model call, automatic apply, note deletion/move or production migration.
- Root AGENTS takes precedence; legacy CLAUDE is a fallback, not a double count.
- Reuse deny/secret/link/hardlink/stable-read policy; all inputs and traversals are bounded.
- Static characters and declared mappings do not certify runtime execution or quality.
- Preserve existing CLI defaults, evidence APIs, plugin scopes, paused NS workflows and hook trust.

## Review Focus

- Path aliases, reparse points and hardlinks must be rejected before denied file reads.
- Malformed/deep/oversized manifests and recursive import cycles must terminate safely.
- Stale hashes or changing source/destination files must invalidate complete previews.
- Duplicate IDs, overlapping original rows and missing required check declarations must be visible.
- Conditional rule inventory and caller-reported evidence must not become host-execution claims.

### Task 1: Instruction inventory

Files: new `vault_instruction_budget.py`, `tests/test_vault_instruction_budget.py`; opt-in `vault_doctor.py`.
Interface: `diagnose_instructions(vault, config) -> dict`; Doctor `--instructions`.

- [ ] Write tests for AGENTS precedence/imports/conditional rules plus safety/limit failure classes.
- [ ] Run focused discovery and record expected RED.
- [ ] Implement bounded metadata-only diagnostic with existing path/stable-read helpers.
- [ ] Rerun focused Doctor/inventory regressions; record GREEN and independent review.

### Task 2: Lossless index fold preview

Files: new `vault_index_fold.py`, `tests/test_vault_index_fold.py`.
Interface: explicit-manifest read-only Python CLI; exact schema documented from final implementation.

- [ ] Write tests for completed IDs/exact rows/hashes/destination reachability and stale/overlap cases.
- [ ] Run focused discovery and record expected RED.
- [ ] Implement bounded source-bound plan with preserved original records; no apply path.
- [ ] Rerun focused tests and independently review read-only/source preservation behavior.

### Task 3: Requirement coverage

Files: new `vault_coverage.py`, `tests/test_vault_coverage.py`.
Interface: explicit requirements/acceptance/check manifest plus existing evidence receipt; read-only CLI.

- [ ] Write tests for independently required IDs, mappings, missing/nonpassing results and stale bindings.
- [ ] Run focused discovery and record expected RED.
- [ ] Reuse evidence provenance and run completeness conventions without changing legacy callers.
- [ ] Rerun focused tests and independently review absent-check and truth/execution limitations.

### Task 4: Integration, publication and installation

Files: README/command/guide/index, release manifests/stamp/tests, release/verification documents.

- [ ] Document final CLI schemas and immutable MoAI attribution; align version 0.21.0.
- [ ] Run whole suite, four recall gates, deterministic workflows, compile and whitespace checks.
- [ ] Resolve independent review findings before commit; publish branch/PR and verify exact-source CI.
- [ ] Build immutable Git archive, compare every ZIP member to Git blobs, publish three assets and re-download.
- [ ] Update existing Codex/Claude plugin identities and independently validate files/scopes/preservation.
- [ ] Record six-field evidence in existing NS research/deployment records and commit only owned changes locally.
