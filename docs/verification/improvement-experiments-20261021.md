# Improvement experiments verification — 2026-10-21

The user requested implementation of the preceding Harness² adoption proposal. The scoped delivery connects baseline/general/specialized/composed candidate lineage, current native run comparisons and evidence-aware reflection. It is a data workflow; no model runner, arbitrary command execution or automatic lesson application was added.

| Check | Actual result |
|---|---|
| Native runs baseline | 29 tests, OK, 1 environment skip |
| New TDD suite | Initial20 missing-feature assertion failures; final26 passed |
| Independent frozen new suite | 26 passed, 40.520 seconds |
| Full suite | 1846 tests, OK, 33 environment skips, exit0, 770.11 seconds |
| Required offline regressions | Seven existing evaluation commands, all exit0 |
| Deterministic example | Five scenarios,20 native runs (20 prepared +20 completed receipts),15 native comparisons |
| Package verifier tests | 12 tests, OK,2 platform skips |
| Compilation / whitespace | Passed |

Full command: `python -X utf8 -m unittest discover -s tests -v`. New suite: `python -m unittest tests.test_vault_improvements -v`. Example: `python scripts/evaluate_improvement_experiments.py`. Structured commands, counts and frozen source hashes are in [the verification JSON](improvement-experiments-20261021.json).

One intermediate test incorrectly compared a binary floating delta with literal0.2; the assertion was corrected to approximate equality. This was a test correction, not evidence that all initial checks passed. Original RED/failure logs remain in the local plan workspace.

The independent reviewer reproduced pending abstention, unique positive recommendation, keep-base regressions, positive ties, hard/error vetoes, NO_ENTRY and two-pair lesson references. Supplemental probes rejected empty lesson indices, status-error evidence, report mutation at publication and a valid63845-byte input whose65544-byte encoded receipt exceeded the native65536-byte cap. No blocking code findings remained.

Reflection eligibility is recalculated from compatible current native completed receipts; a pre-existing comparison receipt is not mandatory. Successful outputs carry advisory/review/no-apply flags. CLI error JSON remains content-free. Inputs, evidence, configuration and receipt links are rechecked before use/publication. These bindings do not independently prove host observations or causal effects.

The implementation was checked on this Windows/Python3.12 host. No actual model experiment, general business improvement, total-dollar saving or paper benchmark reproduction was measured. Release version0.26.0 and active installations were not changed. Usage and exact schema are in [improvement-experiments.md](../improvement-experiments.md).

- task_id: `av-harness2-implementation-20261021`
- artifact_paths: new coordinator/tests/example, usage and skill/Codex routing, this verification note/JSON, design and plan.
- verification_commands_and_results: table and linked JSON; raw logs kept in `.superpowers/sdd/harness2-improvement-20261021/` of the retained feature worktree.
- verified_by: Codex independent read-only `/root/improvements_contract_review`,2026-10-21,26 tests + example + supplemental probes. Full suite and legacy offline regressions executed by root.
- assumptions: only the approved first implementation scope; existing defaults and native safety/observation semantics preserved.
- unresolved: actual model/business effectiveness, total costs and new cross-platform execution.
- next_safe_action: use the deterministic example or prepare host-owned experiment inputs. Publication/installation requires the separate release workflow.
