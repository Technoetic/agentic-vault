# Brief Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement D1–D4 and P1–P6, then integrate and verify the actual local installation.

**Architecture:** Extend existing bounded Markdown helpers. New focused stdlib modules expose policy/hash-bound APIs and CLIs; default behavior stays compatible. Independent files can be developed concurrently; shared-file changes, commits, dependent tasks and integration remain sequential.

**Tech Stack:** Python 3.10+, unittest, Markdown, JSON, native host plugin CLIs; optional tokenizer/providers.

**Spec:** `docs/superpowers/specs/2026-10-07-brief-hardening-design.md`

## Global Constraints

- Standard library core, no implicit network/model call or new mandatory dependency.
- Preserve valid legacy defaults, deny/exclude/source hashes, originals and user configuration/activation.
- New findings warn first; promotion requires hash-bound benign zero-false-positive evidence.
- Runtime writes are ignored, bounded and source-policy controlled; no Windows os.kill(pid, 0).
- Red/green output and independent evidence precede success claims; only root stages/commits shared worktree files.

## Review Focus

- Unicode controls mixed with legitimate Korean/emoji: inspect copies only, preserve original bytes (Task 1).
- Malformed hook input, denied configured notes and tiny/zero budgets: compact never bypasses policy/cap (Task 2).
- Ambiguous aliases/duplicate basenames/fenced headings: explicit provenance, no guessed link destinations (Tasks 3–4).
- Lock expiry or stale receipt races: current owner/base checks prevent clobbering (Tasks 5–8).
- Forged trust/evidence/sample metadata or pathological regex: no authority, bounded cost and visible abstention (Tasks 5, 8–9).

### Task 1: D1 normalization

Files: `skills/agentic-vault/scripts/jev_client.py`, `tests/test_jev_client.py`, `tests/test_rule_sources.py`.
Interfaces: existing `_normalized(str)->str`, `contains_sensitive(str)->bool`, `build_payload(dict)->dict`; callers unchanged.
- [ ] Add four fillers × four fake token families at detector/payload boundaries and benign Korean/selector controls.
- [ ] Run focused tests and save expected failing assertions in `.superpowers/brief-hardening/task1-red.txt`.
- [ ] Extend normalization with explicit code points/ranges; preserve raw payloads and no all-Lo/Mn/Cn rule.
- [ ] Run covering tests; independent reviewer checks actual callable surfaces and controls.
- [ ] Root commits only the owned files with `build:` prefix.

### Task 2: D2 compact constraints

Files: `hooks/session_start.py`, `hooks/hooks.json`, `tests/test_session_start.py`, related hook-source tests.
Interfaces: bounded `read_hook_event()->dict`, compact-only constraint source; `compose_context` default contract unchanged.
- [ ] Red tests for compact group excluding healthcheck, event parsing, fixed-block preservation/hash, zero budgets, denied notes and 9,500-unit cap.
- [ ] Implement separate matcher and compact-only source binding/composition; startup remains byte-compatible.
- [ ] Run session/doctor/rule-source covering tests, inspect native host support without executing NS hooks.
- [ ] Independent review and root owned-file commit.

### Task 3: P1/D4 evaluation and query expansion

Files: `vault_recall.py`, `vault_retrieval.py`, new `vault_query_expansion.py`, `scripts/evaluate_recall.py`, hard fixture and tests.
Interfaces: `expand_query(vault, query, *, enabled=False, mapping=None)->dict` with deterministic terms/provenance/limits; retrievers accept opt-in expansion. Evaluator accepts top-k 3/5 and exposes claim coverage, nDCG and paired randomization.
- [ ] Red tests for hard Korean-English queries, aliases literal-vs-expanded ablation, ambiguous/denied/malformed mappings and deterministic statistics.
- [ ] Add at least 32 Korean and 32 English independently labeled hard queries covering six scenarios; no expected paths passed to retriever.
- [ ] Implement bounded expansion and evaluation metrics, leaving defaults/legacy reports compatible.
- [ ] Run recall/retrieval/evaluation tests and strict same-budget ablations; independent review.
- [ ] Root owned-file commit.

### Task 4: D3 warnings and promotion contract

Files: new `vault_lint_extensions.py`, new `vault_warning_policy.py`, `vault_healthcheck.py`, tests/harmless corpus.
Interfaces: `analyze_notes(notes:dict[str,str], config:dict)->list[dict]`; `validate_promotion(policy, checker_hash, corpus_hash)->dict` fails explicit escalation without matching zero-FP evidence.
- [ ] Red tests for missing heading/block anchors, invalid dates, ambiguous duplicate basenames and valid path-qualified/fenced/Unicode controls.
- [ ] Add warning-only section; preserve original fatal gates and zero-FP benign corpus.
- [ ] Implement explicit promotion evidence validation with deterministic issue IDs; no default escalation.
- [ ] Run healthcheck/config/staged/upgrade tests; independent review and root owned-file commit.

### Task 5: P2 provenance and capture

