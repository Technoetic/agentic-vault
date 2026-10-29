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
rendering, and the existing literal boundary rules; a subject that starts with a
non-ASCII letter also needs the [subject left edge](#subject-left-edge) below.
Values keep the existing rules only. An explicit wiki display
alias in the subject or source line is accepted. Frontmatter aliases, filenames,
organization metadata and inferred entity synonyms do not create aliases.
Before building match patterns, a rendered and normalized subject target or
explicit display alias must contain a Unicode letter or number (`isalnum`).
Empty, anchor-only, marker-only and other symbol-only subjects are conservatively
`unassessed` with `subject_missing` and empty `via`, regardless of source formatting.
When an explicit alias supplies substantive text, marker-only target candidates
are still excluded from matching. This bounded lexical rule may leave a real
symbol-only entity unassessed; it does not declare such entities invalid.
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

### Subject left edge

The existing literal edges apply only where the first or last character of a
literal is an ASCII letter or digit. For `--bind-subject` subjects only, a
normalized subject form whose first character is a non-ASCII letter
(`str.isalpha`) also needs a left edge: it does not match where another letter
comes right before it. On the line `이가나다전기 매출 12억` the subject `가나다전기`
is absent, and so is `Éclair` in `xÉclair`. The subject may follow the start of
the line, a character that is not a word character (a space, punctuation or a
symbol), a decimal digit, `_`, a kana or Han character, a character in the
representative Thai, Lao, Myanmar or Khmer ranges, or U+119E. Right after
one of the five legal forms of the Korean Commercial Act written without a space
(`주식회사`, `유한회사`, `합명회사`, `합자회사`, `유한책임회사`) is an edge too;
`(주)` and `㈜` (NFKC `(주)`) end in `)`. Values, default support and
`--row-references` keep the existing rules, so `(주)이가나다전기` still supports
the value `가나다전기`.

The rule approximates UAX #29 WB5 (no break between two letters) with a
one-character lookbehind. It does not use the Word_Break table and does not apply
WB4, WB6/7 or WB13a/b, so a boundary is assumed after a middle dot (U+00B7), after
an underscore and after a format character such as U+00AD. It does not claim
UAX #29 conformance. A letter here is a word character (`\w`) that is not a
decimal digit (`\d`) or `_` in the running Python's Unicode database, so it
includes a few numeric characters that are not decimal digits, and the blocked set
can shift slightly between Python versions. It also differs from the Word_Break table for
some scripts: it blocks, for example, Tangut, Khitan Small Script, Nushu and
hentaigana, and does not block some modifier letters. U+119E is deliberately not
blocked, a Korean tailoring of its default ALetter value: it is the NFKC form of
U+318D (`ㆍ`), which Korean text writes in place of a middle dot, so
`XYㆍ가나다전기` keeps an edge like `XY·가나다전기`. The
[subject boundary note](verification/subject-boundary-20261030.md) records how far
the class is from the Word_Break table.

Kept limits:

- A subject that starts with an ASCII letter or digit keeps the ASCII edge. One
  Hangul syllable before it still matches (`이XY전기` for `XY전기`), while a
  Hangul subject right after an ASCII letter does not (`XY가나다전기` for
  `가나다전기`).
- The right edge stays open, because Korean particles follow a name without a
  space (`가나다전기는`).
- Apart from the five legal forms, only the one character before the subject is
  examined. A subject that starts with a kana or Han character is blocked after a
  letter too, where UAX #29 would break.
- Any other word written before the subject without a space, such as a legal form
  outside the five or `고객사가나다전기`, blocks it and can add a `subject_absent`
  warning for a real mention.

Compared with v0.33.1 this changes only `--bind-subject` results. A row whose
same-line subject matches are all blocked by this edge is now `subject_absent`, adding
`ssot-subject-unbound` to `binding_warnings`, or `unassessed` with
`source_incomplete` when a cited source was left unassessed. Default output,
`warnings`, counts and exit codes are unchanged.

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
The [subject left edge](#subject-left-edge) changed `vault_ssot.py` again after
v0.33.1, so that development source has its own `checker_sha256`; the
[subject boundary note](verification/subject-boundary-20261030.md) states it with
the same coordination.

Focused validation uses synthetic temporary vaults:

```sh
python -m unittest tests.test_support_row_diagnostics -v
```

This checks the CLI/audit draw round trip, default serialization preservation,
all parsed-row states, wrong-subject sources, aliases and numeric boundaries,
the subject left edge with its kept limits, default support of a value inside a
longer name, frontmatter/table/line exclusions, source budgets/refusals and
stale-read checks.
It is not a real-vault coverage or model-effectiveness measurement.
