# Lesson cue observation scoring

`scripts/evaluate_lesson_cue_effects.py` scores supplied observations from paired
baseline, hint and advisory guard trials. It compares attempted actions and final
outcomes against an independently authored task oracle. It runs no host, model,
shell command, lesson compiler or hook, makes no network request, and needs no
credentials. This adds measurement for the
[existing optional lesson cues](../assets/adapters/lesson-cues/guide.md); it does
not change their delivery, defaults or permission behavior.

```text
python scripts/evaluate_lesson_cue_effects.py
python scripts/evaluate_lesson_cue_effects.py --json
python scripts/evaluate_lesson_cue_effects.py --input local-observations.json --json
python -m unittest discover -s tests -p test_lesson_cue_effects.py -v
```

The default is an original **scripted demonstration**: 16 fictional trial
receipts in six groups showing a positive pattern, no effect, late delivery,
missing observations, stale compilation and an incomplete pair. It contains
**zero actual-host trials**. The positive pattern is hand-authored, not an
experiment or a measured benefit. Default hints are marked as available at a
later decision: the existing PreToolUse hint cannot change the triggering call.
No actual-host effectiveness claim is established by this release.

`--input` reads one explicitly supplied local JSON file. An explicitly empty
path is rejected; only omission of `--input` selects the demo. It does not discover
transcripts, read a user vault or follow paths mentioned in a record. Importers
must preserve failed actions and distinguish unknown observations from observed
zeroes. Do not include secrets, contact details or unnecessary transcript text.
Action and outcome IDs refer to observations reviewed by the trial author; this
scorer cannot extract their meaning from raw prose.

## Python API and schema v1

Load this checkout script as a Python module to call:

```python
pack = evaluator.load_trials(path)       # bounded local JSON reader
report = evaluator.evaluate_trials(pack) # pure in-memory scoring, no file reads
demo = evaluator.demo_pack()             # scripted inputs, not report output
```

`evaluate_trials(pack)` accepts exactly these top-level fields:

| Field | Value |
| --- | --- |
| `schema_version` | Integer `1`, never a boolean |
| `scenarios` | Between 1 and 100 independently specified task/oracle records |
| `trials` | Between 0 and 300 observed trial records |

A scenario has exactly `id`, `bindings`, and `oracle`. IDs use 1–96 ASCII letters,
digits, `.`, `_`, or `-`, starting with a letter or digit. A binding object has
exactly these seven lowercase SHA-256 values:

```json
{
  "task_sha256": "<hash of the frozen task specification>",
  "prompt_sha256": "<hash of the common prompt, excluding the declared cue>",
  "model_config_sha256": "<hash of the frozen model and sampling configuration>",
  "host_config_sha256": "<hash of the frozen host, tools and environment configuration>",
  "lesson_source_sha256": "<hash of the source lesson ledger bytes>",
  "compiled_lesson_sha256": "<hash of the compiled cue artifact bytes>",
  "oracle_sha256": "<hash of canonical oracle JSON>"
}
```

The placeholder strings above are documentation, not valid digests. Source and
compiled digests identify different artifacts and are not expected to equal each
other. Every trial must match the declared expected value for each artifact.
Task, prompt and configuration hashes must bind the same inputs across all three
conditions. Record the intervention separately as `condition` and `cue`; do not
exclude unrelated model, prompt or host changes from those hashes.

The oracle has exactly these fields, authored from the task's success criteria
before reviewing intervention outcomes:

```json
{
  "id": "snapshot-task-oracle",
  "expected_first_actions": ["inspect-snapshot"],
  "acceptable_final_outcomes": ["snapshot-verified"],
  "interruption_expected": false
}
```

Each action/outcome list contains 1–32 unique IDs. Oracle alternatives are OR.
`interruption_expected` is a boolean task criterion. To hash this oracle:

```python
hashlib.sha256(json.dumps(oracle, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
```

An incorrect oracle consistently produces incorrect scores. Its digest checks
binding and accidental edits, not truth or independent authorship. The trial's
optional `teacher_label` is ignored in scoring. It cannot replace the oracle.

