# Recall support and lesson observation implementation plan

**Goal:** Ship opt-in authored evidence assessment and two honest evaluation tools.
**Spec:** `docs/superpowers/specs/2026-11-10-recall-support-design.md`.
**Architecture:** Existing recall remains unchanged. Independent assessor and
checkout evaluators use stdlib, source-attributed literal observations and explicit
trial provenance. Root integrates docs/resources/release and published registry.
**Tech Stack:** Python 3.10+ standard library, unittest, existing plugin packaging.

## Global constraints

- No new production dependency or default recall/hook/config change.
- No external dataset/code copying, company input, automatic model/host execution.
- Exact declared evidence is not semantic truth, source freshness, permission or
  proof of authentic/causal actual-host efficacy.
- Every tool validates bounded inputs and distinguishes missing/unobserved/error.
- User authorized implementation and publication; no additional approval loop.

## Review focus

- A quote in the wrong source or unrendered snippet must not count as evidence.
- Scan completeness and context truncation cannot imply an unsupported absence.
- Missing or conflicting conditions cannot be collapsed to all-supported.
- Trial label or hash cannot authenticate actual-host execution or causal effect.
- Missing/stale/unpaired trial metrics cannot be silently converted to zero.

## Tasks

1. Assessor: failing tests, `vault_recall_support.py`, focused tests, API guide.
   Interface and contract are in the spec; no filesystem reads. Reviewer probes
   label injection, duplicate quotes, offsets, truncation and malformed shapes.
2. Real-recall stress evaluation: failing tests, evaluator, original fixtures,
   normal versus naive control run, guide. Consumes task 1 API. Reviewer reruns
   the real pipeline and checks 524288-byte UTF-8 fixtures and scope labels.
3. Lesson effects: failing tests, strict receipt scorer and scripted demo,
   focused tests, guide. Independent of tasks 1/2. Reviewer checks pair/confound
   rejection and that no demo observation is presented as actual host efficacy.
4. Integration: README/skill discovery/resource inventory, version 0.45.0,
   changelog/release/verification contract and research attribution. Full suite,
   independent review and any fixes, release assets, exact CI, public release,
   public byte/attestation checks and pinned Atlas update.

## Execution ledger

- Base: `a969abc655c298df58ef500e901ea52cb0d3d7e8`.
- Workspace: external linked worktree `D:/NS-AX-worktrees/agentic-vault-v0450`.
- Ruling: the previous proposal and current “implement and publish” authorize
  execution; use the established runtime and release workflow without another
  design approval question. Three disjoint file sets permit parallel tasks.
- In progress: tasks 1–3; root owns shared metadata, release and Atlas integration.
