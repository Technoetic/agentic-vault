# OpenClaw-inspired phase 1 verification

Date: 2026-09-07. Base: `61474337321d6c2e620d1ec3a2f594daf1f62dac` (v0.9.0).

This is the historical phase 1 checkpoint at `b45a74f`. The subsequent Windows
Jarvis correction and release verification are recorded in [v0.10.0 verification](v0.10.0.md).
Results and pending actions below describe that earlier checkpoint.

## Scope and artifacts

task_id: `openclaw-phase1`

artifact_paths:

- `skills/agentic-vault/scripts/vault_doctor.py` — read-only, content-free diagnosis using the existing hook's configuration, bounded reads and renderer.
- `skills/agentic-vault/scripts/vault_proposals.py` — revision-bound single-file proposal receipts, review/check/reject/apply, explicit approval and interrupted-application reconciliation.
- `tests/test_vault_doctor.py`, `tests/test_vault_proposals.py` — temporary-vault CLI and failure-path tests.
- `commands/vault-doctor.md`, session command updates, `skills/agentic-vault/SKILL.md` and its Codex reference — agent routing and authorization boundaries.
- `docs/lesson-proposals.md`, `docs/codex.md`, `docs/reliability.md`, `README.md` — usage and limits.
- `docs/superpowers/specs/2026-09-07-openclaw-phase1.md`, `docs/superpowers/plans/2026-09-07-openclaw-phase1.md` — approved scope and implementation plan.

These are development-branch additions. Version metadata remains 0.9.0; the published v0.9.0 artifact does not contain them. Source lineage and upgrade preflight remain outside this phase.

## Verification

verification_commands_and_results:

| Check | Result |
|---|---|
| Baseline `python -m unittest discover -s tests -v` | 304 tests; OK, 10 platform skips; Windows Python 3.12 |
| Doctor test-first check | First CLI test failed because the script was absent; expanded preimplementation run failed as expected |
| Proposals test-first check | Four initial tests failed because the script was absent; later regressions exposed and fixed applied-state hash checking, reserved engine paths and deeply nested JSON |
| `python -m unittest tests.test_vault_doctor tests.test_session_start -v` | After parser correction: 43 tests; 41 passed, 2 Windows symlink skips; existing hook unchanged |
| Independent `python -m unittest tests.test_vault_proposals -v` | 27 tests; 25 passed, 2 platform skips |
| `python -m unittest tests.test_release_metadata -v` | 7 passed |
| `claude plugin validate --strict commands` | Passed |
| `claude plugin validate --strict skills` | Passed |
| `claude plugin validate --strict .claude-plugin/plugin.json` | Passed |
| Skill creator `quick_validate.py skills/agentic-vault` | Initial default-encoding invocation failed on Windows cp949; repeated with `python -X utf8` passed |
| `python scripts/evaluate_recall.py` | 16 fixture queries (8 Korean, 8 English), recall@3 = 1.0, MRR = 1.0; no failures/errors. Lexical fixture evaluation only |
| `python -m compileall -q hooks skills/agentic-vault/scripts scripts` | Exit 0 |
| `python -X utf8 scripts/verify_codex_plugin.py` | Exit 0; isolated disposable home, installed resources discovered, version 0.9.0, 2 session hooks, hook trust untrusted, 0 model turns |
| Routing probes before/after documentation changes | Added explicit doctor routing, current-revision proposal flow, no authority from stored approval text, and no claim that file diagnosis proves host hook execution; documented ID discovery after the probe |
| Independent documentation/CLI integration review | Local links resolved, 12 command entries, examples matched actual CLI help |
| Final `python -m unittest discover -s tests -v` | 346 tests in 88.956s: 332 passed, 13 skipped, 1 error in the existing Jarvis same-content concurrency test (`BrokenBarrierError`); exit 1 |
| `git diff --cached --check` | Exit 0; all 16 intended files included |

The first integrated full-suite run executed 345 tests and reported one error and one failure in existing Jarvis concurrent-capture tests, with 13 platform skips. Independent repeated execution reproduced this on the unchanged v0.9.0 baseline as well as the feature branch. During concurrent creation, Windows/Python 3.12 path resolution sometimes returned an extended-length `\\?\C:\...` inbox path and a regular `C:\...` vault root; containment comparison then rejected the same physical location. The affected Jarvis and shared resolver code is unchanged in this phase. No fixture workaround was added; a separate resolver correction should retain its containment tests.

