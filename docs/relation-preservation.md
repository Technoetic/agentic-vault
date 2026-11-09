# Authored record preservation

The optional v0.44.0 record probes bind an author-selected literal anchor to one
body line or paragraph in a selected original and candidate. They observe a
changed quantity assignment, appended negation or removed exception inside that
scope even when the same numbers and names remain elsewhere. This is a scoped
lexical comparison, with no model, natural-language parser or new runtime
dependency. It does not establish truth, semantic equivalence or permission to
apply an edit or quarantine a source.

Omitting `relation_probes`, or passing `None` to the Python APIs, preserves the
existing results and statuses without new keys. An explicitly empty list is an
error. Valid authored record probes supply explicit selected evidence even when
the legacy extractor finds no number, date, name or claim. Only the obsolete
`no_selected_literal_facts` review reason is then removed; all other literal
review reasons and every failed record observation remain in force. Without
probes, an empty legacy fact set still requires review.

## Probe format and matching

Proposal sidecars use this exact JSON object:

```json
{"schema_version":1,"probes":[{"id":"item_a","anchor":"[[Item A]]","scope":"line"},{"id":"policy_exception","anchor":"[[Policy A]]","scope":"paragraph"}]}
```

There must be 1..64 probes. Each unique `id` is 1..64 ASCII letters, digits,
underscores or hyphens, beginning with a letter or digit. An `anchor` is a
nonblank literal of at most 256 Unicode characters, without control, format,
surrogate or line/paragraph separator characters. Its punctuation has no regex
meaning. Unknown fields, duplicate JSON keys, nonfinite numbers, boolean schema
versions and unsupported scopes are rejected.

`line` selects one nonblank LF-delimited body line. `paragraph` selects one
maximal run of nonblank lines, separated from other paragraphs by blank lines
(including whitespace-only lines). Repeated anchor occurrences inside one
record count as one selected record. Exactly one original record must contain
the anchor; zero or multiple original records are invalid authored input. A
missing candidate record or multiple candidate records requires review. A copy
of the original elsewhere therefore cannot hide a changed selected record.

The anchor and body are normalized to Unicode NFC. CRLF and CR become LF.
A leading BOM and frontmatter are excluded, using the existing preservation
check's frontmatter boundary (closing boundary searched in the first 64 lines
after the opening line, including its schema-accepted malformed closing line).
All other record whitespace, indentation,
punctuation, case and compatibility characters are preserved. There is no
NFKC or case folding: `Pa` and `pA`, or `²` and `2`, can differ meaningfully.
Trailing newlines and blank paragraph delimiters are outside a selected record.
Whole record order may change without requiring review.

Paraphrases inside the scope conservatively require review. Conditions outside
the scope are unverified: a `line` probe cannot check an exception on the next
line. Anchors locate authored records; they do not infer entities or discover
unregistered copies. Hashes are integrity observations, not signatures.

## Python APIs and results

With `skills/agentic-vault/scripts` on `sys.path`:

```python
from vault_relation_probes import check_relation_records, RelationProbeError
from vault_compile_quality import check_preservation

original = '[[Item A]] quantity 10.\n[[Item B]] quantity 20.\n'
candidate = '[[Item A]] quantity 20.\n[[Item B]] quantity 10.\n'
probes = [{'id': 'item_a', 'anchor': '[[Item A]]', 'scope': 'line'}]
observed = check_relation_records(original, candidate, probes)
combined = check_preservation(original, candidate, relation_probes=probes)
```

`check_relation_records(original: str, candidate: str, probes: list[dict]) -> dict`
returns `status` (`complete` or `needs_review`), `relation_rows` and
`relation_scope`. It accepts at most 256 KiB of UTF-8 per text and raises
`RelationProbeError` with a safe code on invalid input. `check_preservation` and
`check_compile` translate these errors to their existing `QualityError`.

Each row contains `id`, `scope`, `original_count`, `candidate_count`,
`original_sha256`, `candidate_sha256`, `original_locator`, `candidate_locator`,
`preserved` and `reason`. Locators contain 1-based body-relative `start_line` and
`end_line`. Candidate hashes and locators are null unless exactly one candidate
record was selected. Reasons are null, `record_changed`, `candidate_missing` or
`candidate_ambiguous`. No raw record or anchor is returned in these fields;
existing literal fields in the combined legacy report retain their documented
contents.

When opted in, preservation adds `relation_rows` and `relation_scope`; failed
rows add `relation_record_changed`, `relation_candidate_missing` or
`relation_candidate_ambiguous` to `review_reasons`. Compile adds the same rows
and scope plus `relation_review_reasons`. It retains any existing `review_reason`,
otherwise uses the first relation reason. Either API requires review if any
selected candidate record fails. These observations do not change apply approval.

