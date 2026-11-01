# Knowledge evolution implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add explicit source-change review, portable selected-note OKF output, and an offline source-evolution regression fixture.

**Architecture:** Independent opt-in Python CLIs reuse existing config/path/stable-read guards. Existing vault files and default hooks are untouched. Tests and final integration use isolated synthetic vaults.

**Tech Stack:** Python standard library, unittest, existing agentic-vault guard modules.

**Spec:** `docs/superpowers/specs/2026-11-01-knowledge-evolution-design.md`

## Global Constraints

- Python standard library only; no network, model calls or production-vault writes in new tools.
- Output paths and JSON are untrusted data, never execution authority.
- Preserve default hooks, current checker fingerprints, host behavior and unrelated working edits.
- Local implementation only; no release, network push or active-installation update.

## Review Focus

- Deleted or newly denied source must yield review/unavailable, never success by omission.
- Source or config changes during capture/export must not publish a fresh-looking result.
- Existing/colliding/escaped output paths must never overwrite unrelated files.
- Wiki links in code and unselected/ambiguous targets must not silently acquire fabricated relationships.
- Historical truth and missing evidence must not turn a changed hash into an unsupported falsehood judgment.

### Task 1: Change-impact inventory

**Files:** create `skills/agentic-vault/scripts/vault_impact.py`, `tests/test_vault_impact.py`, `docs/knowledge-impact.md`.
**Interfaces:** produces `capture_inventory(vault: Path, declarations: dict) -> dict` and `check_inventory(vault: Path, inventory: dict) -> dict` from the spec.

- [x] Write tests with literal expected claim/source states for unchanged, modified, removed, owner drift, malformed and denied inputs; CLI exit behavior; bounded reads and capture/config drift.
- [x] Run `python -m unittest discover -s tests -p test_vault_impact.py -v`; observe missing-feature failure before production code.
- [x] Implement the minimal versioned inventory and read-only checker using existing stable/path guards.
- [x] Re-run focused tests; document explicit dependency scope and interpretation limits.

### Task 2: OKF projection

**Files:** create `skills/agentic-vault/scripts/vault_okf.py`, `tests/test_vault_okf.py`, `docs/okf-export.md`.
**Interfaces:** produces `export_bundle(vault: Path, notes: list[str], destination: Path) -> dict`, success manifest as defined in spec; invalid input raises a bounded documented error and CLI exit2.

- [x] Write tests for two linked selected notes, YAML/type/index/manifest invariants, original metadata preservation, code literal preservation, unresolved/ambiguous links and embeds, reserved names, collision, denied paths, existing destination and mutation.
- [x] Run `python -m unittest discover -s tests -p test_vault_okf.py -v`; observe missing-feature failure.
- [x] Implement bounded selected reads, deterministic link projection and staged output publication.
- [x] Re-run focused tests; document producer profile, loss reporting and invocation.

### Task 3: Source-evolution fixture

**Files:** create `scripts/evaluate_knowledge_evolution.py`, `tests/test_knowledge_evolution.py`, `tests/fixtures/knowledge_evolution/*`, `docs/knowledge-evolution.md`.
**Interfaces:** consumes Task1/Task2 APIs; outputs deterministic per-checkpoint behavior and separately labelled structured-claim assessment with explicit denominators.

- [x] Author independent source history, expected statuses and structured claim labels including current contradiction/history support and absent evidence.
- [x] Write and run tests proving wrong historical classification and wrong impact/export behavior fail.
- [x] Implement offline replay of real APIs in temporary vaults; no output-derived expected values.
- [x] Run focused tests and evaluator CLI; document scope and limitations.

### Task 4: Delivery and verification

**Files:** update README, `skills/agentic-vault/SKILL.md`, its Codex reference, and relevant delivery allowlists/tests only as required by existing package rules.

- [x] Review independent components against design and repair findings with regression tests.
- [x] Expose commands, examples and boundaries consistently for both hosts, without default hooks or version/release claims.
- [x] Run full `python -m unittest discover -s tests -v`, relevant packaging/install checks and the evolution CLI; save actual evidence privately.
- [x] Obtain independent whole-branch review, run independent synthetic acceptance checks, commit and integrate locally, record vault handoff/ADR under current vault rules.

## Execution evidence

The independent 94-module checkpoint accounted for all 2,671 discovered IDs. Its reconciled result replaces three failed module runs with recorded serial reruns: 2,626 pass, 45 environment skips, no remaining failures/errors. The private runner required ROOT import-path and Windows spawn main-guard corrections; first failures remain preserved. This is not a single monolithic green run. A subsequent CRLF output-budget patch was checked by impact11/skip1, evolution4, README11, independent raw-byte boundary review, and native disposable Codex installation. Runtime checker-bound files and default hooks are unchanged. Detailed local evidence is in `.superpowers/sdd/2026-11-01-knowledge-evolution/final-acceptance.json`.
