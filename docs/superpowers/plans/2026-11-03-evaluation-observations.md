# Stage Interventions and Korean Observations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add optional stage-specific experiments and fresh paired Korean model
observations, then publish the verified v0.39.0 release.

**Architecture:** Two checkout-only evaluators reuse current pure modules,
bounded transport and scorers; neither modifies the installed engine defaults.
Each owns its datasets, source bindings, tests and explanatory documentation.

**Tech Stack:** Python 3.10+ standard library, unittest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-03-evaluation-observations.md`

## Global Constraints

- Python 3.10+ standard library; no new runtime dependency.
- Existing hook/config/search and evaluator CLI/output defaults remain unchanged.
- No changes to the seven certificate-bound checkers or committed LF corpus.
- Default runs are offline controls; inference requires explicit local opt-in.
- Never report synthetic responses/HTTP contract services as real LLM results.
- Source/replay hashes bind bytes and declared observations, not authority or truth.

## Review Focus

1. Identical paired evidence still requires independent responses; errors in one
   arm must not be replaced by the other's result (Task 2).
2. Replay fingerprints include labels, actual evidence/completeness, both arm IDs,
   model/limits and the current local code closure (Task 2).
3. Stage interventions affect real stored/consumer input; retrieval must actually
   feed returned retrieval context and leave persisted bytes unchanged (Task 1).
4. Invalid output retains valid observed usage while timeouts stop future requests;
   no raw prose/error/credential bytes leak (Tasks 1 and 2).
5. Final artifact/CI/certificate observations correspond to the exact frozen source
   and no environment skip or model non-observation becomes an efficacy claim (Task 4).

## Task 1 — Stage-specific original controls and local-model path

**Files:** Create `scripts/evaluate_memory_stages.py`,
`tests/test_memory_stages.py`, `docs/memory-stage-evaluation.md`.

**Interfaces:** The three spec functions; consume unchanged lifecycle
`solve_sample`/`ModelLimits` and existing formation/trust/retrieval primitives.

- [ ] Add tests for actual writing/management/retrieval mechanisms and isolated
  clean/poison comparisons; missing module/function fails for the expected reason.
- [ ] Implement only fixed fixtures, named contrasts and bounded transports.
- [ ] Test stage-only evidence, no labels in requests, incomplete exposure,
  invalid/timeout usage and static source fingerprints.
- [ ] Document deterministic interventions and limits; run focused tests and smoke.
- [ ] Independent spec/code review, corrections, scoped commit.

## Task 2 — Fresh Korean collection and bound replay

**Files:** Create `scripts/evaluate_model_korean_memory.py`,
`tests/test_model_korean_memory.py`, `docs/model-korean-memory.md`.

**Interfaces:** The five spec functions; consume unchanged Korean strict grader,
retrieval and shared local transport. No Task 1 interface dependency.

- [ ] Add failing paired-request, response parser and stale/swapped replay tests.
- [ ] Implement fresh per-arm collection with public payload only and safe receipt.
- [ ] Verify full fixture100requests, identical evidence independence, actual HTTP
  contract, valid usage on invalid content, budget/timeout stops and old behavior.
- [ ] Document actual inference versus scripted/HTTP/replay evidence.
- [ ] Independent spec/code review, corrections, scoped commit.

## Task 3 — Integrate optional tools and release metadata

**Files:** `.github/workflows/tests.yml`, current README/SECURITY/Codex docs,
plugin manifests, engine version stamp, release metadata tests and new v0.39.0
release/verification docs. Do not bulk-rewrite historical sections.

- [ ] Add offline smoke steps for Tasks1/2 and research/source/scope documentation.
- [ ] Deliberately update current version/install refs and regression expectations.
- [ ] Run changed metadata/default compatibility checks and full existing gates.
- [ ] Whole-branch independent review and scoped integration commit.

## Task 4 — Publish exact verified source

**Artifacts:** Ignored `.superpowers/releases/v0.39.0/`; sanitized public ZIP,
verification.json and SHA256SUMS only. Source publication is user-authorized.

- [ ] Freeze final source, validate Claude/native isolated Codex and certificate.
- [ ] Integrate master and annotated tag without replacing existing tags; verify
  exact master/tag CI mandatory11jobs and all actual suite totals/skips.
- [ ] Rebuild ZIP at UTC0, compare Git blobs and independently review draft bytes.
- [ ] Publish immutable Latest v0.39.0 and verify public downloads/attestations.
- [ ] Record accurate final six-field handoff and user-facing release link.
