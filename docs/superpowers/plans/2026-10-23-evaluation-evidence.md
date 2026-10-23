# Evaluation Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bind evaluator identity and record source-bound, explicitly qualified improvement costs.

**Architecture:** Optional run-manifest evaluator binding plus a separate immutable cost-receipt tool using the existing native store. Preserve legacy outputs by omitting all optional data unless requested.

**Tech Stack:** Python 3.10+ standard library, unittest.

**Spec:** `docs/superpowers/specs/2026-10-23-evaluation-evidence-design.md`

## Global Constraints

- Python 3.10+ standard library only; no model/network/command execution or automatic application.
- Selected files stay inside the existing vault path policy; no secret or reserved-store reads.
- No version bump, remote push, publication or active installation.

## Review Focus

- Old manifests and IDs must stay byte-identical without new arguments.
- Recomputed checksums must not disguise evaluator drift or report/data inconsistency.
- Unknown cost must never become zero or a cost-effectiveness claim.
- Repeated/shared evidence must not silently double-count costs.
- Publish-time races must recheck native inputs, report and evidence.

### Task 1: Optional evaluator identity

Files: `skills/agentic-vault/scripts/vault_runs.py`, `tests/test_run_evaluator_binding.py`.

- [x] Write failing tests for optional API/CLI, unchanged old outputs, code/config mutation, comparisons, role overlap and bounded unsafe inputs.
- [x] Run tests and preserve RED evidence.
- [x] Implement spec's optional evaluator contract; include it in currentness and comparison checks, and exclude it from observation evidence.
- [x] Run evaluator tests plus existing runs/improvements tests; inspect failures.

### Task 2: Source-bound cost accounting

Files: `skills/agentic-vault/scripts/vault_run_costs.py`, `tests/test_run_costs.py`.

- [x] Write failing tests for spec's report schema, immutable record/inspect, four-phase totals, failed/discarded entries, unknown propagation, comparison and selected aggregation.
- [x] Run tests and preserve RED evidence.
- [x] Implement `record`, `inspect`, `summarize`, `compare` and safe CLI; bind/recompute source data and recheck on publish.
- [x] Run all new cost tests; preserve green output and perform self-review.

### Task 3: Integration, documentation and review

Files: `scripts/evaluate_improvement_accounting.py`, `tests/test_improvement_accounting.py`, package validators, `README.md`, `docs/README.md`, `docs/tower-reliability.md`, `docs/improvement-experiments.md`, `docs/improvement-accounting.md`, command/skill routing where appropriate.

- [x] Test a bounded synthetic baseline/candidate example with evaluator binding, mixed observations, a discarded attempt and evaluator mutation rejection.
- [x] Document JSON and CLI use, trust limits, full-scope declaration and currency/basis restrictions.
- [x] Run affected tests and existing offline/package gates, then complete suite; verify old output against the pinned baseline.
- [x] Independent read-only review and targeted rerun; address substantive findings.
- [x] Commit only this feature, integrate locally after passing checks, and record NS handoff with evidence. Keep foreign dirty vault changes untouched.