Files: new `vault_provenance.py`, `jarvis_bridge.py`, capture tests, staged provenance integration in lint extensions, ingest/process commands.
Interfaces: capture metadata `captured_via/content_origin/captured_at/body_sha256`; `derive_provenance(vault, sources, target)->dict`, `validate_provenance(...)->dict` rereads approved originals, hashes and independent evidence.
- [ ] Red tests for own vs forwarded/url, duplicate capture replay, derived weakest trust, source drift and self-asserted verification.
- [ ] Add host metadata and explicit derivation/validation CLI, preserving body bytes and capture idempotence.
- [ ] Add warning-only privileged-consumer checks and pre-quarantine provenance command steps.
- [ ] Run Jarvis/provenance/staged tests; independent review and root owned-file commit.

### Task 6: P3 cooperative state

Files: new `vault_state.py`, `hooks/session_checkpoint.py`, hook manifest, session-end command, runtime exclusions and tests.
Interfaces: `advisory_lock(vault, key, *, ttl_seconds, wait_seconds)`; `patch_note(vault,path,expected_sha256,edits)->dict`; `project_handoff(...)`, `detect_removed_blockers(...)`, minimal checkpoint CLI. Locks/runtime under `00-meta/.agentic-vault/runtime/`, ignored and excluded from all note inventories.
- [ ] Red tests for owner-safe release, stale bases/locks, partial-byte preservation, concurrent updates, stable IDs and removed blocker warnings.
- [ ] Implement atomic bounded hash-bound partial edits and deterministic projection from explicit allowed log/tasks/anchor.
- [ ] Add model-free PreCompact/SessionEnd checkpoints with no business-note edits; bounded runtime paths and hashes only.
- [ ] Run concurrency/Windows/path/session tests; independent review and root owned-file commit.

### Task 7: P4 temporal ledger

Files: new `vault_ssot.py`, ledger template, lint extension integration, vault-upgrade command, migration tests.
Interfaces: `parse_ledger(text)->dict`, `check_ledger(...)->list[dict]`, `prepare_migration(vault,path)->dict`, `apply_migration(...,approve=False)->dict` using Task6 locks and expected hashes.
- [ ] Red tests for disjoint history, overlapping active values, source-less confirmed rows, column aliases and escaped pipes.
- [ ] Implement explicit v1→v2 dry-run/apply preserving original values/prose and blocking stale hashes; do not migrate real user facts.
- [ ] Add template/upgrade dry-run documentation and warning integration.
- [ ] Run ledger/migration/upgrade tests; independent review and root owned-file commit.

### Task 8: P5 lesson deltas and rules

Files: new `vault_lesson_delta.py`, new `vault_declarative_rules.py`, lesson/session-end command wiring and tests.
Interfaces: `prepare_delta(vault,path,operations)->dict`, `apply_delta(...,approve=False)->dict`; `evaluate_rules(text,rules)->dict` with literal default and timed bounded regex worker.
- [ ] Red tests for ADD/INC/EDIT/RETIRE, duplicate/stale IDs, untouched CRLF/LF bytes, retirement preservation and receipt/metrics links.
- [ ] Implement revision-bound delta proposals using Task6 locks; retain explicit approval and historical IDs.
- [ ] Implement existence/substitution/occurrence data rules, regex input/pattern/time caps, timeout diagnosis and no code execution.
- [ ] Run delta/rules/proposal/lesson tests; independent review and root owned-file commit.

### Task 9: P6 coverage, egress and calibration

Files: new `vault_compile_quality.py`, new `vault_token_calibration.py`, egress warning integration, ingest/process commands and tests.
Interfaces: `check_compile(vault,sources,targets,required_claims)->dict` revision-bound literal facts plus advisory semantic references; `calibrate(samples,model_id,generation)->dict` and `check_drift(...)` with optional tokenizer/API adapters.
- [ ] Red tests for lost numeric/date/name facts, source/target drift, denied paths, fake sensitive/Korean identifier patterns, forged or nonfinite sample metadata and calibration drift.
- [ ] Add pre-quarantine coverage/provenance checks and warning-only D1-normalized egress scans; optional scanners never required.
- [ ] Implement bounded model/generation-bound calibration with source labels and explicit coefficient selection; preserve default estimator.
- [ ] Run compile/calibration/egress and optional real tokenizer synthetic checks; unavailable credentialed adapters remain explicit.
- [ ] Independent review and root owned-file commit.

### Task 10: package, review and local application

Files: shared SKILL/commands/Codex refs, install verifier, README/usage/verification, manifests/CI, own NS records after all code freezes.
- [ ] Connect every new CLI to actual commands/resource verification and update schema/template/ignore assets without changing user values.
- [ ] Run focused integration then one complete unittest suite, compileall and diff checks; broaden only for new failures/changes.
- [ ] Fresh broad independent review covers dependencies and privileged-write/host/runtime boundaries; resolve required findings and rerun covering checks.
- [ ] Root commits verified source, fast-forwards clean local master and installs a unique local version from Git archive while preserving prior caches/checkouts/configs/scopes.
- [ ] Independent installed synthetic/real-allowed-note read-only smoke; record commands/hashes/six fields and actual limitations.
- [ ] Partially update and locally commit only own NS note records; MemoryHub durable handoff. Remote publication requires an explicit release action.
