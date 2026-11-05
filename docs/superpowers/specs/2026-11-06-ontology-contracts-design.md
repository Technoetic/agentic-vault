# Optional source-bound ontology contracts

Authorized by the user's 2026-11-06 request to implement and publish the
NVIDIA-inspired proposal. Base: v0.40.1 / c485fe1. Target: v0.41.0.

## Outcome and limits

Add an explicit, standard-library-only definitions sidecar, typed links,
structured query preflight, definition-change detection, and a narrow read-only
CLI. Markdown remains the source. No NLP extraction, semantic truth engine,
model/network/database calls, default hook/config changes, automatic merges,
active-installation update, or company-vault conversion. The CLI is not an MCP
server. Host review labels and hashes authenticate neither truth nor authority.

## Declarations v1

Exact root keys: `schema_version` (integer1), `ontology_id`, `version`, `types`,
`entities`, `relations`, `links`, `metrics`. IDs are lowercase ASCII strings
matching `[a-z][a-z0-9_.:-]{0,95}`. `types` is a nonempty unique list of IDs;
these are host-declared schema categories. All record IDs across the four other
collections are globally unique. At most128 records total, at most32 types.
No unknown keys, duplicate JSON keys, nonfinite numbers, excessive depth/counts.
Use existing64KiB JSON limits and bounded primitives before serialization.

Every record includes `review` (`candidate` or `reviewed`) and `sources`, a
nonempty list of at most4 exact `{path,line,quote,expected_sha256?}` proofs.
Labels/definitions are host-authored annotations bound to evidence, not extracted
or certified by the helper. `candidate` records are never usable query context.

- Entity exact fields: `id,type,label,aliases,review,sources`.
- Relation: `id,label,domain,range,review,sources` (domain/range reference types).
- Link: `id,subject,predicate,object,review,sources`. References must exist and
  entity types must match the relation direction. Duplicate triples are invalid.
- Metric: `id,label,aliases,definition,unit,grain,population,aggregation,review,sources`.
  These four dimension values are nonempty literal strings, not executable code.

Entity/metric labels and aliases may overlap: report ambiguity, never merge.
Search compares NFC+casefold exact labels/aliases/IDs; no fuzzy identity guesses.
Global IDs and literal data are preserved without normalizing source quotes.

## C1/C3 core: vault_ontology.py

Public `capture_registry(vault: Path, declaration_path: str) -> dict` loads an
explicit safe JSON file. Reuse `_Policy`, `_proof`, strict evidence encode/decode
and stable reads; additionally reject `00-meta/.agentic-vault/runtime` paths
before reading both JSON and Markdown. The engine-runtime guard is unconditional.
Apply deny/exclude/.git/link/hardlink and configured revocation policies before
source data can enter output. Prove exact LF-delimited quotes (CRLF is literal),
retain full raw file binding and SHA256 of literal UTF8 quote bytes.

Successful capture exact fields: `type='ontology_registry',schema_version=1,
authority='data_only',ontology_id,version,types,entities,relations,links,metrics,
declaration={path,sha256,size},config_sha256,registry_sha256`.
Record sources are `_proof` output plus `span_sha256`. Registry digest is SHA256
of canonical existing `encode` of all fields except `registry_sha256`. Check
encoded output budget before success. The helper prints; it does not write files.

Public `check_registry(vault: Path, snapshot: dict) -> dict` returns a check report.
Strictly validate snapshot shape/digest; reread captured declarations under current
policy and recompile sources, rather than trusting self-authored snapshot fields.
Compare declaration/config/current compiled contents. An unchanged version label
does not hide definition edits. Any failed/unselected declaration invalidates the
entire registry: no silent trimming or usable partial context. Altered source,
config, declarations, revoked proof, missing or blocked input fails closed.

Internal `_with_registry(vault, snapshot, builder) -> dict` is the integration API:
validate current snapshot using one policy session, call trusted internal
`builder(registry)`, then recheck all source/config/declaration bindings before
return. Builder returns a bounded JSON result; errors produce
`unavailable(reason)` with no registry/context/matches. Public helper
`unavailable(reason)` has stable data-only report metadata, `status='unavailable'`,
`reason`, `context=[]`, `matches=[]`, `gaps=[]`. Include no failed source text.

`check_registry` uses `_with_registry` with a current-status builder. Reports
include current registry digest; status is `current` only for policy-current
definitions, never a claim of semantic truth or available answer data.

## C2/C4 readers: vault_ontology_query.py

Functions `search_terms(vault,snapshot,text)`, `get_term(vault,snapshot,term_id)`,
`check_answerable(vault,snapshot,request)` use `_with_registry` and never write.
Candidate records and links whose dependency closure is not reviewed cannot be
used. Validate the complete request before considering a result.

Search returns reviewed exact matches (entity/metric), status `matched`,
`ambiguous` or `not_found`; ambiguity returns no usable `context`. Get by global
ID supports entities, metrics, relations and links; candidate or dependency
candidate returns `needs_review`, no context. Returned source bindings describe
annotations; no external approval capability is created.

Request exact keys: `schema_version` (integer1), `entities`, `relations`, `metrics`.
Entities: unique explicit entity IDs. Relations: unique exact objects
`{subject,predicate,object}`. Metrics: unique objects `{id,slots}` with slot keys
subset of `unit,grain,population,aggregation,period`. Each metric needs all five.
The four literal dimensions must match its definition exactly; period is exact
`{start,end}` canonical YYYY-MM-DD, start<end, half-open. Unknown keys and invalid
shapes are errors; absent required slots, unknown IDs, unsupported links and
unreviewed dependencies yield gaps. Empty plan yields a gap. At most32 per list.
This is a host-authored structured plan, not natural-language interpretation.

Only a complete reviewed definition closure and valid slots returns
`status='ready_for_evidence'`, with selected `context` and no gaps. Any gap yields
`status='needs_clarification'`, `context=[]`. Never emit a coverage percentage,
`answerable=true`, actual values, SQL, correctness probability, or query results.
Having a date/term does not establish underlying data exists or is correct.

## CLI: vault_ontology_cli.py

`--vault PATH capture --input RELATIVE.json`, `check --registry RELATIVE.json`,
`search-terms --registry ... --text ...`, `get-term --registry ... --id ...`,
`check-answerable --registry ... --input RELATIVE.json`.
Safely stable-read and recheck every JSON input, including multiple inputs;
engine-runtime/deny/exclude/link guards apply. Strict duplicate-key decoder.
Output ASCII escaped canonical JSON with LF. Exit0 for successful capture/current/
matched/ready;1 for ambiguity/missing/review/clarification;2 for unavailable/invalid.
Failed final input/source rechecks clear all usable result content. No write,
delete, promotion, model configuration or arbitrary command/SQL operations.

## Verification and publication

TDD with canonical resolved temp roots, exact positive/negative source proofs,
revocation, stale definitions/config/source, candidate closure, alias ambiguity,
reversed/type-invalid relations, missing/conflicting dimensions, period bounds,
duplicate IDs/keys, file/count/output budgets, input races and no-write CLI.
Reuse existing source policy; do not edit certificate-bound checker files.
Document NVIDIA pinned inspiration and explicit differences from current AV.
Run focused/full regression, installer/package validation, independent review,
master+tag CI and reproducible immutable release checks. Update Reference Atlas
with pinned public citation evidence and independent verification after release.
