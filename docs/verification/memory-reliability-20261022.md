# Memory reliability tools — 2026-10-22 verification

The user authorized implementation of the first three recommendations after the
v0.27.0 research review. This change adds paired formation controls, source-bound
current-value assembly and receipt-bound procedural recall. It is a local source
change; no release, active installation update or production vault upgrade was
performed. The published version and manifests remain v0.27.0.

## Implementation and reproducible checks

| Area | Command | Observation |
|---|---|---|
| Existing baseline | `python -X utf8 -m unittest discover -s tests -p test_vault_improvements.py -v` | 26 passed before implementation |
| Formation controls | `python -X utf8 -m unittest discover -s tests -p test_memory_formation.py -v` | Final 26 passed; source SHA256 `cc742326c9021cc1cc5a3fb16f81d4f2acda91778af473eeea1a51a0831e7c74` |
| Assembly | `python -X utf8 -m unittest discover -s tests -p test_vault_assembly.py -v` | 44 tests, 43 passed, one Windows symlink-permission skip |
| Procedures | `python -B -m unittest discover -s tests -p test_vault_procedures.py -v` | 36 passed |
| Integration example | `python scripts/evaluate_memory_reliability.py` | Seven checked cases passed, native receipts and actual synthetic operations; no model/network calls |
| Full discovery/run | `python -X utf8 -m unittest discover -s tests -v` | 1,950 tests in 878.783 s, 34 skips, one documentation-link failure described below |
| Existing offline gates | The eight commands retained in `.github/workflows/tests.yml`: basic/advanced recall, memory workflows, Korean evidence, tool controls, improvement contracts, hard top3/top5 | All exit 0 |
| Package | `python scripts/verify_codex_plugin.py --codex <native codex.exe>` | Actual disposable Codex home, resource byte comparison passed, model turns 0, hook trust remains untrusted |
| Compile | `python -m compileall -q hooks skills/agentic-vault/scripts scripts` | Exit 0 |

The first full test collection preceded five additional formation regressions.
All 26 final formation cases were rerun after the fix and independently rerun.
Final discovery contains 1,955 unique test IDs. The audit joins the full run,
the final formation run and the documentation rerun; it does not describe these
as a single clean full-suite invocation.

## Findings and corrections

1. Independent review found that P5 initially required identical experience pools
   but did not require the same selected subset. A changed selected subset could
   be mistaken for an order-only intervention. A failing regression reproduced
   this; the final code requires identical selected experiences and distinguishes
   P4 selection effects from integration-order changes. Final and transient
   contamination, including later-corrected writes, are separate observations.
2. Assembly initially used argparse's normal error output, which echoed unknown
   arguments. A failing secret-marker regression preceded the fixed JSON-only
   argument error path.
3. The full run's one failure was
   `test_readme_truth.PublicDocsTests.test_relative_links_resolve`, for this exact
   verification file: README linked it before the final record was written.
   This document completes that target. The documentation test module is rerun
   after creating it; the initial failure and raw log remain in the verification
   archive. No failed assertion or threshold was removed.
4. The unit run alone took nearly fifteen minutes, before the remaining CLI gates.
   The CI unit job's timeout is twenty minutes; this does not change any test's
   assertion or the normal session/retrieval budget.

## Independent review and boundaries

- Codex `/root/onboarding_assets`: read-only assembly 44 tests (one skip) and three
  adversarial probes, including hidden unknown-validity rows and forged quotes.
- Codex `/root/bizrouter_video_readiness`: read-only procedures 36 tests and two
  extra probes: laundering model conditions through both catalogue and caller,
  and changing instruction bytes immediately after native inspection.
- Codex `/root/memory_reliability_final_review`: read-only assembly/procedures
  tests, final formation 26 tests, the changed-P5-selected-set rejection and the
  seven-case integration example, 2026-10-22 11:14:22 KST. Required code findings
  were resolved. The final command/count audit is recorded separately.

Fifteen existing hook, manifest, recall and checker files matched the unchanged
clean v0.27.0 checkout byte for byte. Raw Git blobs use LF for three JSON files
whose Windows checkout uses CRLF; the byte audit therefore compares the actual
clean baseline checkout, not an incompatible blob representation. The seven-file
warning checker set was not edited.

These are synthetic functional observations, not a model benchmark or a security
proof. Bounded formation still loses the legitimate task in two poisoned cases
after rejecting evidence. It is an evaluation policy, not a global engine
defense. Assembly supports explicitly structured scope/polarity and literal
identities; it does not extract meaning or entailment from arbitrary prose.
Procedure source substrings and successful host observations do not authenticate
truth, human review or full procedure execution. Returned steps remain advisory.
New runtime execution on Linux/macOS and real business effects were not measured.

## Handoff

- task_id: `agentic-vault-memory-reliability-20261022`
- artifact_paths: the four new tool/example scripts, four new test modules,
  three user guides, routing/package/CI integration and this verification record.
- verification_commands_and_results: the commands above, with raw logs and
  receipts archived by the coordinating host; the final audit preserves the
  initial full-run failure and subsequent scoped repair.
- assumptions: Python 3.10+ standard library, opt-in tools, explicit host source
  authority/catalogue/conditions, unchanged default session and recall behavior.
- unresolved: real model/business effectiveness and cross-platform execution of
  the new code; no claim of general prompt-injection resistance.
- next_safe_action: review the recorded file hashes, integrate locally, then
  handle publication and active installation as separate operations.
