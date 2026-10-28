# Synthetic state and dependency revision evaluation

`scripts/evaluate_state_revisions.py` is a standalone, offline Python 3.10+
standard-library tool. Its independently authored synthetic pack has four cases
and nine snapshots: a direct update, an unchanged unit price beside a quantity
update, two successive updates with chained arithmetic dependencies, and a total
that must be recalculated after its quantity changes. It uses no user vault,
network, model provider, shell execution, or upstream dataset.

Run from the repository root:

```console
python scripts/evaluate_state_revisions.py
python scripts/evaluate_state_revisions.py --control stale
python scripts/evaluate_state_revisions.py --control mixed
python scripts/evaluate_state_revisions.py --control wrong
python scripts/evaluate_state_revisions.py --write-observations state-observations.json
python scripts/evaluate_state_revisions.py --observations state-observations.json
python -m unittest discover -s tests -p test_state_revision_evaluation.py -v
```

Export requires a **new** local file; it never overwrites an existing file. Use
the same `--control` setting when replaying an export because it is bound as
part of the evaluator configuration. Exit 0 means execution succeeded, including
intentionally wrong synthetic controls; 1 means one or more observations failed
validation/execution; 2 means invalid or unsafe top-level input/output. These
codes do not certify model quality. CLI errors do not echo file contents, paths,
or callback exception messages.

## Public interfaces and fixture

`load_pack(fixture=DEFAULT_FIXTURE)` accepts a pack JSON **file**, validates it,
and returns its dictionary. `evaluate(fixture=DEFAULT_FIXTURE, *, control="good",
answerer=None, observations=None)` returns the report. `main(argv=None)` returns
the CLI exit code. `answerer` and `observations` are mutually exclusive. The CLI
always uses the bundled fixture; Python callers can supply independently authored
synthetic packs with the same strict schema.

A pack has exactly `schema_version: 1`, `synthetic: true`, `cases`, and `labels`.
Each public case has `id`, `question`, `initial`, `revisions`, `rules`, `requested`,
and `snapshots`. Revisions are ordered `{"set": {"fact": "decimal"}}` updates
to already declared direct facts. Rules have `target`, `op`, and two `inputs`;
supported operations are `add`, `subtract`, and `multiply`. Rules are evaluated
in declaration order and can only refer to previously declared direct facts or
rule targets, preventing cycles. All direct facts and derived targets must be
requested. Each snapshot has an `id` and an integer `after_revision`; snapshots
must start at zero and end at the final revision, with increasing positions.

Values are plain decimal **strings**, never JSON numbers, expressions, exponent
notation, booleans, or executable rules. Absolute magnitude is at most `1e12`,
with at most 12 fractional digits. Arithmetic uses a local 64-digit Decimal
context and rejects results outside those bounds; it never rounds an answer to
fit. Numerically equal spellings such as `12.50` and `12.5` compare equally.
There is no division, unit conversion, or implicit dependency discovery.

Private `labels[case_id][snapshot_id]` holds a complete `current` mapping and a
`superseded` list of distinct earlier complete mappings, excluding mappings equal
to current. The loader checks these labels against every declared intermediate
state. Labels and future revisions are excluded from the answerer payload:

```json
{
  "case_id": "derived",
  "snapshot": {"id": "pre", "after_revision": 0},
  "question": "Give quantity, unit price and the recomputed total at this snapshot.",
  "requested": ["quantity", "unit_price", "total"],
  "evidence": {
    "initial": {"quantity": "2", "unit_price": "12.5"},
    "revisions": [],
    "rules": [{"target": "total", "op": "multiply", "inputs": ["quantity", "unit_price"]}]
  }
}
```

Callbacks receive a separate deep copy for each snapshot. A response is exactly
`{"status":"answer","values":{"quantity":"2","unit_price":"12.5","total":"25"}}`,
or `{"status":"abstain","values":{}}`. Answer mappings must include exactly
the requested facts. Free prose, partial answers and extra facts are errors.

