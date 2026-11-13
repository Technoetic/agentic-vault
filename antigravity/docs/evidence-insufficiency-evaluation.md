# Synthetic evidence insufficiency evaluation

`scripts/evaluate_evidence_insufficiency.py` distinguishes acknowledging missing
evidence from asserting an unsupported negative. It uses five independently
authored Korean synthetic cases: a supported positive answer, missing evidence,
partial slot coverage, incomplete/truncated evidence, and explicit negative
evidence. It does not copy an upstream corpus or read a user vault.

```sh
python scripts/evaluate_evidence_insufficiency.py
python scripts/evaluate_evidence_insufficiency.py --control deny_missing
python scripts/evaluate_evidence_insufficiency.py --control always_insufficient
python scripts/evaluate_evidence_insufficiency.py --control wrong
python scripts/evaluate_evidence_insufficiency.py --write-observations observations.json
python scripts/evaluate_evidence_insufficiency.py --observations observations.json
python -m unittest discover -s tests -p test_evidence_insufficiency_evaluation.py -v
```

The default named `good` control yields two supported answers and three
acknowledgements. `deny_missing` turns the three insufficient cases into
unsupported premise denials. `always_insufficient` acknowledges all cases and
misses the two supported answers. `wrong` changes the supported answer values.
These controls exercise the evaluator, not model robustness or effectiveness.
The existing Korean memory evaluator already reports response, answerable,
should-abstain and silent-error rates; those metrics are unchanged. This tool's
new distinction is evidence insufficiency versus unsupported premise denial.

## Explicit response and evidence schema

A public case contains only `id`, `question`, `claim_slots` and `evidence`.
Each declared slot has `id`, `subject` and `predicate`. Evidence has an explicit
boolean `complete` and a list of records; a record has `slot`, `value`, and
`polarity` (`positive` or `negative`). One record per slot is permitted. This
small representation deliberately excludes conflicting evidence and does not
parse arbitrary documents. Labels live separately under case IDs and are never
passed to controls or answerer callbacks.

Responses contain exactly `kind` and `claims`. The bounded kinds are `answer`,
`insufficient`, and `premise_denial`; claims have the same shape as evidence
records. An asserted answer or denial needs claims. A declared premise denial
requires negative claims. An insufficiency acknowledgement may retain supported
partial claims. Free prose is not accepted or semantically classified.

An answer is supported only when its exact claims match the separate expected
claims and the evidence is complete and covers all requested slots. Missing
evidence never supports a negative. A negative claim needs the exact explicit
negative evidence record and complete evidence; otherwise it is classified as
`unsupported_premise_denial`, even when the response calls itself an `answer`
or an `insufficient` acknowledgement. Explicit negative evidence can support a
legitimate negative answer or declared premise denial. `certified_negative`
means only this exact fixture-relative check passed; it is not a real-world
absence or truth certificate. No incomplete evidence can produce that flag.

Per-case categories are `supported_answer`, `insufficiency_acknowledged`,
`unsupported_premise_denial`, `other_error`, and `execution_error`. A callback
exception or malformed response is an execution error, never correct. Exception
text is omitted. Empty case populations yield a null accuracy, not zero or one.

## Coverage is diagnostic only

`coverage` reports declared/covered slot counts, missing slot IDs, their ratio,
`coverage_low`, and evidence completeness. The default diagnostic threshold is
1.0; `--coverage-low-threshold` accepts a finite number from 0 through 1. A ratio
below it sets `coverage_low`. Changing the threshold does not change grading or
enable a retrieval cutoff. A truncated case may cover every declared slot and
still be incomplete. These diagnostics describe only the fixture's declared
slots: they do not measure general relevance, discover missing concepts, prove
nonexistence, or change any default retrieval policy.

## Replay, provenance and limits

`load_pack(path)` accepts a synthetic `pack.json` file or its directory.
`evaluate(pack, control="good", observations=None, answerer=None,
coverage_low_threshold=1.0)` returns results and an `observations` envelope.
A callback receives a deep copy of one public case, with no labels, whole-pack
metadata, or other-case observations. Both controls and the grader operate on
independent copies; callback mutation cannot alter grading inputs.

The replay envelope contains `schema_version`, `fixture_digest`,
`config_digest`, and `observations`. Each observation has `case_id`, `binding`,
`response`, `origin`, and `control`. `binding` includes SHA-256 digests of the
full fixture, full public case, question, evidence and evaluator configuration.
Digests use canonical JSON; JSON formatting changes alone do not invalidate a
replay. Changed fixture content, question, evidence or diagnostic configuration
does invalidate it. Missing, extra, duplicate, malformed or mismatched replay
rows abort the entire run. A complete empty pack can replay an empty envelope.

To replay external outputs, retain the exported envelope and each binding,
replace only the corresponding structured response, and set that observation's
`origin` to `external_observation` and `control` to null. This is caller-attested
provenance, not a signature, model invocation proof, or independent origin
verification. `synthetic_control` rows name a declared control. Replay validation
does not authenticate that a response was really generated by the named origin.
The evaluator makes zero model requests and reports no actual cost; it does not
invent model names, token usage or billing. No network or provider is configured.

JSON inputs are limited to 1 MiB, 100 cases, 32 slots/claims per case, 20,000
tree nodes, depth 24, bounded strings, and finite bounded numbers. Unknown fields,
duplicate JSON keys, conflicting slot records, booleans used as numeric versions
or thresholds, nonfinite values, symlinks/reparse points (including ancestors),
hardlinked files and unstable file reads are rejected. Before filesystem
inspection of a supplied input, output or fixture path, portable lexical
validation normalizes both separators and rejects UNC/network prefixes,
including extended network/device forms, colon-bearing components (including
NTFS alternate data streams), reserved Windows device names, and unsafe Windows
component characters or trailing spaces/dots. CLI paths are validated before
loading the bundled fixture. Ordinary local relative paths and drive anchors
such as `C:/` remain accepted. UTF-8 BOM is accepted.
These checks are not an OS sandbox or a guarantee about mapped drives or
network-backed mounts; they also cannot prevent an adversarial process racing
directory replacement.

The CLI emits JSON, escaping non-ASCII text for Windows pipe compatibility.
Invalid input or output produces a bounded generic error and exit status 2.
Scoring errors are data, not exit-code gates. Observation export creates a new
file and refuses to overwrite an existing file; its parent must already exist.
Callbacks with failed or malformed responses produce an explicitly incomplete
observation envelope, which cannot be replayed successfully; the CLI refuses an
incomplete export. The CLI accepts only the bundled synthetic fixture, not a
vault or arbitrary document path.