## Proposal CLI

Write a schema1 sidecar inside the allowed vault, outside receipt/evidence/runtime
directories, then run:

```text
python skills/agentic-vault/scripts/vault_proposals.py --vault DEMO_VAULT preserve-check ID --relation-probes 00-meta/probes.json
```

The explicitly selected `.json` sidecar is limited to 64 KiB and read through
the existing portable path, deny-zone, regular-file, single-link and stable-read
guards. Retrieval exclusions keep the existing explicitly selected ingest
semantics. The command rechecks configuration, receipt integrity and the raw
sidecar digest after analysis. Changed inputs produce an error, with no partial
success. The original and candidate are the receipt's stored, hash-bound texts;
this does not claim that the live target is still current. Nothing is written,
and `apply` still requires its existing explicit approval and live-target checks.
Exit 0 means complete, 1 means review or error (inspect the JSON), and malformed
CLI arguments exit 2, as before.

## Compile CLI

Add an optional `relation_probes` list to the existing stdin request. Each probe
also requires `source_path` and `target_path`, exactly matching the canonical
vault-relative paths of already selected source and target bindings:

```json
{
  "sources": [{"path":"10-inbox/source.md","sha256":"<64 lower-case hex>"}],
  "targets": [{"path":"20-knowledge/summary.md","sha256":"<64 lower-case hex>"}],
  "required_claims": [],
  "relation_probes": [{"id":"item_a","anchor":"[[Item A]]","scope":"line","source_path":"10-inbox/source.md","target_path":"20-knowledge/summary.md"}]
}
```

Pass it to `vault_compile_quality.py --vault DEMO_VAULT`. The path fields only
select from the existing hash-bound roles; they never discover or read another
file. A target cannot stand in for a selected source. Existing per-file, total
read and request budgets remain in force. Every source, target and policy is
rechecked after record analysis. These are point-in-time checks, not an atomic
multi-file snapshot; a change after the final observation remains possible.
Exit codes stay 0 for complete, 1 for review, 2 for invalid or unavailable input.

## Clean-room example

This standalone Python example creates only synthetic temporary files and runs
the compile CLI with real hashes. Run it from the plugin checkout:

```python
import hashlib, json, pathlib, subprocess, sys, tempfile

with tempfile.TemporaryDirectory() as directory:
    vault = pathlib.Path(directory)
    (vault / '00-meta').mkdir()
    (vault / '00-meta/vault-config.json').write_text('{}', encoding='utf-8')
    bindings = []
    for relative, text in [
        ('source.md', '[[Item A]] quantity 10.\n[[Item B]] quantity 20.\n'),
        ('summary.md', '[[Item A]] quantity 20.\n[[Item B]] quantity 10.\n'),
    ]:
        data = text.encode('utf-8')
        (vault / relative).write_bytes(data)
        bindings.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest()})
    request = {'sources': [bindings[0]], 'targets': [bindings[1]], 'required_claims': [],
               'relation_probes': [{'id': 'item_a', 'anchor': '[[Item A]]', 'scope': 'line',
                                    'source_path': 'source.md', 'target_path': 'summary.md'}]}
    result = subprocess.run([sys.executable, 'skills/agentic-vault/scripts/vault_compile_quality.py',
                             '--vault', str(vault)], input=json.dumps(request), text=True,
                            capture_output=True, encoding='utf-8')
    print(result.stdout)
    assert result.returncode == 1  # authored record requires review
```

Focused synthetic regressions live in `tests/test_relation_probes.py` and
`tests/test_stateful_relation_probes.py`. The latter uses the existing optional
Hypothesis development dependency and explicitly fails its direct command when
that dependency is absent. Generated edits cover reorder, quantity swaps,
negation, exception removal, missing/duplicate records, restoration, NFC,
CRLF and frontmatter decoys. They measure these scoped behaviors, not general
semantic preservation or live model performance.

## Paired offline fixture audit

`python scripts/evaluate_relation_preservation.py` runs both the legacy literal
observation and the opt-in record observation on the same repository-authored
original/candidate pairs. The expected selected-record labels are written in
`tests/fixtures/relation_preservation/pack.json`; they are not inferred from
either checker. The output binds the fixture and the three code files, reports
per-case paired decisions and confusion counts, and does not repeat raw source
records. This is a small synthetic regression audit, not model accuracy,
business truth, a token-budget optimizer or evidence about arbitrary prose.

The deliberately weaker control is executable:

```text
python scripts/evaluate_relation_preservation.py --record-arm literal-control
```

It substitutes the literal-only observation for the record arm and exits1 on
the bundled change cases. A missing case or invalid source/fixture is an error,
not a safe result. To inspect a different bounded inline synthetic pack, pass
`--pack PATH.json`; this reads only that selected pack and writes nothing.
