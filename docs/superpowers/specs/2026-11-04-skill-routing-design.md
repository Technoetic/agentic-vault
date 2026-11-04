# Optional skill-routing evaluation

The user authorized implementation and public release of the proposed Skill Reach
absorption. This is a checkout-only, Python 3.10+ standard-library evaluator.
No model, subprocess, network, installation, hook, permission, or SKILL description
changes are performed by evaluation. Upstream code/datasets are not copied.

## Deliverables

1. Strict multi-skill evaluation of caller-supplied ordered observations.
2. A bilingual, human-authored synthetic case suite and clearly labeled controls.
3. Explicit manifest catalog collection and advisory vocabulary overlap.
4. Source-bound JSON reports and compatible before/after regression comparisons.
5. Documentation, tests, CI smoke, and a normal public release.

## Shared contracts (schema_version 1)

Catalog: `kind: skill_routing_catalog`, `source_kind: synthetic|manifest_scan`,
`skills: [{id, name, description, source_sha256, source_path}]`.
IDs are namespaced ASCII `owner:name`, each component lowercase alphanumeric
with hyphens/underscores. source_path is a relative POSIX locator, never a path
to execute. A catalog has 1..256 unique IDs; duplicate names across owners are
permitted and reported as ambiguity, duplicate owned IDs are invalid.

Suite: `kind: skill_routing_suite`, `suite_id`, `cases: [{id, prompt, context,
expect: {required, allowed, forbidden, before, entrypoints}}]`.
Context is a bounded JSON mapping (fixtures use in_vault, jev_first, request_mode).
The three skill lists are disjoint; all referenced IDs must exist in catalog.
`before` contains distinct acyclic [earlier,later] pairs within required+allowed.
Every later invocation must have an earlier invocation preceding it. Empty
required permits true non-trigger cases; unexpected invocations still fail.
An empty entrypoints list imposes no entrypoint condition. Otherwise the first
non-neutral (not in allowed) invocation must belong to entrypoints. Entry points
must be required. Cases: 1..1000, prompt <=4096 characters; IDs unique.

Trace: `kind: skill_routing_trace`, `suite_sha256`, `catalog_sha256`,
`source_kind: scripted_control|host_observation`,
`observation_kind: skill_invocation|skill_file_read|self_report`,
`runtime: {host, version, model}`, `observations: [{case_id, status, invoked}]`.
status is complete|unavailable|error; noncomplete rows must have no invoked
items. Invoked is an ordered list, at most128 IDs. IDs outside catalog are
reported as unexpected, not silently discarded. Unknown/duplicate case IDs are
invalid; missing observations are unmeasured. Complete empty observations can
establish a non-trigger pass. A self-report is never scored as invocation.
Default evidence kind is skill_invocation; skill_file_read requires explicit
selection and must remain labeled file reads. Caller attestations/hashes are
not independent proof that host events occurred or that a skill succeeded.

Canonical SHA256 = UTF-8 json.dumps(sort_keys=True, ensure_ascii=False,
separators=(',', ':'), allow_nan=False), without an added newline. Trace binding
must match full catalog/suite canonical digests. Raw input hashes are recorded
separately by CLI. Reject duplicate keys, nonfinite values, oversize input,
symlink inputs/root traversal, unknown schema fields, malformed IDs/digests,
conflicting expectations and cyclic before constraints. Max input1MiB; bounded
context and nesting. Do not include full prompts/context in default reports.

## Module interfaces

`scripts/skill_routing_contract.py` owns RoutingError(code), read_document(path),
digest_document(value), validate_catalog(value), validate_suite(value,catalog),
validate_trace(value,suite,catalog), evaluate(catalog,suite,trace,
observation_kind='skill_invocation'). Validation returns independent values.

Evaluation report: kind skill_routing_evaluation, schema_version1, suite_id,
suite_sha256, catalog_sha256, catalog_skill_ids, source_kind, observation_kind,
runtime, summary {cases,scored,unmeasured,passed,failed}, cases [{id,status,
observed,missing_required,forbidden,unexpected,order_violations,
entrypoint_violations,reason_codes}]. Status pass|fail|unmeasured. Include
`model_run_performed: false`; evaluator never runs models. Synthetic controls
remain controls even when all pass. Lists are stable; repeat valid calls are
reported but not automatically penalized. No invocation is business success.

`scripts/skill_routing_catalog.py`: build_catalog(roots) where roots is a list
of (owner,Path) pairs, returns catalog; analyze_catalog(catalog) returns advisory
same-name/description and vocabulary-overlap findings (not model evidence).
Scan only explicitly supplied SKILL.md roots; bounded strict frontmatter subset,
no PyYAML, no following symlinks, stable relative paths and raw manifest digests.
Reject unsupported manifest syntax rather than misread it. Support repository's
quoted one-line descriptions including YAML single-quote escaping.

`scripts/evaluate_skill_routing.py` CLI:
* catalog --root owner=PATH (repeat) [--output NEWFILE]
* evaluate --catalog JSON --cases JSON --trace JSON
  [--observation-kind skill_invocation|skill_file_read] [--output NEWFILE]
* compare --before JSON --after JSON [--output NEWFILE]
No argument errors print input contents. New output files cannot overwrite.
Evaluation exit0 only if no failed/unmeasured cases; exit1 measured failures;
exit2 malformed/missing/incompatible evidence. Emit JSON on stdout by default.
Reports include evaluator_sha256 over the source modules, raw input digests,
and report_sha256 over all fields except report_sha256 itself. Compare validates
report integrity and shapes, and requires same suite, evaluator, catalog IDs,
trace kind, observation kind and runtime. Catalog description hashes may differ.
Report improved/regressed/unchanged/unmeasured case IDs; no statistical/model
accuracy claim. Missing measurements cannot count as improvements.

## Fixtures and integration

`tests/fixtures/skill_routing/`: synthetic catalog, original KR/EN cases,
baseline/control traces. At least24 cases covering negative, ambiguous,
non-vault/vault, read-only/implementation, authorized Jev context, composed
skills, forbidden skills and order. Include intentionally faulty controls,
never fabricate actual host trace provenance. Fixture trace digests are computed
from the frozen suite/catalog with the shared canonical algorithm.

Default CI adds a fast scripted control smoke/check, no live API job. Unit and
existing full platform matrices stay intact. Existing host installation/native
discovery checks are unchanged. The developer tool is not a newly installed
skill or slash-command; no new default discovery ambiguity is introduced.

## Verification / publication

TDD for runtime and CLI boundaries; independently verify malformed/unmeasured
evidence cannot produce a green result. Run the complete existing suite and
platform CI. New reviewer checks code and fixture semantics, then normal
fast-forward integration, versioned signed/provenance assets and public GitHub
release follow existing release-integrity procedure. No NS vault network push.

Upstream inspiration: google/skill-reach6cc80dd9f7fc3771f088b3ae5941651db5c11938.
Its single expected target and heuristic auto-apply are deliberately not copied.
