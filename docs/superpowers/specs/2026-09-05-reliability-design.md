# Reliability and verifiable recall design

The local improvement branch preserves the Claude Code plugin, Markdown vault,
Python 3.10+ floor, standard-library-only runtime, and existing user configuration.
It addresses the review of upstream commit `74890f6`. A quality score is not an
acceptance criterion: observable behavior and clearly stated limits are.

## Acceptance criteria

1. SessionStart never reads configured paths outside the vault or in deny zones,
   including absolute paths, traversal, symlinks and Windows junctions.
2. Invalid configuration injects no notes and emits a concise diagnostic without
   note contents; a non-vault remains a quiet no-op.
3. Each injected section stays within its configured **estimated** token budget,
   including its labels/truncation marker. Missing budgets use documented defaults;
   zero disables that section. Reads have a finite byte limit before allocation.
4. Startup and clear preserve existing behavior; resume is covered too. Hook
   interpreter fallback chooses a usable Python, not a retry after program failure.
5. Backups retain independent, immutable, uniquely named snapshots of tracked and
   untracked files, with SHA-256 manifests. Failed runs never publish a snapshot.
6. Source/destination overlap, symlink/junction escapes, concurrent backup writers,
   unsupported configuration, unreadable files and missing required Git history
   fail visibly. No existing snapshot is deleted automatically.
7. Verification detects corruption. Restore validates first and exports into a new
   directory, never overwriting an existing vault. Tests demonstrate recovery after
   deleting an untracked binary from the source.
8. Deterministic local recall returns ranked, source-attributed excerpts under an
   estimated context budget, excludes deny zones and unsafe paths, and reports
   search/read limits. It makes no semantic-recall or model-intelligence claim.
9. A checked-in bilingual retrieval fixture suite measures source recall with
   literal expected note paths. It is explicitly a lexical retrieval regression
   benchmark, separate from real Claude session quality.
10. Existing tests pass, new regression tests exercise real temporary files and
    subprocesses, CI covers supported Python/OS combinations, and documentation
    states the actual guarantees and untested live integrations.

## Boundaries and ownership

- Session task: `hooks/session_start.py`, hook runner/wiring, shared
  `skills/agentic-vault/scripts/vault_paths.py`, and their focused tests.
- Backup task: `backup_vault.py` and its focused tests. Existing CLI flags
  `--vault` and `--target` remain valid; new verification/restore are additive.
- Recall task: `vault_recall.py`, retrieval tests and fixtures, evaluation runner.
- Integration task: commands, templates, metadata, documentation, CI and final
  integration tests. The controller owns these to prevent concurrent edits.

Shared path API: `resolve_note_path(vault: Path, rel_path: str,
deny_zones: Sequence[str] = ()) -> Path`. It returns a resolved, contained path
with no symlink/reparse component; invalid paths raise `ValueError` or `OSError`.
Consumers use the existing healthcheck `validate_config` and `estimate_tokens`.
The installed standalone healthcheck does not gain a new module dependency.

## Deliberate trade-offs

Snapshots use more disk than a deletion-synchronized mirror. Retention is explicit,
never automatic. Restore exports a verified snapshot instead of merging files.
Lexical search provides reproducible retrieval and citations; semantic judgment,
summarization, and lesson promotion still require the model and human review.
Estimated tokens are not exact provider billing tokens. No live API calls,
Telegram messages, plugin installation, remote push, or publication are part of
the local implementation.
