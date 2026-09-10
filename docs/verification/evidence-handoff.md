# Verification evidence and handoff — local verification

Date: 2026-09-10. Source base: `351cef76232e965fe954abf940c6273b290992a3`
(v0.10.0). This is an unreleased source increment, not an installed-plugin upgrade.

task_id: `agentic-vault-evidence-handoff-20260910`

artifact_paths:

- `skills/agentic-vault/scripts/vault_evidence.py`
- `tests/test_vault_evidence.py`
- `docs/evidence.md`, `docs/plans/2026-09-10-verification-evidence.md`
- `README.md`, `assets/templates/handoff.md`
- `commands/vault-session-start.md`, `commands/vault-session-end.md`
- `skills/agentic-vault/SKILL.md`, `skills/agentic-vault/references/codex.md`

verification_commands_and_results:

| Check | Observed result |
|---|---|
| Baseline `python -X utf8 -m unittest discover -s tests -v` | 351 tests, 338 passed, 13 skipped, no failures; Windows Python 3.12 |
| New lifecycle test before implementation | Failed because the CLI did not exist; exit 2 |
| Review regressions before fixes | Numeric overflow accepted; distinct-case evidence reference omitted; oversized report source accepted after checksum recomputation |
| Final `python -X utf8 -m unittest discover -s tests -v` | 406 tests in 97.728 seconds; 391 passed, 15 skipped, no failures/errors |
| Independent `python -m unittest discover -s tests -p test_vault_evidence.py -v` | 55 tests in 37.068 seconds; 53 passed, 2 skipped, no failures/errors |
| `python scripts/evaluate_recall.py` | 16 bilingual lexical fixture queries; recall@3 and MRR 1.0; no failed queries or evaluation errors |
| `python -m compileall -q hooks skills/agentic-vault/scripts scripts` | Exit 0 |
| `git diff --check` | Exit 0 |
| Documentation example in a temporary vault, executed by `/root/evidence_docs` | Snapshot, three real fixture tests, report record, current handoff with an explicit Linux gap; modifying the log produced stale, nonzero exit and empty preserve |
| Documentation routing and release metadata | Seven existing release metadata tests passed; links and handoff headings/anchor preserved |

Coverage includes pending/current/stale transitions, immutable finalization,
Korean text, byte-exact hashes, mixed verified/gap/failed/regression reports,
changed/missing/unreadable files, strict report and receipt validation, path and
deny-zone policy, read-only queries, writer locks, interrupted/failed atomic
publication, configured limits, policy changes during capture, and inert command
strings. A matching binding is not treated as a passing claim.

The first implementation run exposed a Windows metadata difference: after atomic
rename, `lstat` and `fstat` can disagree on creation time for the same file. The
comparison now uses ctime as an additional change indicator only outside Windows;
file identity, size, mtime, link count and SHA-256 remain checked. No test assertion
was relaxed to ignore changed content.

Independent review found and resolved unconditional case folding of file identity
keys, numeric JSON overflow such as `1e309`, the report-source size limit when
reading a checksummed receipt, and documentation wording about the observation
interval. The regressions failed before their fixes. Reserved and denied path
checks retain the existing conservative policy.

verified_by: Codex `/root/evidence_design_review`, **2026-09-10 19:53:47 KST**.
Read-only design/code review and independent 55-test rerun passed. SHA-256 values
of the ten reviewed files were unchanged before and after verification:

| File | SHA-256 |
|---|---|
| `skills/agentic-vault/scripts/vault_evidence.py` | `8babe00a8e9d0e150c32bf4a7710d88e49ba6a063fa4284192daaea5816bec93` |
| `tests/test_vault_evidence.py` | `865c899223230f225a6aa8dbfceea53196a1b4f29ca97f6f8d872ee1b597d281` |
| `docs/evidence.md` | `04a7ee0faf23638403f79a6e03654f1308fbb79d55238e720af76c9269f48bbf` |
| `docs/plans/2026-09-10-verification-evidence.md` | `06ebac526c2d78786ebd781f61b4c86119c9156cccb9e18cc665f7221e253cf6` |
| `README.md` | `7e4f2a93708ac6fc770450b03275c222982987f60565038822bcc11d7350ff6c` |
| `assets/templates/handoff.md` | `bdc901918a9f164bdcfda2c8f64a0df08dae345bc9a90251095839f4060ee388` |
| `commands/vault-session-start.md` | `38cd98f9119c6ff9015ba266c8fc24c8731d33a060c46ee5b37849d4e53454c2` |
| `commands/vault-session-end.md` | `6361945929da676118a299182ffe99a3d78899f071db7413a0268cfea3a280fe` |
| `skills/agentic-vault/SKILL.md` | `309472403c7129d7df0b6f88b0b9093ecf86e98bbcfac23c1b1281551860a501` |
| `skills/agentic-vault/references/codex.md` | `cda124be597453397cadca8dc15bba47a2b4d11a967b8b8b4aa91153077faab4` |

assumptions:

- Verification describes Windows Python 3.12.10. The existing CI matrix is not a
  record that this unpublished change ran on other operating systems or Python versions.
- Only explicitly declared files are bound. There is no OS-enforced frozen test
  copy, semantic truth check, verifier authentication, signature or test executor.
- Reports and derived next actions are evidence data; they cannot create new
  authority. Existing authorization, partial-edit rules and hook trust still apply.

unresolved:

- New tests skip symlink creation without Windows privileges and distinct-case
  files on this case-insensitive filesystem. A platform-independent PurePosix
  receipt test exercises the missing distinct-case binding regression.
- Linux/macOS execution, other Python versions and hosted CI have not run for this
  change. The 13 existing baseline platform skips remain.
- No measured improvement in multi-session task quality, time or token use is
  claimed. The fixed-task pilot in `docs/evidence.md` remains prospective.
- Public release, remote push and live installed-cache updates are not part of this change.

next_safe_action: Use the local source CLI for an explicitly scoped task, taking
the snapshot before verification; consult current handoff results alongside the
user's current requirements. A later release can run the existing hosted matrix
and update installation metadata in its own scope.
