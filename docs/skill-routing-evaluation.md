# Optional skill-routing evaluation

This checkout-only developer tool grades caller-supplied, ordered skill observations
against explicit expectations. It can check required composition, forbidden calls,
neutral helpers, invocation order and entrypoints. It does not run a model or prove
that a selected skill completed the user's task.

The bundled controls are synthetic grader checks. **Actual host routing has not
been measured by these controls.** In particular, there is no live Codex observation
adapter in this release. Neither a fully passing control nor its hashes establish
that Codex, Claude Code or another host selected these skills.

The evaluator uses Python 3.10+ and the standard library. It performs no network,
model or subprocess calls, installs nothing, adds no hooks, changes no permissions
or skill descriptions, and executes no skill bodies. It is not an installed skill,
slash command or automatic routing optimizer. Existing host discovery checks remain
separate.

## Replay the frozen controls

Run commands from the repository checkout. All output paths below must be new files;
the tool refuses to overwrite an existing output. They create local JSON reports
in the checkout. Use an absolute path into a separate existing output directory
to keep reports outside the checkout; parent-traversal paths are rejected.
Omitting `--output` prints JSON to standard output.

```text
python scripts/evaluate_skill_routing.py evaluate --catalog tests/fixtures/skill_routing/catalog.json --cases tests/fixtures/skill_routing/cases.json --trace tests/fixtures/skill_routing/control-pass.json --output routing-after.json
python scripts/evaluate_skill_routing.py evaluate --catalog tests/fixtures/skill_routing/catalog.json --cases tests/fixtures/skill_routing/cases.json --trace tests/fixtures/skill_routing/control-fail.json --output routing-before.json
python scripts/evaluate_skill_routing.py compare --before routing-before.json --after routing-after.json --output routing-comparison.json
```

The passing control is expected to exit `0`: 32 passed, 0 failed, 0 unmeasured.
The intentionally faulty control is expected to exit `2`: 15 passed, 14 failed,
3 unmeasured. An unmeasured case makes the evidence incomplete even when other
cases have measured failures. A complete, valid trace with measured failures and
no missing measurements exits `1`; malformed or incompatible input exits `2`.
These are expected fixture outcomes, not a measured model accuracy rate.

The comparison illustrates how the grader classifies changes between scripted
controls. It is not evidence of a routing improvement. Cases absent or unavailable
in the faulty control remain unmeasured in the comparison; filling a gap cannot
be counted as an improvement. This comparison also exits `2` because those three
cases remain unmeasured. A comparison uses the same evaluator source, suite,
owned skill IDs, observation kind, source kind and runtime labels on both sides.

The focused fixture check is:

```text
python -m unittest discover -s tests -p test_skill_routing_fixtures.py -v
```

## Collect a catalog explicitly

`catalog` scans only roots the caller provides. The namespace before `=` identifies
the owner, not a directory to execute. Use one root per selected skill collection;
repeat `--root` to include another owner.

```text
python scripts/evaluate_skill_routing.py catalog --root av=skills --output routing-catalog.json --analysis-output routing-catalog-analysis.json
```

The catalog file is reusable input to `evaluate`. The optional analysis file
contains advisory same-name, same-description and vocabulary-overlap findings.
An overlap finding does not predict a model's routing or automatically rewrite a
description. Distinct owners may use the same skill name; identities remain the
full `owner:name` IDs. Duplicate owned IDs are invalid.

A collected catalog has `source_kind: manifest_scan`, relative POSIX source
locators and raw manifest SHA-256 values. The scanner does not follow symlinks or
execute files. It accepts a bounded frontmatter subset and rejects unsupported
syntax rather than guessing its meaning. It does not search the machine for
installed skills, read unrelated vault content or initialize a vault.

The frozen control traces bind the synthetic catalog. They cannot be replayed
against a newly scanned catalog: supply a suite and observations bound to that
catalog instead. The scanner's report is inventory evidence, not host invocation
evidence.

## Evidence and grading

All documents use `schema_version: 1`. The four fixture files are under
`tests/fixtures/skill_routing/`:

| File | Meaning |
|---|---|
| `catalog.json` | Frozen, explicitly synthetic descriptions for eight owned IDs |
| `cases.json` | 32 original KR/EN requests, bounded context and declared expectations |
| `control-pass.json` | Scripted observations satisfying every case |
| `control-fail.json` | Deliberate routing mistakes, one unavailable row, one missing row and one error row |

The cases were individually written for this repository with AI assistance. They
are not copied from upstream prompts, real company records, credentials or a
third-party dataset. Their labels define a small synthetic routing policy; they
do not replace project permissions or demonstrate human-authored provenance.

The synthetic catalog uses the following IDs:

| ID | Role in these cases |
|---|---|
| `av:agentic-vault` | Vault operations and explicitly authorized direct or Jev-first questions |
| `av:webapp-principles` | Actual webapp implementation and review, including outside a vault |
| `harness14:webapp` | Explicitly selected workflow entry or status |
| `helper:orientation` | Optional neutral helper |
| `helper:implementation` | Requested code changes after applicable app guidance |
| `helper:review` | Requested read-only review after applicable app guidance |
| `decoy:webapp` | Explicit greeting-only namesake, exercising owned-ID ambiguity |
| `decoy:publish` | Deployment decoy, forbidden throughout this suite |

