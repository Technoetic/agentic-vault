# Note withdrawal & supersession lifecycle (`vault_lifecycle.py`)

`skills/agentic-vault/scripts/vault_lifecycle.py` is an opt-in, offline, read-only standard-library helper added in `v0.59.0` (inspired by the artifact-governance lifecycle patterns in [openai/math](https://github.com/openai/math), Apache-2.0).

```sh
python skills/agentic-vault/scripts/vault_lifecycle.py --vault <vault> check [--json]
```

## Frontmatter fields

- `status: withdrawn` — marks a note whose proof or supporting evidence has been retracted.
  - `withdrawn_reason`: non-empty explanation of the gap (checked for presence only; raw text is never echoed in JSON/CLI diagnostics).
  - `withdrawn_revision`: optional pinned full 40- or 64-character lowercase hex Git SHA pointing to the pre-withdrawal revision.
  - **Scope distinction:** `withdrawal_scope` is fixed to `proof_or_evidence_only`; retracting evidence does not assert that the underlying proposition is false.
- `status: superseded` — preserves an earlier edition unchanged while pointing readers to its replacement.
  - `superseded_by`: wikilink (`"[[Replacement Note]]"`) or allowed vault-relative path to the superseding note.

## Warning codes & cascade report

- Output schema: `agentic-vault/lifecycle-audit/v1` (`authority`: `lifecycle_audit_only`).
- `withdrawn-missing-reason`, `withdrawn-invalid-revision`
- `superseded-missing-target`, `superseded-target-unresolved`, `superseded-cycle`
- `cites-withdrawn-note` — an `active`, `draft`, or omitted/empty-`status` note links (`[[...]]`, in frontmatter or body outside code fences/spans/HTML comments) to a `withdrawn` note.
- `cites-superseded-note` — an `active`, `draft`, or omitted/empty-`status` note links to a `superseded` note without also linking to its direct or terminal replacement.
- `lifecycle-incomplete` — input limits or unreadable files truncated coverage.
- `cascade` — bounded BFS (`MAX_CASCADE_DEPTH = 16`) listing `direct_dependents` and `transitive_dependents` among active/draft/omitted-status notes for each withdrawn or superseded root.

Exit codes: `0` (clean), `1` (warnings or incomplete), `2` (invalid vault, config, or arguments).

## Boundaries

- Read-only and offline: never mutates notes, executes commands, queries Git, or accesses the network.
- Ignores `deny_zones`, `exclude_dirs`, runtime folders, registered linked worktrees, and symlinks/reparse points.
- Does not modify any of the seven certificate-bound warning-promotion checker files (`checker_sha256` remains unchanged).
- Since `v0.60.0`, `vault_recall.py` / `vault_retrieval.py` also reads frontmatter `status: withdrawn | superseded` into `temporal.lifecycle_status`, excludes `withdrawn` and unbounded `superseded` under `--as-of` (or via opt-in `--exclude-withdrawn` / `--exclude-superseded`), and `vault_healthcheck.py` supports opt-in `--exclude-retired` / `exclude_retired_from_active_checks` for §7 stale and §8 SSOT checks.