A trial requires `id`, `scenario_id`, `pair_id`, `condition`, `provenance` and
`bindings`. Its remaining permitted fields are listed below. Unknown fields are
invalid. Omitting an observation field is equivalent to `null`.

| Field | Value |
| --- | --- |
| `id` | Globally unique trial ID |
| `scenario_id` | An existing scenario ID |
| `pair_id` | One comparison group, containing exactly one trial per condition |
| `condition` | `baseline`, `hint` or `guard` |
| `provenance` | `scripted` or `caller_reported_actual_host` |
| `bindings` | The seven-field binding object above |
| `cue` | `null` for baseline; otherwise the timing object below or unknown `null` |
| `actions` | A complete attempted action sequence, at most 128 entries, or `null` |
| `final_task_check` | Outcome and oracle observation below, or `null` |
| `interrupted` | Observed boolean, or `null` |
| `elapsed_ms` | Observed total trial time in milliseconds, or `null` |
| `usage` | Object with optional `input_tokens`, `output_tokens`, or `null` |
| `teacher_label` | Optional string, at most 256 characters, or `null`; ignored |

Timing and observation examples:

```json
{
  "cue": {"kind": "hint", "shown_at_ms": 25, "decision_index": 2},
  "actions": [
    {"action_id": "change-first", "decision_index": 1, "attempt_index": 1, "at_ms": 20},
    {"action_id": "inspect-snapshot", "decision_index": 2, "attempt_index": 2, "at_ms": 40}
  ],
  "final_task_check": {
    "oracle_sha256": "<same canonical oracle digest>",
    "outcome_id": "snapshot-verified",
    "checked_at_ms": 80
  },
  "interrupted": false,
  "elapsed_ms": 100,
  "usage": {"input_tokens": 120, "output_tokens": null}
}
```

`decision_index` identifies actual attempted-action decisions, beginning at 1
and increasing consecutively without omitted decisions. `at_ms` is the observed decision/action time on a trial
relative monotonic clock. The cue's index is the first decision to which it was
available, and `shown_at_ms` uses the same clock. Record next-request delivery
of a PreToolUse hint as a later decision; do not treat injection as evidence that
the triggering decision read it. The scorer rejects contradictory recorded
timestamps but cannot verify the host clock or delivery semantics. With a known
action sequence, the cue index must identify an actual recorded decision and
the cue timestamp must fall between that decision's predecessor and the named
decision. A cue after the final task check is invalid. A missing action timeline
leaves cue availability `unknown_timeline` and supplies no intervention deltas.

An action sequence starts with attempt 1. Attempt indices stay the same within
one task attempt, then increase by exactly 1 for a retry. Failed or denied
attempted calls belong in the sequence. Indices do not turn successful steps
within one attempt into retries. A partial sequence must be `null`, not a tail
with the failed first action removed. An empty sequence supplies no first action
or retry count. Action times cannot decrease, the final check cannot precede the
last action, and recorded events cannot exceed known total elapsed time.

Times are finite nonnegative numbers. Token counts and indices are integers;
booleans are rejected as numbers. Numeric values are bounded at `10**12`.

## Scores and exclusions

The report retains one row per supplied trial, with observation state
`complete`, `partial`, `missing`, or `invalid`. Valid raw observations remain in
stale/confounded rows, but do not enter paired deltas. These metrics are separate:

| Metric | Derivation and direction of delta |
| --- | --- |
| `first_action_correct` | Whether the first attempted action matches the task oracle; positive is better |
| `retry_count` | Last observed attempt index minus one; negative means fewer retries |
| `final_task_correct` | Whether the recorded final outcome matches the bound task oracle; positive is better |
| `false_interruption` | Observed interruption when the task oracle expected none; negative is better |
| `elapsed_ms` | Supplied total latency; negative means faster |
| `input_tokens`, `output_tokens` | Supplied token usage, independently nullable; negative means less usage |

