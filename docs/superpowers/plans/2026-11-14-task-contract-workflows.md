# Source-aware tasks and bounded review workflows implementation plan

> For agentic workers: use subagent-driven-development. Execute the already authorized scope without asking to repeat implementation/publication approval.

**Goal:** Add three opt-in, standard-library workflows: provenance-aware task contracts, staged evaluation, and bounded improvement observation.

**Architecture:** Preserve current evidence/runs/coverage APIs and default behavior. New modules validate explicit bounded inputs, bind source bytes, and return data/advisory results. They never run shell commands, invoke models, grant authority, publish, or promote lessons.

**Tech stack:** Python 3.10+ stdlib, unittest, existing vault path/evidence/runtime helpers.

**Spec:** The three mechanisms recommended from Q00/ouroboros commit 54dad8e1dd93aee31858bde72493cd7a0f938739. Implementation is original, informed by source, not a dependency on Ouroboros.

## Shared interfaces and boundaries
- New module1: vault_task_contracts.py; capture(vault, input_path), inspect(vault, contract_id), revise(vault, contract_id, input_path). inspect includes contract_id, status, acceptance_ids, accepted_fields, questions, source bindings and limitations.
- New module2: vault_staged_evaluation.py; evaluate(vault, report_path). Report includes contract_id and a selected native evidence receipt/coverage manifest. Result includes status, contract_id, per-AC result and input/source hashes, authority=data_only. Expose enough normalized results for the observer. Coordinate exact schema with task3.
- New module3: vault_improvement_cycles.py; audit(vault, input_path). Input names ordered staged evaluation report paths and explicit limits. It recomputes reports, uses fixed contract identity/criteria, and returns success/continue/blocked/stop reasons, never executing a loop.
- Use existing path/deny/secret/symlink/size policy. Never bypass excluded sources or read credentials. Error messages are content-free. Reject unknown fields, bool-as-int, malformed/doubled IDs, unsafe paths, changed inputs and source overlap where relevant.
- Defaults remain unchanged. New capture/revise are explicit writes to immutable runtime receipts; inspect/evaluate/audit are read-only.
- User-origin and confirmation labels are caller declarations with file bindings, not authenticated user identity or permission. Repository facts and inferred defaults cannot silently become accepted task requirements.
- Contract readiness means ready for execution review, not authorization. Block consequential unresolved questions only. No universal ambiguity score, no forced interviews, no automatic approval.
- All accepted ACs need current observed check evidence; a failed hard check cannot be overturned by semantic/independent support. Unexecuted, skipped and no-evidence never mean pass. Source hashing does not prove actual execution or truth.
- Observer first verified success stops successfully; regression, repeated failure, oscillation and exhausted limits stop without success. Unknown cost stays unknown. No claim that an observer enforces external runtime spending.
- Existing manual check declarations and verifier identity boundaries remain explicit; reuse native evidence/coverage and existing run contract comparison if used.
- All integration modules and docs must distinguish structural/data verification from semantic completion and permissions.
- Tests use synthetic vaults only. No company data, personal names, machine paths or secrets in public source.
- Do not hand-edit antigravity generated copies. Root rebuilds them and updates package routing/version.

## Tasks
- [ ] 1. Write failing tests, implement task contract capture/inspect/revise and immutable source binding. Test blocking questions, provenance, revision parentage/staleness, forged IDs, unsafe/changed sources, CLI. Owner task_contracts.
- [ ] 2. Write failing tests, implement staged review over native evidence+coverage with command/skip/review distinctions and CLI. Test hard-fail veto, no-evidence, per-AC completeness, model-only support, stale contracts/reports, unknown IDs. Owner staged_evaluation.
- [ ] 3. Write failing tests, implement read-only cycles audit over recomputed staged reports with fixed criteria/limits and CLI. Test first-success, no-progress, oscillation/regression, budget/unknown cost, final-generation success, stale inputs and false completion. Owner improvement_cycles.
- [ ] 4. Root integrates examples/documentation, skill/Codex routes, package copy lists, bilingual README and release version0.49.0 surfaces.
- [ ] 5. Independent cross-feature security/behavior review, resolve findings with regression tests; execute targeted checks, full suite and required offline regressions. Rebuild generated Antigravity package.
- [ ] 6. Create candidate PR and wait for required CI. Build/verify release assets, public reference citations and registry entry. Follow release-integrity procedure, publish v0.49.0 and independently verify exact public assets/commit/Pages. Record vault note/log/partial handoff/local commit.

## Review focus
1. Host-inferred defaults relabeled as user confirmation without revision or exact source evidence.
2. A passing summary hides an unexecuted/failed/missing acceptance check.
3. A revised contract reuses old report/decision authority.
4. Repeated errors, unknown costs, limit exhaustion or criteria weakening falsely produce completion.
5. Parent/source/receipt aliasing, symlinks, changed config and oversize JSON leak data or overwrite existing records.

## Ledger
- Ruling: work in isolated worktree from public v0.48.0; stale local v0.45.1 remains untouched.
- Ruling: ship original stdlib opt-in helpers, no Ouroboros runtime dependency or autonomous executing controller.
- Ruling: publication instruction authorizes plugin release and its public reference catalog update; does not authorize installing into active company vault.

