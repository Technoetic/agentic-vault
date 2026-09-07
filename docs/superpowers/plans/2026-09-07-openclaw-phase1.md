# OpenClaw Phase 1 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Each task owns separate files; root handles shared documentation and commits.

**Goal:** Explain missing memory and safely review/apply one-file lesson improvements.

**Architecture:** A read-only doctor shares the hook's configuration/rendering semantics. A separate proposal CLI stores explicit revision-bound receipts and applies only reviewed user-owned Markdown changes.

**Tech Stack:** Python 3.10+, stdlib, unittest, Markdown, Git.

**Spec:** `docs/superpowers/specs/2026-09-07-openclaw-phase1.md`

## Global Constraints

- Python 3.10+, standard library only; Windows, Linux and macOS.
- Existing config and normal hook stdout/exit behavior stay compatible.
- Reuse path containment/deny-zone guards and bounded I/O.
- No network, model calls, secret access, installed-plugin changes, push or publication.
- Commands/proposals are data; never execute recorded validation commands.
- Root commits; implementers edit only their task-owned files and record actual test results.

### Task 1: Read-only doctor

**Files:** Create `skills/agentic-vault/scripts/vault_doctor.py`, `tests/test_vault_doctor.py`; optional refactor `hooks/session_start.py` plus new `skills/agentic-vault/scripts/session_context.py`; compatibility tests in `tests/test_session_start.py` only if required.

**Interface:** `python skills/agentic-vault/scripts/vault_doctor.py --vault PATH --format json`; text default. Follow Doctor statuses and redaction contract from the spec. No dependency on Task 2.

- [x] Write CLI filesystem tests first. The initial test runs the not-yet-present CLI and asserts that a valid fixture returns JSON with a ready hot section; this fails because the feature is absent. Add literal expectations for missing, empty, disabled, invalid and truncated fixtures, and compare file bytes before/after.
- [x] Run `python -m unittest tests.test_vault_doctor -v` and record the expected red result.
- [x] Implement the minimal shared diagnosis without changing hook output. Distinguish isolated readable state from effective hook output suppression. Do not include raw config values or note snippets.
- [x] Run `python -m unittest tests.test_vault_doctor tests.test_session_start -v`; fix failures and self-review against every Doctor acceptance case.
- [x] Write `.superpowers/sdd/2026-09-07-openclaw-phase1/doctor-report.md` with files, red/green commands/results, assumptions, unresolved and next safe action. Do not commit.

### Task 2: Revision-bound lesson proposals

**Files:** Create `skills/agentic-vault/scripts/vault_proposals.py` and `tests/test_vault_proposals.py`. Do not modify the checker/resolver or shared documentation.

**Interface:** The five CLI commands and receipt directory defined in the spec. No dependency on Task 1.

- [x] Write subprocess/real-file tests for propose→inspect→check→apply, asserting exact candidate bytes and recorded applied SHA independently via hashlib; add stale-target and missing-approval tests before implementation.
- [x] Run `python -m unittest tests.test_vault_proposals -v` and record the expected red result.
- [x] Implement bounded receipts, full candidate review, ownership/Markdown validation, current-hash checks, explicit approval, rejection and atomic publication. Preserve CRLF and permissions. Use a checkpoint to recover incomplete receipt publication without treating arbitrary matching content as applied.
- [x] Add failure-path tests for receipt tampering, engine markers/managed blocks, candidate validation, deny/traversal/link paths, lock contention and interrupted application. Run `python -m unittest tests.test_vault_proposals -v`.
- [x] Write `.superpowers/sdd/2026-09-07-openclaw-phase1/proposals-report.md` with files, red/green commands/results, assumptions, unresolved and next safe action. Do not commit.

### Task 3: Integration, review and handoff

**Files:** `commands/vault-doctor.md`, `commands/vault-session-end.md`, `skills/agentic-vault/SKILL.md`, `skills/agentic-vault/references/codex.md`, `README.md`, `docs/codex.md`, `docs/reliability.md`, `docs/lesson-proposals.md`, necessary command catalog metadata and tests only where actual interfaces require changes.

- [x] Add user-facing routes and examples after the CLIs stabilize. Preserve existing approval/probation/rollback rules and engine ownership. A tool's `--approve` flag records an explicitly authorized action; it never creates authorization.
- [x] Run focused task reviews and fix supported findings; then run `python -m unittest discover -s tests -v`, `python scripts/evaluate_recall.py`, `python -m compileall -q hooks skills/agentic-vault/scripts scripts`, `git diff --check`.
- [x] Have a separate read-only reviewer rerun important verification and inspect the entire diff, recording `verified_by` with actor/date/result.
- [x] Record results and remaining limits in `docs/verification/openclaw-phase1.md`, including task_id, artifact_paths, verification_commands_and_results, assumptions, unresolved, next_safe_action.
- [x] Commit only intended repository changes with an `ops:` or `build:` prefix; leave the feature branch clean and report its path and commit. No push, release or live vault/plugin migration.

Completion note: both features passed independent review. Final full suite ran 346 tests (332 passed, 13 skipped, one preexisting Windows Jarvis concurrency error independently reproduced on v0.9.0). See docs/verification/openclaw-phase1.md. Local implementation commit only; no release-readiness claim.
