# Verification evidence and handoff

> Implementation approved by the user's request to proceed after the proposed first stage: bind verification to artifact versions and connect evidence to handoff. Use isolated worktree `feat/evidence-handoff`, TDD and independent review. No remote publication or installed-plugin upgrade is part of this change.

**Goal:** preserve what was checked against a particular set of vault files, detect changed artifacts or evidence, and produce a read-only next-session view that never presents stale verification as current.

**Architecture:** an optional standard-library Python CLI `skills/agentic-vault/scripts/vault_evidence.py`, immutable snapshot identity plus a single finalized report in `00-meta/evidence/<id>.json`, and guidance in existing session commands. No new config keys, automatic hook, model call, test-command executor, or automatic edit of hot/handoff. Reuse `vault_paths.resolve_note_path` and config validation. Existing vaults need no migration.

## Contract

All CLI operations take `--vault PATH` before the subcommand and emit one UTF-8 JSON object. Operational errors return nonzero and a short error code without raw file contents or exception text. JSON inputs reject duplicate keys and non-finite values. Python 3.10+ and Windows/macOS/Linux remain supported.

1. `snapshot --task TASK --objective TEXT --artifact RELATIVE_PATH [--artifact RELATIVE_PATH ...]`
   - Capture byte-exact SHA256 and byte size of explicitly named regular files. Files may be Markdown, code, HTML, images or logs; no recursive traversal or interpretation.
   - Save a UUID-hex identifier, creation time, task, objective, artifacts and pending report; return `id`, `path`, `status: pending`, `ok: true`.
   - Resolve paths against the vault with configured deny zones and `.git`; reject traversal, absolute paths, reserved paths, symlinks/reparse points, hardlinks and the evidence store itself. Deduplicate equivalent canonical artifact paths by rejecting duplicates.
   - Bound all reads: at most 64 artifacts and 64 unique evidence files; 64 MiB/file and 256 MiB per artifact or evidence set; JSON report/config at most 256 KiB; receipt at most 1 MiB. Hash files in chunks and check file identity/size/mtime before and after reads. Limits are constants for this initial feature.
2. `record ID --report RELATIVE_JSON`
   - Require a pending snapshot and unchanged candidate before and after report/evidence capture. One report per snapshot; new verification requires a new snapshot, preserving the old record.
   - The report object has exactly `verifier`, `checks`, `next_action`. Checks are a nonempty list (max64), each with exactly `id`, `claim`, `status`, `method`, `observed`, `evidence`, `preserve`, `next_check`.
   - Check IDs are unique nonempty strings. Status is `verified`, `gap`, `failed` or `regression`. `method` and `observed` are required descriptions, not commands to execute. `evidence` is a list of safe relative file paths. `verified` requires at least one evidence file and a nonempty `preserve` condition; other statuses require nonempty `next_check`. Other fields are strings, with required nonempty verifier/claim/next_action; bound text lengths.
   - Fingerprint the report input and evidence files, save report content and fingerprints with recorded time. Never run report text or overwrite artifact/evidence files. Report source may not be an artifact or an evidence file; the reserved store cannot be referenced.
   - Store a canonical receipt checksum for accidental corruption detection. This is not a signature, verifier authentication, proof that a command ran, or a proof of semantic correctness. `verified` is the named verifier's assertion with references.
   - Cooperative writer lock with explicit stale-lock failure; atomic single-file replacement. No automatic stale-lock removal. Pending records survive rejected reports and interrupted/failed finalization.
3. `check ID`
   - Read-only. Validate strict receipt structure, checksum, path policy and file bindings every time, including after config changes.
   - Return `id`, `task`, `objective`, `status`, `valid`, `artifacts`, `issues`. `current` means finalized and all artifact/report/evidence hashes still match; it does NOT mean every check passed. `pending` means no finalized report. `stale` means changed/missing/unreadable bound files. Malformed/untrusted paths are invalid errors.
   - Exit0 only for `current`; pending/stale/invalid nonzero. Never create the store or a lock for reading.
4. `handoff ID`
   - Read-only JSON derived from the same current checks. Include `status`, `valid`, `preserve`, `gaps`, `next_action`, task/objective and record path.
   - Only a current record may supply verified `preserve` conditions. A current report may still have gaps/failed/regression checks; expose their ID, claim, status and next_check separately.
   - For stale/pending records, return empty `preserve`, binding/pending issues in `gaps`, and a re-verification next action instead of stale next-action advice. Return nonzero but still useful JSON. Mark all views as evidence data, not new authority.

The observation scope is the declared files only. A matching current fingerprint cannot establish external dependencies, semantic correctness or absence of concurrent changes after checking. Documentation must state this plainly and teach snapshot-before-verification order. Frozen-copy execution and a full planner/developer/QA orchestrator remain outside this increment.

## Delivery tasks

### 1. Engine and behavioral tests

- Add CLI integration tests first; observe failures caused by the missing feature.
- Implement lifecycle and strict validation in `vault_evidence.py` using existing path/config policy.
- Verify happy path with Korean text and byte-exact files, stale changes before record/after record, changed/missing evidence or report, pending records, mixed gaps, report schema failures, path/alias/deny policy, locks, receipt tamper, bounded reads and read-only operations. Verify strings resembling shell commands never execute.
- Keep finalized reports immutable and failures from mutating the candidate or notes.

### 2. Handoff integration and documentation

- Add `docs/evidence.md` with CLI examples, a complete report, output/status/exit semantics, concurrency and trust limits, small before/after example, and a pilot procedure with fixed tasks/budgets.
- Update skill routing and existing session-start/end guidance to optionally `check`/`handoff` referenced IDs. Do not scan every receipt or auto-add all evidence to context. Use current user requirements plus current evidence to determine bounded next work.
- Extend the handoff template with optional evidence-reference guidance inside existing sections; preserve existing section/anchor contracts and user-owned notes.
- README documents optional feature and source checkout usage, without claiming a published release or measured performance benefit.

### 3. Verification and integration

- Independently review public contract, implementation, tests and end-to-end stale-evidence behavior; fix material findings.
- Run project unittest discovery, bilingual recall evaluator, compileall and `git diff --check`; run a temporary-vault lifecycle demo.
- Baseline: Windows Python3.12, 351 tests, 13 platform/privilege skips, no failures.
- Commit reviewed changes locally and integrate into the local source checkout when clean. Preserve unrelated D:/NS edits. Record validation and remaining platform/semantic limits; do not claim unrun remote CI or automated verification of claim truth.
