# Subject left boundary: development source fingerprint (2026-10-30)

This dated development record describes the `--bind-subject` subject left edge,
implemented on top of v0.33.1 (`4b213f3ed0a314595278adb7d70eb9a0c6fc61e5`). Public
metadata was v0.33.1 at that point. This is not a release: version badges, manifests,
installation refs and the [v0.33.1 verification record](v0.33.1.md) still describe
v0.33.1, and this note does not certify a release, installation or deployment.

## Change

`vault_ssot.py check-support --bind-subject` builds its subject patterns with
`_support_pattern(literal, subject=True)`. A normalized subject form that starts with
a non-ASCII letter then needs a left edge: a letter right before it blocks the match
(kana, Han, the representative SA ranges and U+119E do not), except right after one
of the five legal forms of the Korean Commercial Act written without a space. The
value patterns of the same diagnostic, literal support and
`_matches` are unchanged, so default output, `warnings`, counts and exit codes stay
as in v0.33.1, and only `binding_observations` and `binding_warnings` can differ.
The rule, its UAX #29 approximation, the U+119E tailoring and the kept limits are in
[Support-row diagnostics](../support-row-diagnostics.md#subject-left-edge).

## Fingerprints and certificate coordination

The actual source certificate CLI declares `checker_sha256`:

`be02b141ffed91cbe34f8cb2317a790c60075740bfe5d8dc435ef202f113174f`

The published v0.33.1 checker fingerprint remains:

`af8f4c5ab9fc809a4998e55870e769d5f617d6742a53d0c5c2844df7ff3d7b92`

The [v0.33.1 release note](../releases/v0.33.1.md) and
[v0.33.1 verification record](v0.33.1.md) are historical records and retain that
published fingerprint. The source hash differs because the subject left edge changed
`vault_ssot.py`, one of the seven bound files, even though default support output is
unchanged. The bundled harmless corpus and its replay result are unchanged (21 cases,
0 findings). A certificate binds actual bytes, rather than the version label alone.

Before switching to or from these source bytes, return promoted levels to `warning`;
align every engine copy that checks the vault (Claude Code, Codex, and the git vault's
`00-meta/scripts/`) to the same bytes; run `vault_warning_policy.py --certificate` on
that installation (including its harmless replay), then promote again.
Use the same order when returning to published v0.33.1. Version-stamped upgrade
tools do not establish byte equality for a development source. Installed plugins and
vault engine copies were not changed as part of this work.

The existing `.gitattributes` pins Python files and the bound
`skills/agentic-vault/scripts/resources/lint-benign-v1.json` corpus to LF.
The declared source fingerprint assumes those canonical checkout bytes; a locally
modified bound file or different line endings require a different certificate.
No checker logic was changed to force a particular fingerprint.

## Checks on 2026-10-30

These ran on one Windows PC with Python 3.12 and synthetic data only.

- Output comparison with an export of v0.33.1 (`git archive 4b213f3`) on a synthetic
  vault with Hangul, Latin and confusable names: `--help`, `check-support --help`,
  default `check-support`, `--row-references`, `--primary-text-only`,
  `--normalize-periods --fiscal-start 4`, the last three together, `check`, `prepare`
  and three error paths gave byte-identical stdout, stderr and exit codes. The
  `check_support` API gave identical serialized results for every combination of the
  other options, and with `bind_subject` once `binding_observations` and
  `binding_warnings` are set aside; `parse_ledger`, `check_ledger` and the lint
  extensions' `analyze_notes` were identical too. With `--bind-subject`, four rows
  changed from `bound`: three to `subject_absent` (one more Hangul syllable before the
  subject, another word written before it without a space, and a Latin letter before
  `Éclair`) and one to `unassessed` (`source_incomplete`), because its other cited
  source was denied.
- Compared with WordBreakProperty-18.0.0 (ALetter and Hebrew_Letter) over the 142,897
  assigned, non-private code points that NFKC and casefolding leave unchanged in the
  Unicode 15.0.0 database of Python 3.12, the blocking class blocks 9,147 code points
  that the table does not treat as letters (mostly Tangut, Khitan Small Script, Nushu,
  hentaigana and some Tai and Myanmar ranges) and does not block 117 that it does
  (modifier letters, squared or circled Latin letters, and U+119E by design). Python
  versions with other Unicode databases give other counts.

Run from this checkout:

```console
python skills/agentic-vault/scripts/vault_warning_policy.py --certificate
python -m unittest -v tests.test_support_row_diagnostics tests.test_release_metadata
```

The focused unittest command passed 54 tests on 2026-10-30, and the certificate CLI
returned the declared source fingerprint above. The integrated full-suite result is
a separate validation step and is not asserted by this note.