A wrong first action followed by a good final result scores `false` and `true`,
respectively. This prevents a retry or correction from erasing the initial
mistake. A final check is a recorded observation, not a command that this scorer
executes. `false_interruption` does not claim the absence of a missed required
interruption; read it with the action and final-outcome metrics.

Every delta is intervention minus baseline for a complete baseline/hint/guard
group. A known zero remains zero; a missing value remains JSON `null`. Each
summary cell has its own `n` (measured pairs) and `mean_delta`, which stays `null`
at `n: 0`. There is no zero filling or pooling of scripted and supplied host
observations. Summaries further separate `before_first_decision`,
`later_decision`, `missing_cue` and `unknown_timeline`. Later cues never receive first-action delta
credit. Their whole-trial retries, final outcomes, latency and usage can still
be compared, with the later-delivery label visible. Missing cues supply no
intervention deltas, even when other observations are present.

Trial states are `valid`, `stale` (source/compiled lesson or final-check oracle
mismatch), `confounded` (task/prompt/model/host/oracle binding mismatch or repeated
trial ID), and `invalid` (malformed). Pair states are:

| State | Meaning |
| --- | --- |
| `eligible` | Three structurally valid, bound trials with complete metric observations |
| `missing_observations` | Pair structure is valid; score only known metrics with an observed cue |
| `unpaired` | A condition is absent |
| `contains_stale` | A member has a stale artifact/check binding |
| `contains_invalid` | A member is malformed |
| `confounded` | Repeated condition, mixed scenario/provenance, duplicate trial ID, or binding confound |

Only `eligible` and `missing_observations` supply paired deltas. If several
problems coexist, pair precedence is confounded, invalid, stale, unpaired,
missing, eligible; individual rows preserve their own states. A malformed
record without a safe `pair_id` remains an invalid ungrouped row. A missing
third condition excludes both comparisons, rather than silently changing the
comparison design.

Input and trial canonical JSON digests support audit lookup. Validated binding
digests and safe IDs appear in the report; raw action text, teacher labels,
oracle outcome lists and configurations do not. Choose IDs that are safe to
disclose. Digests are neither signatures nor proof that a real model ran.

## Validation and interpretation limits

The reader rejects duplicate JSON keys, nonfinite/oversized numbers, unsupported
objects, control characters, surrogate code points, cyclic direct input,
structures deeper than 20, more than 20,000 nodes, objects over 64 keys, strings
over 4,096 characters, and input over 1 MiB. Input/schema/scenario errors raise
fixed-code `ValueError`; a malformed individual trial is an invalid report row.
The CLI exits with 2 and a content-free error code on rejected input, or 0 when
a report is produced. Exit 0 does not certify trial eligibility or efficacy.

Explicit file paths reject network/UNC/device/stream syntax, Windows reserved
names, existing symlinks or reparse components, directories, nonregular files,
multiple hard links, and changes detected across file-open/read checks. This is
defensive local-file handling, not a sandbox or guarantee against a hostile
filesystem racing every check. It does not authorize access to a restricted
file. Paths inside a pack are unsupported schema data and are never followed.
An operating-system alias such as macOS `/var` is also a linked parent; supply
the explicit physical path instead. Tests resolve only their owned temporary
directories before supplying fixture paths and separately verify alias refusal.

`caller_reported_actual_host` means the caller claims the receipts came from a
host run. This tool does **not** authenticate that claim, infer a hidden model
run from cue delivery, verify natural-language correctness, or establish causal
effect. Matching hashes and coherent timing do not detect forged transcripts,
carryover learning, selection bias or undeclared environmental changes. Review
the original authorized observations and oracle independently. Use fresh,
isolated trials and a justified sampling/order protocol for a real experiment;
per-metric sample sizes here are descriptive, with no efficacy certification.

The existing hint/guard mechanism previously drew concepts from
`joeblackwaslike/lessons-learned` at `3b39f38152fe` (MIT), as recorded in the
linked cue guide. This evaluator adds independently authored observation
scoring. It imports no upstream code, benchmark data, model endpoint or new
runtime dependency, and those upstream results are not plugin measurements.
