# Skill Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish optional source-bound multi-skill routing evaluation.

**Architecture:** Strict bounded JSON contracts, ordered multi-skill grader,
catalog analyzer and report comparison share one canonical binding format.
Caller observations and synthetic controls stay separate from model execution.

**Tech Stack:** Python3.10+ standard library; existing unittest and CI matrices.

**Spec:** docs/superpowers/specs/2026-11-04-skill-routing-design.md

## Global Constraints

- Checkout-only; no dependency, hooks, installed resources, or permission change.
- No network, subprocess, models, automatic edits or source-body execution.
- Missing/unsupported observations are unmeasured, never a passing non-trigger.
- NS company data and credentials must not enter published fixtures/assets.

## Review Focus

- Contradictory/malformed expectation or binding cannot earn a pass.
- Neutral helpers do not hide forbidden calls or missing mandatory composition.
- Self-report/read/budget-error evidence cannot masquerade as invocation.
- Changed labels/runtime/cases/source cannot yield an unsupported A/B improvement.
- Symlinks, large/nested JSON and unusual manifests fail without execution.

## Task1 — Contracts and grader

- [ ] Write failing tests for bindings, malformed inputs, required/forbidden/order,
  complete-empty versus missing observations, and evidence-level separation.
- [ ] Implement skill_routing_contract.py to pass; run targeted tests.

## Task2 — CLI and comparisons

- [ ] Write failing subprocess/CLI tests for output, exit status, report integrity,
  compatible regression comparison, missing observations and no overwrite.
- [ ] Implement evaluate_skill_routing.py; test through real CLI.

## Task3 — Catalog collection

- [ ] Write failing tests for explicit owned roots, frontmatter escaping,
  duplicate identities, symlinks, unsupported syntax and advisory overlap.
- [ ] Implement skill_routing_catalog.py; scan repository's two real skills.

## Task4 — Public controls and docs

- [ ] Author >=24 KR/EN cases, frozen synthetic catalog and bound traces.
- [ ] Document actual command examples, evidence limitations and upstream credit.
- [ ] Add a deterministic CI smoke and replay the published controls.

## Task5 — Integration and publication

- [ ] Independent adversarial/fixture review, fix and rerun relevant checks.
- [ ] Full local suite plus existing required CI for final source.
- [ ] Version metadata, reproducible archive, provenance and immutable release.
- [ ] Verify public download/CI/assets and record scoped NS handoff.
