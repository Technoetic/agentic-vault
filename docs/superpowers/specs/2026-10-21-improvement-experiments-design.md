# Harness²-inspired improvement experiments

The user approved the preceding research recommendation with “구현해”. Implement the first scoped delivery: candidate lineage, edit-level evidence, and connections to existing frozen run comparison. This is an opt-in data workflow; no model runner, automatic patching, hook, installation or release is requested.

## Contract

- Python 3.10+, standard library only. Existing defaults and public contracts stay unchanged.
- New module `skills/agentic-vault/scripts/vault_improvements.py`, imported as `vault_improvements`.
- Reuse `vault_runs.Store`, source/JSON validation, immutable publication, cooperative leases and run inspection/comparison. No new path policy or receipt store.
- Successful output carries `authority=advisory_host_observations_only`, `requires_review=true`, `applies_changes=false`. CLI errors follow the existing content-free `{ok:false,error:code}` contract. Observations and hashes do not prove truth, identity, causality, permissions or general effectiveness.
- Public functions `prepare(vault, input_path)`, `inspect(vault, plan_id)`, `compare(vault, plan_id)`, `record(vault, plan_id, report_path)`, `inspect_record(vault, record_id)`. Safe `ImprovementError` codes and JSON-only CLI; invalid argparse values must not echo input.
- CLI: `--vault PATH prepare --input FILE`; `inspect ID`; `compare ID`; `record ID --report FILE`; `inspect-record ID`. Help remains available.

## Plan schema

Input JSON has exactly `version`, `task_class`, `nodes`. Version is integer1, task_class a safe ASCII label. Exactly four nodes with unique safe `id`, unique `run_id`, and one of each role `baseline`, `general`, `specialized`, `composed`. Each node has exactly `id`, `role`, `run_id`, `parents`.

- Baseline has no parents.
- General and specialized each have exactly the baseline node ID as parent.
- Composed has exactly the general and specialized IDs as parents.
- Canonicalize nodes by role order above and parents by ID; reject duplicates, missing nodes, unrelated parents/cycles and extra fields.
- Every run already exists as a valid prepared `vault_runs` receipt. Runs may complete after plan creation. Require matching frozen fixture/tools/model/provider/token budget/config via existing compatibility semantics. Candidate and instruction files are treatment and may differ.
- Bind definition JSON fingerprint, canonical definition, compatibility hash, config hash. No full user note content is copied to receipts. Plan ID is the hash of canonical payload; publish immutable `improvement-plan-ID.json`, kind `improvement_plan`. Same inputs reuse the current receipt.
- Every inspect/use revalidates definition bytes/parsed content, plan kind/ID/checksum, run input bindings and compatibility. Config/source change, receipt tampering, stale or unsafe paths fail closed.

## Comparison

- `inspect` returns the plan and per-node prepared/complete status, read-only.
- `compare` with any incomplete node returns `status=pending`, `decision=abstain`, no winner, explicit incomplete IDs. Do not silently drop missing runs.
- With all four complete, use existing `vault_runs.compare` for each nonbaseline against baseline, returning those comparison IDs and complete outputs. Never run model, shell, source instructions or scripts.
- Hard failure or error of baseline makes decision `abstain`. Ineligible candidates are retained in output but cannot be recommended.
- A unique eligible candidate with strictly greatest positive score delta gives `decision=candidate`, its node ID in `candidate_id`.
- No positive eligible delta gives `decision=keep_base`, baseline node ID in `candidate_id`.
- A tie among best positive candidates gives `decision=abstain`, `candidate_id=null` and explicit reason. Fixed candidate count is not evidence of effectiveness. Costs are not automatically measured by this v1.
- Comparison is advisory; it cannot call proposal apply or promote lessons.

## Reflection schema

Report JSON exactly `version`, `edits`, `lesson`. Up to16 edit rows. Each row exactly `node_id`, `component`, `change`, `observed`, `isolation`, `verdict`, `evidence`.

- node_id is a nonbaseline plan node; component is a safe bounded label; change/observed bounded nonempty sensitive-scanned strings.
- isolation is `single_component`, `multiple_components`, or `unknown` (host observation, not independently certified).
- verdict is `helped`, `hurt`, `no_observed_effect`, or `confounded`. Multiple/unknown isolation requires confounded. Multiple component rows for the same node cannot assert isolated helped/hurt.
- Evidence contains at most16 unique refs, each exactly `run_id`, `case_id`, `repetition` (strict integer, no bool). References must be to baseline or the row node and real case/repetition observations with `status=complete`; an error outcome is not a completed observation. Require at least one matching baseline/node case+repetition pair. All bound evidence files remain current through native run inspection.
- `helped`/`hurt` requires at least two distinct paired observations; a single rollout contrast is confounded. References merely bind recorded observations; they cannot certify causal claims.
- `lesson=null` is NO_ENTRY: valid reflection without a durable lesson. Do not require inventing lessons.
- Non-null lesson exactly `cue`, `operation`, `edit_indices`, `confidence`. Strings bounded; confidence one of low/medium/high. Indices nonempty, unique strict integers indexing edits. Every cited edit must be helped with single_component isolation and its node must have an eligible, positive baseline comparison. Confounded/hurt/no-effect alone must never become a lesson.
- All four runs must be complete when recording. Store immutable source-bound reflection, plan ID, current completion payload hashes and normalized edits/lesson. `record` returns `id`, `path`, `lesson_candidate` (or null) for host review. It never writes knowledge notes/proposals or claims general transfer.
- Reflection eligibility is recalculated from the current compatible native completed receipts using native score/hard-error rules. A prior persisted comparison receipt is not required; the separate compare operation creates native comparisons and returns their IDs. Reflection inspection does not create comparison records.
- `inspect_record` is read-only, checks kind/ID/checksum, current plan/source/report/run completion bindings and recalculates validated report. A stale reflection is unusable. Append immutable records; no directory-wide auto-discovery.

## Verification and deliverables

- Real temporary-vault tests through existing run prepare/record/compare: improving, regressing, tie, hard failures, pending, incompatible conditions, lineage errors, NO_ENTRY, isolated/unisolated claims, source/evidence/config drift, corrupt receipts, duplicate/nonfinite/oversized JSON, unsafe paths and secret echo.
- Deterministic offline example runs four real run receipts and verifies coordinator decisions. It is a functional contract example, not a measured model improvement or Harness² benchmark reproduction.
- Document CLI/JSON and links in skill/Codex routing/docs index/README. Keep 15 existing slash commands and version0.26.0; no new release/version change or user installation.
- Full existing suite and required offline evaluation regressions once integration stabilizes; independent code review and repro commands. Scope source commit to feature branch; no remote push/publication.

## Sources

- https://arxiv.org/html/2610.14224v1#S2
- https://arxiv.org/html/2610.14224v1#A8
- https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/harness2/core/selection.py#L93-L111
- Existing AV v0.26.0 `vault_runs`, `vault_proposals`, `vault_evidence` and `vault_lesson_metrics` remain the operational boundaries.
