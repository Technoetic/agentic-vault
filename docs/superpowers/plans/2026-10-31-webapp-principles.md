# Webapp Principles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Embed the six webapp rules in a discoverable cross-host skill and
verify that source/installed packages deliver the correct reference.

**Architecture:** One canonical guide, one independently discoverable skill,
small links from existing entry points, and extensions to the existing isolated
Codex verifier. No engine feature, global hook or application patching.

**Tech Stack:** Markdown, Python standard-library unittest, existing native
Codex plugin/app-server verifier. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-31-webapp-principles-design.md`.

## Global Constraints

- Scope webapp implementation/change/review; ordinary vault notes do not start
  app work or load app requirements solely because they mention a website.
- Preserve explicit user/project architecture and existing host workflows.
- Source-only integration: no version bump, push, publication, real-cache
  install, live application modification or vault migration.
- Preserve current hooks and untrusted-hook behavior in isolated host tests.
- Publish only generic rules and synthetic observations, no organization data,
  private file paths, credentials or previous conversation dumps.

## Review Focus

- Clean next navigation must update automatically; a manual reload suggestion
  cannot satisfy the full open-tab rule.
- Loaded build baseline must come from the document; rollback and startup races
  must not falsely report current code as new.
- Dirty/in-flight forms and transient failures must retain user work.
- Native discovery missing/disabled/duplicate/nonexistent skill resources must
  fail verification while old result fields remain compatible.
- This is guidance/discovery, not proof that all agents or apps automatically
  comply; real-app checks remain required.

### Task 1: Skill and canonical contract

- [x] Observe a no-skill baseline plan before writing new skill text.
- [x] Add scoped `skills/webapp-principles/SKILL.md` and canonical
  `docs/webapp-principles.md`, keeping the short trigger/reference separation.
- [x] Run the identical synthetic scenario with the skill and report the
  observed differences accurately; include read-only and existing-framework
  counterexamples.

### Task 2: Delivery verifier

- [x] Write failing skill-discovery and missing/changed resource tests in
  `tests/test_codex_verification.py`.
- [x] Require both enabled native skills in `scripts/verify_codex_plugin.py`,
  retaining the old primary `skill` field and all hook invariants.
- [x] Compare installed new skill, canonical guide and changed core skill
  bytes; run focused tests to GREEN.

### Task 3: Entry points and integration

- [x] Link the two resources in README/docs index/Codex guide/core skill and
  Codex reference; avoid default hook injection or a workflow-name collision.
- [x] Run appropriate repository tests and native isolated-home discovery.
- [x] Obtain independent code/content review, fix concrete findings and verify
  frozen file hashes. Record required six-field handoff and `verified_by`.

### Integration handoff

After verification, local commit/fast-forward and the scoped project-thread
record are the next authorized operations. Their actual completion is recorded
in the project handoff receipts, not predeclared in this pre-commit plan.
Preserve other agents' working edits through scoped projected staging.