Synthetic `source_path` values are logical locators beginning with `synthetic/`;
they do not claim those manifests exist or were read. Each synthetic
`source_sha256` hashes the canonical JSON object containing just `id`, `name` and
`description`. This is deliberately distinct from the raw manifest digest produced
by `catalog`.

Context records `in_vault`, `jev_first`, `request_mode`, `language` and coverage
labels. Context is input data, not an authorization mechanism. In particular, the
Jev cases describe already authorized inline requests; replaying them calls no Jev
API and transmits no request. Ordinary mention of Jev, a vault or a webapp includes
negative cases. Read-only requests forbid implementation and publication. Ordinary
app requests do not select the synthetic Harness14 workflow automatically.

Each case declares disjoint `required`, `allowed` and `forbidden` ID lists.
Invocations outside the declared acceptable set fail, including IDs unknown to
the catalog. `allowed` calls are neutral for entrypoint selection but do not fill
a missing required skill. `before` is an acyclic list of `[earlier, later]` pairs:
each invocation of `later` must have an invocation of `earlier` before it. Valid
repeated calls do not fail merely because they repeat. If `entrypoints` is
nonempty, the first non-neutral call must be one of its required IDs. Some cases
explicitly permit two entrypoints.

A complete row with `invoked: []` can establish a negative pass. A missing row,
`unavailable` row or `error` row is unmeasured, including for a negative case.
Noncomplete rows cannot contain invoked IDs. The faulty control deliberately
checks all three missing-evidence paths, omitted mandatory composition, forbidden
implementation/publication, wrong owned-name selection, wrong entrypoints,
ordering and an unknown invocation ID.

The trace identifies `source_kind` and `observation_kind` separately:

| Label | Interpretation |
|---|---|
| `scripted_control` | Authored test observations; never host measurement |
| `host_observation` | Caller-submitted observations claimed to come from a host |
| `skill_invocation` | Ordered invocation evidence selected by default |
| `skill_file_read` | File-read evidence; score only with explicit `--observation-kind skill_file_read` and retain that label |
| `self_report` | A report about skill use, never scored as an invocation |

The runtime object records `host`, `version` and `model`. Both controls use
`{"host":"scripted-control","version":"1","model":"none"}`. Runtime labels,
caller attestations and hash matches are not independent proof that host events
occurred. Preparing genuine host observations requires a separately obtained host
event source and an honest evidence label; this tool supplies neither a live
adapter nor permission to instrument a host.

## Binding, reports and comparisons

The canonical digest is SHA-256 over UTF-8 bytes of the following serialization,
without an added newline:

```python
json.dumps(value, sort_keys=True, ensure_ascii=False,
           separators=(',', ':'), allow_nan=False)
```

Trace `catalog_sha256` and `suite_sha256` bind the entire parsed catalog and suite,
including descriptions, prompts, expectations and context. The test suite
independently recomputes both bindings. The CLI separately records raw file
digests in `raw_input_sha256`; whitespace changes can change a raw digest without
changing a canonical binding.

Default evaluation reports include IDs, ordered observations, reasons and counts,
not full prompts or context. They retain evidence and runtime labels and state
`model_run_performed: false`. The CLI adds `evaluator_sha256`, derived from the
three evaluator source modules, and `report_sha256`, the canonical digest of the
report without its own `report_sha256` field. These hashes bind the submitted
artifacts and source, not author identity, skill success or authentic host events.

`compare` validates report shape, integrity and compatibility before listing
`improved`, `regressed`, `unchanged` and `unmeasured` case IDs. Suite, evaluator,
catalog IDs, trace kind, observation kind and runtime must match. Catalog
descriptions and their hashes may differ so an explicit description experiment
can be compared; changed cases, source or runtime cannot silently become the same
experiment. The comparison reports cases, without a statistical significance or
model-accuracy claim.

Comparison requires trusted grader outputs. It checks internal consistency but
cannot regrade the expectations omitted from reports, authenticate caller-authored
grades or prove host events. A caller can alter a grade and recompute its hashes;
those hashes do not make the altered grade a trusted evaluation. Comparison
reports explicitly retain `regrades_expectations: false` and
`authenticates_observations: false`.

Inputs are bounded to 1 MiB and reject duplicate JSON keys, nonfinite numbers,
unknown schema fields, malformed IDs/digests, traversal/symlink paths, conflicting
expectations, cycles and excess nesting. Input or option errors report content-free
codes. Nothing in a report grants installation, publication or external-service
authority.

## Upstream reference

The design was informed by
[google/skill-reach at `6cc80dd9f7fc3771f088b3ae5941651db5c11938`](https://github.com/google/skill-reach/tree/6cc80dd9f7fc3771f088b3ae5941651db5c11938),
whose upstream license is Apache-2.0. It is a reference only: no upstream code,
prompts or datasets are copied into these fixtures or evaluator. The local design
uses explicit multi-skill expectations and supplied observations; it does not
adopt a heuristic automatic rewrite/apply loop.

For release assets, reproducible ZIPs and the limits of provenance checks, see
[release integrity](release-integrity.md). Routing-control results and public host
discovery checks must remain separately labeled in release verification.
