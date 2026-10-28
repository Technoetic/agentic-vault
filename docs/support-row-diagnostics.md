# Support-row references and subject diagnostics

`vault_ssot.py check-support` offers two independent, opt-in diagnostics:

```sh
python skills/agentic-vault/scripts/vault_ssot.py --vault /path/to/vault check-support --path notes/facts.md --row-references --bind-subject
```

The Python interface is `check_support(vault, path, row_references=False,
bind_subject=False, ...)`. Both options require actual booleans. With both off,
the existing dictionary keys and serialized output stay unchanged. With either
on, existing `warnings`, literal counts, periods and support decisions still stay
unchanged. These options never edit notes or provide fact verification.

## Complete parsed-row references

`--row-references` adds `row_references`, one entry per successfully parsed fact
row, including supported, unsupported, unresolved and skipped rows. Invalid rows
that the existing parser diagnoses rather than parses are not new fact rows.
Each entry contains only `path`, original ledger `line`, `status`, a fixed
`reason`, and `via` paths whose compared body contains the literal value.
Unsupported/unresolved/skipped entries can have an empty `via`. A mixed citation
that supports the value and also fails retains its existing unresolved warning.
No subjects, values, relations, source excerpts or citation text are copied into
the new fields.

The accompanying `audit_population` is a generic population object with
`population_id`, `source_sha256`, and `items` containing stable `id`, `path` and
`line` references. Extract this object into a JSON file to use the existing audit
tool without a reader change:

```sh
python scripts/real_vault_audit.py draw --population rows.json --k 10 --out sample.json
```

The population covers all parsed rows rather than only flagged rows. An empty
ledger exports an empty population; the audit tool's existing empty-population
refusal still applies. The source digest binds the ledger revision, while the
existing `cited_notes` records source revisions. It is a point-in-time observation,
not an atomic filesystem snapshot or a durable proof of truth.

## Physical-line subject/value observations

`--bind-subject` adds `binding_observations` for every parsed row and a separate
`binding_warnings` list. It never adds a warning to the original `warnings` list.
Each observation contains only the ledger `path`/`line`, `status`, a fixed
`reason`, and `via` references to original physical source lines:

| Status | Meaning |
| --- | --- |
| `bound` | The subject and supported value occur on at least one eligible physical source line (`same_physical_line`). |
| `subject_absent` | At least one eligible line has the value, no such line has the subject, and no relevant source/reference was left unassessed (`subject_not_on_value_line`). Adds `ssot-subject-unbound` to `binding_warnings`. |
| `unassessed` | No literal support, an empty subject, incomplete/failed sources, or no eligible physical value line. This is never a subject-absence finding. |

Comparison reuses NFKC, casefold, whitespace normalization, wiki/code/bold markup
rendering, and the existing literal boundary rules. An explicit wiki display
alias in the subject or source line is accepted. Frontmatter aliases, filenames,
organization metadata and inferred entity synonyms do not create aliases.
Subject and value may be in different cells of the same ordinary table row.
Co-occurrence is only a lexical diagnostic: a sentence that names two companies
can still pass even if the value belongs to the other company. It does not prove
semantic attribution, contradiction, authority or truth.

Frontmatter and recognized fact tables are excluded from binding, including the
ledger's own fact tables on self-citation. The source's original CR/LF physical
lines are examined separately; cutting a fact table never joins adjacent text.
BOM and CRLF input retain original line numbers. A value supported only by a fact
table or by text spanning lines remains `unassessed` for binding. This stricter
diagnostic applies with or without `--primary-text-only`; that flag keeps its
original literal-support meaning.

Empty or markup-only/anchor-only subjects, unresolved/policy-denied/unreadable/oversize sources, reference
limits, parse limits, malformed fact tables and unclosed leading frontmatter do
not justify a subject-absence warning. A positive eligible same-line match can
still be reported when another cited source fails. All allowed source lines are
scanned, but at most 32 line references are returned per binding observation;
`via` is representative evidence and is not an exhaustive line inventory.

## Limits, stability and certificate migration

Existing ledger, citation, inventory, note-count and byte budgets remain in
force. No extra source files are opened for binding. Only when requested, the
diagnostic retains raw text from the same permitted stable read already used by
literal support; it parses eligible lines from that cached revision. Existing
final ledger, cited-source and policy-generation checks still refuse stale runs.
There is no network, model, dependency installation or automatic note rewrite.

Changing `vault_ssot.py` changes the warning-policy `checker_sha256` even with
these flags off. Existing promotion certificates cannot be reused across this
engine change. Follow the existing coordinated migration: demote promoted fatal
warnings to `warning`, update every checking engine copy together (host plugin
copies and vault `00-meta/scripts/`), run the harmless replay on those copies,
then regenerate the certificate with `vault_warning_policy.py --certificate`
before re-promoting. Rollback also requires matching engine/certificate versions.
This implementation does not update installed engines or certificates.

Focused validation uses synthetic temporary vaults:

```sh
python -m unittest tests.test_support_row_diagnostics -v
```

This checks the CLI/audit draw round trip, default serialization preservation,
all parsed-row states, wrong-subject sources, aliases and numeric boundaries,
frontmatter/table/line exclusions, source budgets/refusals and stale-read checks.
It is not a real-vault coverage or model-effectiveness measurement.
