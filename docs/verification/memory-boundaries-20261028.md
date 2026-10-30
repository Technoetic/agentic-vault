# Memory boundaries: development source fingerprint (2026-10-28)

This historical development record describes the approved
[memory-boundaries specification](../superpowers/specs/2026-10-28-memory-boundaries.md)
at implementation commit `e9d382a6877411c377ccb49c0d7f3fba604ba1f8`, before v0.33.0
release preparation. Public metadata was v0.32.0 at that point. Current release
metadata and pending gates are in the [v0.34.0 verification record](v0.34.0.md);
this dated note does not certify a release, installation or deployment.

## Fingerprints and certificate coordination

The actual source certificate CLI declares `checker_sha256`:

`af8f4c5ab9fc809a4998e55870e769d5f617d6742a53d0c5c2844df7ff3d7b92`

The published v0.32.0 checker fingerprint remains:

`9bca12a79bfa87ff2bf44afecb2e5527e19c199b95fc1435d9513d2be73b3563`

The [v0.32.0 release note](../releases/v0.32.0.md) and
[v0.32.0 verification record](v0.32.0.md) are historical records and retain that
published fingerprint. The source hash differs because the opt-in support-row
and binding diagnostics changed `vault_ssot.py`, one of the seven bound files,
even though existing support output is unchanged when the new flags are off.
A certificate binds actual bytes, rather than the version label alone.

Before switching to or from these source bytes, return promoted levels to `warning`;
align every engine copy that checks the vault (Claude Code, Codex, and the git vault's
`00-meta/scripts/`) to the same bytes; run `vault_warning_policy.py --certificate` on
that installation, then promote again.
Use the same order when returning to published v0.32.0. Version-stamped upgrade
tools do not establish byte equality for an unreleased source checkout. Installed
plugins and vault engine copies were not changed as part of this work.

The existing `.gitattributes` pins Python files and the bound
`skills/agentic-vault/scripts/resources/lint-benign-v1.json` corpus to LF.
The declared source fingerprint assumes those canonical checkout bytes; a locally
modified bound file or different line endings require a different certificate.
No checker logic was changed to force a particular fingerprint.

## Integration regression checks

The pre-repair targeted run on `3b0b3bac5fb83f4266394dd4c471fb7884763d40`
reproduced a BOM parsing error, three missing documentation-index rows and the
incorrect comparison of current source bytes with the published fingerprint.
The repair removes only the BOM from the new support-row test, registers the
three guides, and separates current-source certificate checks from explicit
published v0.32.0 hash counts, required statements and older history checks.
The new addendum assertions failed before these addenda existed.

Run from this checkout:

```console
python skills/agentic-vault/scripts/vault_warning_policy.py --certificate
python -m unittest -v tests.test_linear_line_parsers.NoLazySuffixLeftTests tests.test_readme_truth.PublicDocsTests tests.test_release_metadata
```

The focused unittest command passed 33 tests after the repair on 2026-10-28.
The certificate CLI returned the declared source fingerprint above. The integrated
full-suite result is a separate validation step and is not asserted by this note.

## Feature guides and limits

- [State revision evaluation](../state-revision-evaluation.md): independent synthetic
  snapshots and declared arithmetic dependencies; controls do not measure model quality.
- [Evidence insufficiency evaluation](../evidence-insufficiency-evaluation.md): bounded
  structured claims; declared slot coverage does not prove absence or universal relevance.
- [Support-row diagnostics](../support-row-diagnostics.md): opt-in content-free row
  references and conservative physical-line binding observations, not fact verification.
