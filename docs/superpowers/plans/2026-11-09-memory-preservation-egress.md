# Memory preservation and revocation egress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish an opt-in, source-bound record preservation extension, real configured egress projection and independently measured synthetic regressions.

**Architecture:** Keep existing literal checks/default outputs. Add strict authored record scopes, use the existing request-scoped revocation policy at two missing context boundaries, and reuse the current development-only Hypothesis dependency.

**Tech Stack:** Python standard library runtime; existing Hypothesis6.168.5 for generated tests; existing unittest/native Codex/release tooling.

**Spec:** `docs/superpowers/specs/2026-11-09-memory-preservation-egress-design.md`

## Global Constraints

- No new production dependency, live model test, company input, automatic apply or active installation change.
- Omitted new relation option and absent revocation configuration preserve existing result/prompt behavior.
- Limits: 1..64 authored probes, anchors <=256 chars, existing bounded source/request reads and current hash/path policies.
- Compare NFC records; do not claim general semantic correctness or universal poisoning protection.
- Outbox/index wording must reflect simulated sink/current fresh reads, not nonexistent persistent infrastructure.

## Review Focus

- Duplicate matching prose must not hide a changed record.
- BOM/CRLF filtering must occur on original bytes before current display normalization.
- A stale prompt envelope or configuration change between preparation and dispatch must fail closed when configured.
- Unknown scope, path, report observation or missing expected case must not count as success.
- Release default-compatibility, native installed resources and exact CI/ZIP source identity must be checked independently.

## Tasks

- [ ] T1 — Tests first, then `vault_relation_probes.py`, compile/preserve plumbing, optional CLI manifest guards, `tests/test_relation_probes.py`, `tests/test_stateful_relation_probes.py`, documentation and actual red/green records. Owner: relation implementer.
- [ ] T2 — Tests first, then Jev/Jarvis configured revocation projection/envelope checks, necessary helper, consumer regressions, `tests/test_stateful_revocation_egress.py`, bounded evaluator/fixtures and documentation. Owner: egress implementer. Never send an API/subprocess request in tests except mocks.
- [ ] T3 — Independent authored relation fixture/evaluator and negative control, source/fixture fingerprints, paired summary and meaningful evaluator tests. Owner: controller; coordinate the T1 callable contract before import.
- [ ] T4 — Version v0.44.0, README/install/current refs, native inventory, explicit required stateful CI modules, release notes/limits and public research citations. Owner: release metadata implementer. Do not invent verification counts.
- [ ] T5 — Review spec/code in independent roles; run relevant baseline/focused/full suites and required generated tests; fix actual findings. Controller owns integration commits and exact source identity.
- [ ] T6 — Publish tested master/tag and immutable release assets; verify fresh anonymous downloads/checksums/attestations/native install; update and independently check Reference Atlas with pinned public evidence; complete vault records. Controller only.

## Verification commands

- Focused baseline: unittest modules for compile quality, proposal preservation and revocation integration.
- Full suite: `python -B -X faulthandler -m unittest discover -s tests -t . -v`.
- Stateful: existing module plus both new modules under an environment that actually has requirements-dev installed.
- Packaging: `scripts/release_rebuild.py build/check`, independent archive/Git-blob comparison, leak scan and actual published attestation verification; use the release audit for exact current flags.

User explicitly authorized implementation and publication after the cited research. No renewed approval gate is needed for these reversible implementation choices. A new destructive or unrelated action still requires separate authorization.