The final run after the doctor fix still reported one existing Jarvis error: the companion thread timed out at its test barrier, a failure mode also observed during baseline diagnosis. The full suite is therefore **not green**. The 42 new-feature tests had 39 passes and 3 platform skips, with no failures; both independent feature reviews passed. This branch is retained as a locally committed feature implementation with the baseline limitation documented, rather than being declared release-ready.

Independent doctor review found that a bounded JSON integer exceeding Python's digit limit escaped safe diagnostics as a parser `ValueError`. A new subprocess test first reproduced the failure; catching `ValueError` now returns fixed `invalid_config` / `config_json_invalid` JSON, exit 2 and no stderr. The test explicitly sets `PYTHONINTMAXSTRDIGITS=4300` and skips interpreters without that capability. Independent re-review resolved the finding and returned specification/quality PASS after rerunning all 43 doctor/session tests (41 passed, 2 skips).

For the existing Jarvis issue, an isolated run of both tests passed in both worktrees. Repeating these test names 100 times using `unittest.TestSuite` reproduced the problem in each worktree; the feature run finished 200 tests with 2 failures and 5 errors. Baseline repetition was stopped after reproduction, so no completed baseline total is claimed:

```text
tests.test_jarvis_bridge.JarvisStateTests.test_concurrent_same_content_captures_converge_without_temp_residue
tests.test_jarvis_bridge.JarvisStateTests.test_concurrent_conflicting_captures_keep_one_complete_winner
```

The investigator confirmed equal SHA-256 hashes for the baseline/branch Jarvis source and tests and captured the actual prefixed/unprefixed paths on baseline. A separate injected WinError 3-to-2 transition reproduced the relevant Python standard-library behavior; that injected transition is not claimed as a traced OS event from the actual failure.

## Assumptions and limits

assumptions:

- Actual authorization is obtained by the calling workflow. `--approve` records that decision; stored text and unkeyed receipt digests do not authenticate a human or grant permission.
- Doctor reports expected file/configuration behavior. Host hook trust, actual execution and model use of memory remain unverified.
- Proposal application replaces one existing UTF-8 Markdown file. It is unsuitable for local contracts requiring only partial edits to shared handoff/tasks files.
- Cooperating proposal writers use a vault-local lock. Atomic replacement and immediate revision rechecking are not a whole-vault transaction or an OS guarantee against unrelated concurrent writers.
- Receipts include original/candidate content and require the same confidentiality as their target notes. Receipt contents and validation-command text are never executed.
- Proposal validation reuses the existing checker's private pure `_staged_schema_errors` helper; this coupling should be checked when that helper changes.

unresolved:

- Real symlink creation and POSIX mode preservation tests need a supporting host; this Windows session skipped them. Existing Windows junction checks passed.
- Doctor's unreadable-file branch is implemented, but this host could not create a portable subprocess fixture that can stat a regular file yet cannot read it. No positive runtime coverage of that branch is claimed.
- Mode bits are preserved; platform ACL/ownership preservation beyond mode bits has not been established.
- The isolated Codex resource check did not trust hooks, run model turns, update a live plugin, or validate live-vault behavior.
- Preexisting Jarvis concurrent-capture tests can fail intermittently on this Windows/Python 3.12 host because of inconsistent extended-path prefixes; baseline reproduction establishes that this phase did not introduce the defect.

next_safe_action:

- Review the local `feat/openclaw-phase1` commit and address the separately reproduced Windows Jarvis path-representation race before a release gate. Publishing, a version bump and live plugin installation require a separate release task.

verified_by:

- Codex independent read-only subagent `/root/review_proposals`, 2026-09-07 09:42 KST: spec and quality PASS; 27 proposal tests independently executed (25 passed, 2 skips); Windows case-alias/frontmatter probe rejected the invalid candidate.
- Codex independent read-only subagent `/root/review_proposals`, 2026-09-07 09:44 KST: integration documentation/routing PASS; CLI help, 42 local links and the 12-command catalog independently checked.
- Codex independent read-only subagent `/root/diagnose_regression`, 2026-09-07 09:46 KST: baseline reproduction and separate deterministic Windows standard-library boundary probe confirmed the existing Jarvis path-representation issue; no product/test changes.
- Codex independent read-only subagent `/root/review_doctor`, 2026-09-07 09:47 KST: final specification/quality PASS after the parser fix; independently reran 43 doctor/session tests (41 passed, 2 privilege skips); existing hook unchanged.
- Codex independent read-only subagent `/root/review_proposals`, 2026-09-07 09:49 KST: final verification accounting PASS against actual logs and review reports; 346 = 332 passed + 13 skipped + 1 error; staged 16-file inventory and whitespace checked. This accounting review did not rerun tests.
