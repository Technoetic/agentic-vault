# Memory boundaries implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved three-part evaluation/diagnostic increment.

**Architecture:** Two standalone repository evaluation tools use independently
authored synthetic packs and digest-bound replay. One existing engine CLI gains
two opt-in diagnostics; default output and support decisions are preserved.

**Tech Stack:** Python 3.10+ standard library, unittest, JSON, local Git worktrees.

**Spec:** `docs/superpowers/specs/2026-10-28-memory-boundaries.md`

## Global Constraints

- Base v0.32.0 at `446249062ee45193064e255ea71a0eb7e9e0a1f8`.
- Python 3.10+ standard library; no mandatory model/network/dependencies.
- No real user vault input for the two new evaluation CLIs.
- New diagnostics are opt-in; legacy output/decisions unchanged without flags.
- No automatic edits, plugin/cache upgrade, release/tag or remote push.
- Agents own disjoint files and separate worktrees; integration is sequential.

## Review Focus

- Response replay after changing snapshot, question, fixture or config must fail.
- Unknown/incomplete evidence must not become a certified absence judgement.
- A latest direct value with an old derived total must remain a revision failure.
- Subject/value matches across frontmatter, lines or removed tables must not bind.
- Mixed source failures, CRLF/BOM and certificate hash changes must be explicit.

## Task A: state/dependency revision evaluation

**Files:** `scripts/evaluate_state_revisions.py`,
`tests/test_state_revision_evaluation.py`,
`tests/fixtures/state_revisions/pack.json`, `docs/state-revision-evaluation.md`.

**Interfaces:** Standalone module exposes `load_pack`, `evaluate`, `main`.
CLI: `--control`, `--observations PATH`, `--write-observations PATH`.
No existing runtime module changes. Report carries per-snapshot results and
current/superseded/other plus dependency-stale diagnostics.

- [ ] Write failing tests for a quantity update, an unchanged unit price and an
      old derived total after the update; malformed/changed replay must not pass.
- [ ] Run the focused test file and record the expected missing-feature failure.
- [ ] Implement the bounded fixture loader, label-blind controls, grader and CLI.
- [ ] Add tests for duplicate/nonfinite JSON, boolean-as-number, unsafe file,
      missing/extra observations, copied payload isolation and CLI round trip.
- [ ] Run focused tests and synthetic controls; document exact limitations.
- [ ] Commit only Task A files; independent reviewer checks spec and quality.

## Task B: insufficiency versus unsupported premise denial

**Files:** `scripts/evaluate_evidence_insufficiency.py`,
`tests/test_evidence_insufficiency_evaluation.py`,
`tests/fixtures/evidence_insufficiency/pack.json`,
`docs/evidence-insufficiency-evaluation.md`.

**Interfaces:** Standalone module exposes `load_pack`, `evaluate`, `main`.
CLI: `--control`, `--observations PATH`, `--write-observations PATH`.
Bounded declared response kinds/claims; no free-prose semantic inference.

- [ ] Write failing tests: missing evidence acknowledged versus falsely denied;
      partial/incomplete evidence cannot certify absence; explicit negative
      evidence is distinct from missing evidence.
- [ ] Record the failing focused test before writing implementation.
- [ ] Implement fixture-relative coverage diagnostics, grader, controls and CLI.
- [ ] Test schema limits, replay mismatches/errors, evidence incompleteness,
      payload isolation, empty populations and real CLI replay.
- [ ] Run focused tests and controls; document diagnostic-only semantics.
- [ ] Commit only Task B files; independent reviewer checks spec and quality.

## Task C: complete support references and subject diagnostics

**Files:** `skills/agentic-vault/scripts/vault_ssot.py`,
`tests/test_support_row_diagnostics.py`, `docs/support-row-diagnostics.md`.

**Interfaces:** `check_support(..., row_references=False, bind_subject=False)`;
CLI flags `--row-references`, `--bind-subject`. Optional `row_references`,
`audit_population`, and separately named binding observations/warnings fields.
`audit_population` uses the existing generic id/path/line schema; no audit reader
change is required. Existing `warnings`, literal support counts and periods stay
the same with either option; default top-level keys are identical when both off.

- [ ] Write failing tests for supported-row export and same-value wrong-subject
      sources; assert exact baseline dictionary equality with both flags off.
- [ ] Record focused failures before changing the engine.
- [ ] Implement bounded all-row references and generic audit population export.
- [ ] Implement same-original-line binding using the existing policy/stable read
      and cached raw-source data, only when requested.
- [ ] Test all row states, numeric boundaries, explicit aliases, empty subject,
      CRLF/BOM, markup, self-citation, primary-text-only, policy/budget failures,
      concurrent/stale source refusal and all source limits.
- [ ] Run new tests plus existing SSOT, audit, warning-policy/golden tests.
- [ ] Commit Task C files; independent reviewer checks spec and quality.

## Task D: integrate, document and verify

**Files:** README, `docs/memory-evaluation.md`, `docs/hardening.md`, this plan,
and an implementation/verification note under `docs/` as needed.

- [ ] Baseline: run unchanged v0.32.0 full unittest suite independently.
- [ ] Sequentially integrate reviewed A/B/C commits; resolve only owned conflicts.
- [ ] Add discoverable usage links and certificate migration caveat; leave version
      badges, tags and installed engines untouched.
- [ ] Run the full integrated suite, CLI synthetic controls and replay examples,
      audit draw round trip, `git diff --check`, and default-output regressions.
- [ ] Independent final reviewer reruns targeted checks and examines integrations.
- [ ] Resolve material review findings and rerun affected checks.
- [ ] Record actual counts/results/limitations, commit owned changes and integrate
      locally when clean/fast-forward. Do not publish or push remotely.