## Controls and grading

The named deterministic controls parse public evidence without consulting labels.
`good` applies every visible update and recomputes rules. `stale` uses the previous
revision state. `mixed` combines current direct facts with previous derived
values. `wrong` adds one to every current value. Earlier snapshots are evaluated
separately; simply copying the final answer fails earlier snapshots. At an initial
snapshot, stale and mixed controls can be current because no earlier update exists.

The report contains `per_snapshot` rows and explicit counts:

| Classification | Meaning |
| --- | --- |
| `current` | Every value equals the snapshot's current label. |
| `superseded` | The entire answer equals a distinct earlier complete state. |
| `mixed_dependency_stale` | Each individual value belongs to a current/prior state, but the combination equals no complete state. |
| `other` | At least one value lies outside the visible labelled state history. |
| `unassessed` | Abstention or invalid/failed observation; correctness remains null. |

`dependency_error_count` independently counts rules whose returned target does
not equal arithmetic on the returned inputs. A complete old state can be
internally consistent yet superseded. For the derived post-update snapshot,
quantity `5`, unit price `12.5`, and total `25` is mixed/dependency-stale and has
one dependency error. `dependency_assessed_snapshot_count` reports the population
of valid answered snapshots, including snapshots with no declared rules; errors
and abstentions do not enter it. Execution failures also have a separate count.

The bundled results are deterministic: good has nine current snapshots; stale
has four current and five superseded; mixed has six current, three mixed, and
three dependency errors; wrong has nine other snapshots. These are checks of
the scoring machinery, not measurements of a model.

## Explicit replay and trust boundary

Observations are an exact mapping from `case_id/snapshot_id` to an object with
`binding`, `response`, `origin`, `model`, `usage`, and `actual_cost`. Missing or
extra snapshot IDs reject the replay. A malformed response or changed binding
makes that row an execution error and leaves it unassessed. Bindings contain:

- Schema version, case ID, and snapshot ID.
- SHA-256 of the entire canonical validated fixture, including private labels.
- Separate SHA-256 of the question and of the visible evidence, requested facts
  and snapshot position.
- SHA-256 of the evaluator configuration, including grader version, arithmetic
  bounds and control setting.

Hashes bind data to this evaluation; they are not signatures or proof that an
external model produced a response. Origins are `synthetic_control`,
`external_observation`, or `actual_model`. Only `actual_model`, with an explicit
model name, may carry token `usage` and `actual_cost`. Usage is bounded integer
`input_tokens`/`output_tokens`; cost is a finite bounded `amount` and three-letter
uppercase `currency`. External provenance and billing are caller-attested.
Every report has `model_request_count: 0`; aggregate cost stays null, with any
attested costs retained per observation. Aggregate usage exists only when every
snapshot supplied usage. No missing usage, billing or model result is invented.

JSON reads are limited to 1 MiB, 24 nesting levels, bounded collections, strings
and node count. Duplicate keys, nonfinite numbers, boolean numeric fields,
unpaired Unicode surrogates and unknown schema fields are rejected. Local file
reads reject nonregular files, hardlinks, symlinks, reparse points and linked
parents, and check file identity/metadata before and after the bounded read.
Before any filesystem inspection or open, path checks reject colon-bearing
components (including file/directory NTFS alternate streams), reserved Windows
device names, invalid characters and trailing dots/spaces. Only an absolute
local drive anchor may contain a drive colon; drive-relative paths are rejected.
This is a conservative local-file check, not an operating-system sandbox against
an adversary racing parent-directory changes. Canonical hashes ignore JSON
whitespace and key order, while any fixture data change requires a fresh replay.

This evaluation recognizes only structured values and declared dependencies.
It does not infer relationships from prose, retrieve evidence, test real-world
truth, or establish memory robustness, general model effectiveness or free-form
answer quality. Existing runtime code and evaluations are unchanged.
