# Reliability and Verifiable Recall Implementation Plan

> **For agentic workers:** Use test-driven development and independent task review.

**Goal:** Close verified data-boundary failures and ship demonstrable recovery and recall.

**Architecture:** Preserve the plugin and standalone healthcheck. A small shared
path helper protects note readers. Independent snapshot and lexical recall tools
provide deterministic behavior, with integration owned by the controller.

**Tech Stack:** Python 3.10+, standard library, unittest, Git, Claude Code hooks.

**Spec:** `docs/superpowers/specs/2026-09-05-reliability-design.md`

## Global Constraints

- Python 3.10+; standard-library-only runtime.
- Preserve existing configuration and CLI flags unless unsafe; document tightened validation.
- Tests use temporary fixtures and no external services or real user vaults.
- No publication, installation, network messaging, or modification of user settings.
- Task owners edit only assigned files; the controller integrates shared documentation.

## Task 1: Safe and bounded session injection

Files: `hooks/session_start.py`, `hooks/hooks.json`, new hook launcher if needed,
`skills/agentic-vault/scripts/vault_paths.py`, `tests/test_session_start.py`.

Produces shared `resolve_note_path(vault, rel_path, deny_zones=()) -> Path`.
Consumes existing `validate_config` and `estimate_tokens` from healthcheck.

- [x] Write behavioral tests: outside marker never appears, deny paths and malformed
  config yield no injection, normal Korean/English notes appear, zero budget omits
  section, long notes stay within the estimate, oversized reads are bounded,
  symlinks/junctions cannot escape, startup/resume preserve context.

```python
result = run_hook(vault, {"handoff_note": "../outside.md", "hot_note": ""})
self.assertNotIn("OUTSIDE_MARKER", result.stdout)
self.assertEqual(result.stdout, "")
```

- [x] Run `python -m unittest tests.test_session_start -v` and observe regressions.
- [x] Implement strict path helper, validated config, bounded reads and estimated
  section trimming. Use interpreter detection in hook wiring, not `python ... ||
  python3 ...` after a checker failure.
- [x] Re-run focused tests and record evidence; do not edit other task surfaces.

## Task 2: Recoverable snapshots

Files: `skills/agentic-vault/scripts/backup_vault.py`, `tests/test_backup_vault.py`.

Preserves `--vault` and `--target`; adds documented verify/restore CLI operations.
Snapshot layout, manifest and helpers remain internal to this module.

- [x] Write real-filesystem tests for deleted untracked binary recovery, mutation
  and digest mismatch, invalid/overlapping paths, concurrent runs, interrupted
  copy, malformed manifests, existing restore destination and Git bundle failure.

```python
snapshot = create_backup(vault, target)
(vault / "90-assets" / "sample.bin").unlink()
restored = root / "restored"
restore_snapshot(snapshot, restored)
self.assertEqual((restored / "90-assets/sample.bin").read_bytes(), b"original")
```

- [x] Run focused tests and confirm the original mirror cannot meet recovery.
- [x] Implement staging plus atomic snapshot publication, SHA-256 manifests,
  exclusive writer lock, normalized nonoverlap checks, verification and export
  restore. Never automatically prune snapshots or overwrite source data.
- [x] Run `python -m unittest tests.test_backup_vault -v` and record evidence.

## Task 3: Source-attributed deterministic recall

Files: new `skills/agentic-vault/scripts/vault_recall.py`, `tests/test_vault_recall.py`,
`tests/fixtures/recall/`, `scripts/evaluate_recall.py`.

Consumes the shared path helper from Task 1; imports existing config validation
and token estimation. CLI: `--vault`, `--query`, `--limit` (default 5),
`--max-tokens` (default 1500), `--format` (`text` or `json`, default `text`).
JSON contains `context`, source matches and diagnostic counters; the budget
applies to `context`, not JSON metadata. Text mode prints only that context.

- [x] Write literal source-ranking tests for Korean/English queries, zero matches,
  deterministic ties, source attribution, denied/symlink paths, byte/scan limits,
  malformed config, exact context budget behavior and source changes.

```python
result = recall(vault, "deployment rollback", limit=3, max_tokens=300)
self.assertEqual(result["matches"][0]["path"], "20-knowledge/rollback.md")
self.assertIn("20-knowledge/rollback.md", result["context"])
```

- [x] Run tests and observe absent recall behavior, then implement a bounded
  Unicode-aware lexical ranker with title/body evidence and deterministic ordering.
- [x] Add independent bilingual fixture queries and evaluation with recall@3/MRR;
  no paid APIs and no claims about semantic or Claude answer quality.
- [x] Run focused tests and `python scripts/evaluate_recall.py`.

## Task 4: Integrate, document, and verify

Files: commands, config template, plugin metadata, README, release note,
`.github/workflows/tests.yml`, integration tests where behavior requires them.

- [x] Document actual budget semantics, fail-closed paths, snapshots and restore,
  lexical recall limitations, quality criteria and live-validation boundary.
- [x] Wire recall command and session workflow without introducing implicit
  network calls or auto-promoting model-generated lessons.
- [x] Add CI for Ubuntu/Windows/macOS and Python 3.10/3.12/3.14 where supported.
- [x] Inspect full branch diff; obtain independent security/correctness review.
- [x] Run `python -m unittest discover -s tests -v`, recall evaluation,
  `python -m compileall -q hooks skills/agentic-vault/scripts scripts` and
  `git diff --check`; fix material findings and re-run affected checks.
- [x] Commit the reviewed local result and report exact outcomes and limits.

## Related Task 5: Full healthcheck boundary

- [x] Reproduce and close full-mode config, note, rule and report junction escapes.
- [x] Apply consistent case-insensitive deny policy to configured, scanned and
  special readers, while preserving standalone deployment and explicit --output.
- [x] Close Windows reserved-device aliases in all three portable path validators.
- [x] Verify regression fixes through focused tests, the complete suite and
  independent whole-branch review.

## Completed local result

Version 0.9.0-local.1; healthcheck engine 0.9.0. Final independent review:
spec PASS and code quality APPROVE. All required findings addressed.
Full suite: 288 collected, 280 passed, 8 platform/privilege skips, zero failures.
Recall: 16 bilingual queries, recall@3/MRR@3 1.0; 4-7 candidate sources per query.
Strict Claude static validation, compilation and Git whitespace checks passed.
Remote CI and live model integrations remain outside this local validation.
See [validation record](../../validation.md) for evidence and limits.
