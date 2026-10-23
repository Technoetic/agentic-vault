# Evaluator and improvement-cost evidence verification

Local unreleased implementation, 2026-10-23. Base: `c72580603fdf0ca4309ba4c9849b848f7144adcc` (v0.29.0).
Scope: optional evaluator snapshots and explicit, source-bound cost reports. See [usage and limits](../improvement-accounting.md).

| Check | Result |
|---|---|
| Full suite, Windows / Python 3.12 | `python -X utf8 -m unittest discover -s tests -v`: 2025 tests, 1991 passed, 34 environment skips, zero failures/errors, 871.341 seconds |
| New feature tests | Evaluator14 + costs21 + four-arm integration1 =36, all passed; independently rerun |
| Existing affected tests | Native29 (one symlink-creation skip), improvements26, package13 (two POSIX-only skips), all non-skipped passed |
| Offline functional checks | Existing10 + accounting1 =11 commands exit0; compileall and diff-check also exit0 |
| Disposable Codex installation/discovery | Selected resources match, isolated HOME, zero model turns; active user installation untouched |
| Independent review | Code, contracts, docs, routing and packaging: no blocking findings; one minor documentation limit clarified |

TDD evidence: initial missing-feature tests failed; canonical boolean/integer and integer/float receipt forgeries were reproduced before hardening; six missing/modified package-resource cases failed before package checks were extended. The first integration run used an incorrect nested-view key; it was corrected to the API's flattened output before the successful run.

Legacy unbound manifest/ID/output compatibility was directly compared with the base implementation in independent review. The shared evaluator contract works through the existing four-role improvements flow. Unknown costs stay null, discarded attempts count, incomplete scope/bases withhold resource deltas, and performance hard failures retain their native veto independently of cost availability.

- task_id: `av-rsi-evaluation-20261023`
- artifact_paths: feature source/tests, usage/spec/plan, this verification note.
- verification_commands_and_results: commands and results above; raw logs/receipts retained in the local task evidence archive.
- verified_by: Codex read-only task reviewer and separate whole-branch reviewer, 2026-10-23; 36 new, native29/skip1 and package13/skip2 independently rerun.
- assumptions: Python 3.10+ standard-library contract; this full run used Windows and Python3.12. No new remote CI matrix has run for this unreleased implementation.
- unresolved: real-model/task performance and cost-effectiveness unmeasured; hashes do not authenticate execution, metering, complete reporting or reviewer identity. Symlink creation unavailable; junction/hardlink tests passed.
- next_safe_action: local use and separately authorized release validation. No automatic promotion, model/network execution, publication or active installation was performed.
