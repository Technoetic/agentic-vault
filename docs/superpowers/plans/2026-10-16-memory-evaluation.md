# Korean memory evaluation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Parallel file ownership is explicit below.

**Goal:** Make memory answers, stale evidence, operation sequences and synthetic tool safety independently measurable.

**Architecture:** Add evaluation tools beside existing scripts. Reuse recall and real lifecycle APIs without changing default plugin behavior. Tests and fixtures operate on invented data and temporary vaults.

**Tech Stack:** Python 3.10+, unittest, development-only pinned Hypothesis; explicit optional loopback model endpoint.

**Spec:** `docs/superpowers/specs/2026-10-16-memory-evaluation.md`

## Global constraints

- Preserve stdlib runtime, existing CLI contracts, Markdown/git authority and default no-network behavior.
- No real vault input or real external actions in the new tool safety driver.
- Distinguish deterministic controls, observation replay and actual model execution.
- Missing usage/billing is null, not zero; labels never enter answerer/model inputs.
- Do not copy third-party code or datasets. Cite research and document scope.

## Review focus

- Correct retrieval with a wrong answer must fail answer grading independently.
- Historical mention of an old value must not be mislabeled a current stale assertion.
- Changed context or source withdrawal must invalidate previous replay evidence.
- Generated operation retries must preserve unrelated edits and not repeat target writes.
- A reachable synthetic attack followed by a model error remains an achieved attack.

### Task 1: Korean structured-answer evaluation

**Files:** `scripts/evaluate_korean_memory.py`, `tests/test_korean_memory_evaluation.py`, `tests/fixtures/korean_memory/**`.

**Interfaces:** additive `evaluate(...)` and CLI; reuse `vault_retrieval.retrieve`. Keep fixture labels separate from question/evidence payload. Export explicit per-case evidence, answer, execution and cost sections.

- [ ] Write failing tests for separate answer/evidence grades, labels isolation, current/historical scopes, abstention/errors, withdrawal/hash binding and null usage.
- [ ] Observe the expected RED failures.
- [ ] Implement strict bounded response/observation validation and at least40 synthetic Korean cases, including deliberate wrong-answer controls.
- [ ] Run focused tests and default CLI; verify no model calls/default behavior changes.

### Task 2: Stateful lifecycle regressions

**Files:** `tests/test_stateful_memory_lifecycle.py`, `requirements-dev.txt`; production fixes only when a generated sequence proves a defect.

**Interfaces:** `vault_proposals.Store` and fresh locks/receipts; real apply/rollback, `vault_operations` begin/finish/uncertain/reconcile. Independent state expectations and target byte observations.

- [ ] Define invariants and a deliberately broken model/target-write control that proves the test can detect stale writes or duplicated apply.
- [ ] Observe expected failure, then implement state machines with proper lifecycle semantics.
- [ ] Run seeded and exploratory generation; preserve minimized regressions if a real defect occurs.
- [ ] Verify normal unittest discovery works without Hypothesis and the explicit stateful command has zero skips with it installed.

### Task 3: Optional actual tool-call evaluation

**Files:** `scripts/evaluate_tool_safety.py`, `tests/test_tool_safety_evaluation.py`; optional separate transport helper only if needed, no existing planner return-type change.

**Interfaces:** built-in synthetic episodes, actual chat `tools`/`tool_calls`/tool results, independent world graders. Offline scripted responses exercise the same dispatch path but are labeled controls.

- [ ] Write failing tests for labels isolation, reachable canary attack, unsupported protocol, final attack after later error, resource budgets and proxy/redirect restrictions.
- [ ] Observe RED and implement bounded dispatcher, transport and result schema.
- [ ] Run paired clean/attacked scripted controls, refuse invalid actual-model output, and expose explicit optional CLI parameters.
- [ ] Run actual local-model smoke only if a supported local endpoint is available; retain exact observed limits and never substitute controls silently.

### Task 4: Integration and independent verification

**Files:** `.github/workflows/tests.yml`, `docs/memory-patterns.md`, additive usage/results documentation, README concise pointer.

- [ ] Run the baseline suite, install the development dependency in an isolated environment, then integrate explicit new CI commands.
- [ ] Run full suite and all deterministic evaluator CLIs; check examples, invalid inputs, generated controls and source policy.
- [ ] Request fresh independent code review and rerun reported issues to resolution.
- [ ] Commit only owned product files, record exact evidence and unresolved actual-model scope, then record the completed work in the existing vault topic/index/log/handoff without staging peer changes.
