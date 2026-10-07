# Memory-pattern absorption completion verification (2026-10-07)

This is the historical experiment snapshot before publication. The subsequent public package and installation/CI receipts are described in [v0.19.0 verification](v0.19.0.md); the measurements and version state below retain their original scope.

- task_id: `agentic-vault-memory-absorption-complete-20261007`
- artifact_paths: [usage/coverage](../memory-patterns.md), [machine results](memory-absorption-results-20261007.json), local branch `feat/memory-improvements-20261007` based on `904231b8ed2eec1f23a432ba1e14289100dfc52b`; new retrieval, memory-unit and lesson-observation modules, qmd adapter, evaluator/workflow runner, command/installer/CI integration and fixtures.
- verification_commands_and_results: final parent **861 tests: 836 passed, 25 skipped, zero failures**, 193.564s; independent **861: 836 passed, 25 skipped, zero failures**, 193.4s. Full exact commands, outcomes and code/fixture hashes are in the machine results.
- assumptions: selective implementation of all nine proposed source patterns, Python 3.10+ stdlib core, original Markdown/Git preserved. Valid default recall unchanged; advanced options explicit. Source/rank/hash/time/proposal/evidence content is data, never authority. Host remains responsible for meaning and existing authorization.
- unresolved: external qmd embeddings/reranker and complete Graphiti/PPR unmeasured; tiny synthetic experiments cannot establish real-vault answer accuracy, attack success, or general performance. Platform skips remain. Observation checksums lack authentication/trailing-suffix deletion detection; file checks are point-in-time bindings. Public/install version remains v0.18.0.
- next_safe_action: use local optional functions on explicit evidence and measure real task misses, unknown outcomes and costs with the same model and budget.
- verified_by: read-only reviewers `/root/complete_capsules` (lesson outcomes/evaluation), `/root/complete_lesson_metrics` (capsules/retrieval/qmd), plus `/root/complete_retrieval` independent whole-tree rerun. Exact actor/time/result objects are preserved in machine results.

## Retrieval comparison

Same 16 bilingual questions: 14 answerable, two no-answer, top 3 and 1500 estimated context tokens. Advanced fixture options, never expected paths, enter the retriever.

| Observation | Legacy | Advanced |
|---|---:|---:|
| Source recall@3 | 0.7857 | 1.0000 |
| MRR@3 | 0.6786 | 0.9167 |
| No-answer source returns | 2/2 | 0/2 |
| Stale source exposure | 4/4 | 0/4 |
| Ambiguous-link forbidden sources | 0/2 | 0/2 |
| Estimated returned memory tokens, all questions | 699 | 645 |
| Retrieval time, all questions (ms) | 125.215 | 363.548 |

The original16 regression remains recall/MRR=1.0/1.0. Exposure measures returned candidates, not successful malicious instruction following. Advanced reads cost more time; measurements include local read/validation work, not model billing.

## Actual multi-session operations

Each condition has four synthetic scenarios, discovery then two later attempts. Precondition/postcondition failures grade state transitions, not question answering. Actual source quarantine is performed between rollback trials. No model has shell/filesystem/service tools; allowed operations mutate only simulator state. Discovery is excluded: eight later attempts per condition.

| Planner/condition | Completed | Unsafe proposed actions | Estimated memory tokens |
|---|---:|---:|---:|
| Fixed policy control: none | 0/8 | 0 | 0 |
| Fixed policy control: legacy | 0/8 | 1 | 352 |
| Fixed policy control: improved | 8/8 | 0 | 1868 |
| Local Qwen model: none | 6/8 | 0 | 0 |
| Local Qwen model: legacy | 6/8 | 0 | 352 |
| Local Qwen model: improved | 8/8 | 0 | 1868 |

Actual loopback inference: `Qwen3.5-9B-Q8_0.gguf`, temperature0, thinking=false, max output256, identical1500 memory cap. Proxy/redirect disabled; no private-vault data or credentials enter prompts. Actual selected memory totals differ, so improvement is not a cost-free claim. Model planners/actions, session failures, source paths, quarantine events and elapsed times are in machine results. One tiny deterministic-temperature sample set cannot establish statistical/general effects; basic safety of the bounded simulator is separate from real attacker resistance.

The tracked model ID is the basename of the actual local API model identifier for portable display. `MODEL_ID` in the displayed command is a template placeholder, not the literal invocation. The full original identifier/configuration and actual run output are retained in `.superpowers/memory-complete/workflow-local-model.json`; no credential value is recorded.

## Independent findings resolved

Reviews reproduced and regression-tested late source/policy mutation, self-excluded config, duplicate/invalid policy keys, malformed timezone offsets, incomplete graph/supersession uniqueness, denied/stale evidence eligibility, bounded malformed-source inventories, ambient HTTP proxy forwarding, malformed model responses and false backend labels. Real `core.autocrlf=true` checkout reproduced quote mismatch and a fixture LF attribute fixes it. Read-only capsule/retrieval tools execute no network/subprocess/note writes. Local observation records preserve applied-receipt state and known/unknown denominators; no automatic promotion or target edit.

Initial parent/independent854-test runs passed; later adversarial findings justified the final reruns reported above. Native disposable Codex install verifies new resources with isolated home, zero model turns and untrusted hooks; current installation/settings are preserved. Raw logs/review packets remain in task-owned ignored `.superpowers/memory-complete/`; synthetic results needed for review are copied to the tracked machine artifact.
