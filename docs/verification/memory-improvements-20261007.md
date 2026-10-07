# Memory evaluation and read-only link proposals — 2026-10-07

- task_id: `agentic-vault-memory-improvements-20261007`
- artifact_paths: `scripts/evaluate_recall.py`, `skills/agentic-vault/scripts/vault_links.py`, `skills/agentic-vault/scripts/vault_recall.py`, `scripts/verify_codex_plugin.py`; evaluator/link/core/installer tests, `tests/fixtures/recall_experience/`, [usage](../link-proposals.md), README/ingest/Codex documentation. Local branch: `feat/memory-improvements-20261007`, based on `e85a4a7ff7a6183ab14997a23e09bbfe25d8ac8b`.

## verification_commands_and_results

| Command or check | Observed result |
|---|---|
| Baseline `python -X utf8 -m unittest discover -s tests -v` | 663 tests, 22 environment/platform skips, no failures; exit 0. |
| Final frozen-source `python -X utf8 -m unittest discover -s tests -v` | Parent: 708 tests, 23 skips, no failures; exit 0, 178.533 seconds. Independent reviewer: 708 tests, 23 skips, no failures; exit 0. |
| `python -X utf8 -m unittest discover -s tests -p test_recall_evaluation.py -v` | 10 tests, exit 0. RED runs demonstrated unsupported no-answer/exposure/cost fields and invalid-label acceptance before implementation. |
| `python -X utf8 -m unittest discover -s tests -p test_vault_links.py -v` | 33 tests, one Windows symlink-privilege skip; exit 0. A distinct hardlink rejection test passed. |
| `python -X utf8 -m unittest discover -s tests -p test_vault_recall.py -v` | 41 tests, five existing environment skips; exit 0. Added Git-metadata regression first failed, then passed after reserving `.git` even with empty configured exclusions. |
| `python -X utf8 -m unittest discover -s tests -p test_codex_verification.py -v` | 10 tests, two POSIX-only skips; exit 0. Missing/modified helper and documentation tests first failed before installer-resource verification was extended. |
| `python -X utf8 scripts/evaluate_recall.py` | Original 16-query bilingual fixture: recall@3 1.0, MRR@3 1.0; exit 0. Original corpus and thresholds preserved. |
| `python -X utf8 scripts/evaluate_recall.py --fixture tests/fixtures/recall_experience` | 16 queries: 12 answerable, four no-answer; recall@3 1.0, MRR@3 0.8333. No-answer source returns 4/4; labeled forbidden and stale first-ranked exposure each 2/2. Existing retrieval gates exit 0; exposure observations remain visible. |
| Experience fixture plus `--max-no-answer-false-positive-rate 0 --max-forbidden-exposure-rate 0 --max-stale-top-1-rate 0` | Exit 1 as expected: all three observed rates are 1.0. This is a deliberately failing observational gate, not a failed implementation test. |
| `python -X utf8 scripts/verify_codex_plugin.py` | Current working resources installed and byte-compared in disposable `CODEX_HOME`; exit 0, `installed_resources=verified`, model turns 0, hooks remain untrusted. Repeated after the documentation-only reference addition. |
| `python -X utf8 -m compileall -q hooks skills/agentic-vault/scripts scripts` and `git diff --check` | Exit 0. |
| `python -X utf8 skills/agentic-vault/scripts/vault_links.py --vault tests/fixtures/recall/vault --source 20-knowledge/session-handoff.md --query "handoff decisions worktree" --limit 3 --max-tokens 1500 --format json` | Returned three filename links, bound source/targets and a diff in a 568-estimated-token context; data-only authority and approval requirement retained. |
| Independent adversarial checks | Final-source policy mutation, reserved Git reads, bare-CR rejection, eight real Git patch/no-write cases, six token budgets, denominator/gate observations, and 15 nonfinite threshold inputs verified. |

Source-changing review findings were reproduced with failing tests before correction:
final source/target/policy mutation, unsafe diff headers, bare-CR line endings, and
Unicode body separators. The diff uses LF-delimited lines so U+2028, U+2029 and
vertical tabs inside valid UTF-8 content remain literal content. Real Git patch
checks use explicit line-ending options and preserve original source bytes in
temporary test repositories. The helper itself has no write/apply/network path.

## assumptions

- The feature uses explicit lexical queries and existing bounded recall. It does not implement embeddings, automatic memory evolution, or semantic-link certainty.
- Link candidates and diffs are evidence data. Hashes bind point-in-time files, do not confer write permission, and are rechecked before publication. Host application still requires current authorization and revalidation.
- Filename uniqueness is checked only among eligible notes. Denied/excluded paths are never read to prove global uniqueness.
- Token counts use the existing Hangul/non-Hangul character estimate; they are not billing counts. Timing covers recall calls, not end-to-end task latency.
- This is local development on the current v0.18.0 base; the active version manifests and user installation were not updated as a release.

## unresolved

- Synthetic source exposure remains observable. A returned stale/untrusted source is not evidence that an LLM followed its instructions or answered incorrectly.
- No private-vault accuracy gain, real memory-injection attack success, workflow improvement, or operating-cost reduction was measured.
- Windows symlink creation was unavailable in the corresponding fixture test. Hash bindings and path checks are point-in-time observations, not an OS sandbox or a filesystem freeze.
- Bare-CR source line endings and unsafe diff-header paths are explicitly rejected. Global filename collisions outside eligible notes require host review.

## next_safe_action

Use the documented helper from this branch, review the source/target bindings and
proposed diff, and compare future changes against both fixtures. Evaluate selected
real failure questions under the same host model and context budget before expanding
the implementation to temporal fact filtering or another retrieval backend.

## verified_by

- Codex `/root`: fresh final frozen-source suite, benchmark outputs, real CLI invocation, compile/diff hygiene and disposable native Codex resource check, 2026-10-07.
- Codex `/root/review_memory_changes`: PASS_WITH_NOTES with no open actionable findings, independent read-only review, nine adversarial check groups and fresh 708-test suite (685 passed, 23 skipped, exit 0), 2026-10-07 15:20 KST. All 24 current file fingerprints match; source/test/fixture fingerprints stayed unchanged during that run. The usage document received only a design-reference addition; it was reread and the ten Codex verification tests rerun successfully. External reference contents were verified in the preceding research session, not re-browsed by this reviewer.

Detailed local execution logs, reviewer file fingerprints and six-field handoffs
are retained under the task-owned ignored `.superpowers/memory-improvements/` workspace.
