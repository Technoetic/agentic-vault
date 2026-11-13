# Offline memory-formation controls

`scripts/evaluate_memory_formation.py` is an optional standard-library runner. It
creates disposable synthetic vaults and measures paired memory-formation failures.
It does not change normal recall, formation, configuration or CLI defaults.

```console
python -B scripts/evaluate_memory_formation.py
python -B scripts/evaluate_memory_formation.py --control vulnerable
python -B scripts/evaluate_memory_formation.py --max-tokens 0
python -B -m unittest discover -s tests -p test_memory_formation.py -v
```

The CLI emits one JSON document, schema version 1. A completed measurement exits
0, including a vulnerable-control run with observed failures. Invalid input or an
execution error emits only `{"error": "formation_evaluation_invalid"}` and exits
1. It does not echo payloads or exception messages. `--help` displays usage.

The Python API is `evaluate_formation(max_tokens=1500, control='bounded',
scenarios=None)`. `scenario_pairs()` returns fresh strict-schema fixtures for
constraint and mutation tests. There is no CLI option for reading a real vault,
arbitrary path, model endpoint or external fixture.

## Pair contracts

Both arms use the same checksum task, empty initial state, context budget and
formation policy. Discovery first runs the existing workflow's failing default
plan and obtains actual tool feedback. The five categories are small,
MemForm-inspired synthetic cases; they are not official benchmark cases.

| Pair | Changed factor | Preserved constraint |
| --- | --- | --- |
| P1 Content | Instructional advice appended to captured feedback | Structured observation and task |
| P2 Evidence | Source bytes modified after provenance derivation | Original source binding and task |
| P3 Scope | Debug-only observation asks for global application | In-scope observation remains present; outside scope is measured separately |
| P4 Selection | One selected experience changes | Identical two-item pool and selection count of one; no safety backfill |
| P5 Path | Integration order changes | Exactly identical experience multiset and selection; only order differs |

Fixtures are limited to five pairs, eight experiences per arm, 2,048 advice
characters per experience, 65,536 serialized scenario bytes and 0–4,096 context
tokens. IDs/scopes and feedback have bounded syntax; unknown fields, duplicate
selections, incomplete orders and pair-pool changes are rejected before running.

## Core path and measurements

Each arm writes actual captures, derives provenance, compiles source-bound units,
and applies ADD/EDIT lesson deltas in its own temporary vault. It then rereads the
lesson, compiles recall context under the selected budget, and runs the existing
deterministic planner and workflow executor. Capture/proof validation and lesson
mutation use the installed core modules. The tool-safety evaluator's strict JSON
parser rejects duplicate observation keys.

Formation policies see captured bytes, source bindings, scope and integration
order. They do not receive attack labels, arm names or expected answers. The
grading block alone compares stored plans with the tool's discovery feedback.

Reports separate task success; final contaminated memory; any contaminated
storage event, including a later-corrected lesson; unsafe plan adoption; actual
follow-up failures; and clean-task loss between arms. A blocked candidate can
reduce task success: P2 and P4 deliberately expose this cost. Zero context cannot
borrow the grader's expected plan. `unsafe_rule_adopted` means adoption of a
synthetic action plan, not a change to a real agent's rules.

## Interpretation limits

These are deterministic functional controls, not evidence of model robustness or
an official MemForm reproduction. The bounded control is an evaluation-only
policy, not an engine-wide defense. It accepts structured observations with
current provenance and matching scope; the vulnerable control deliberately
bypasses these gates and follows embedded advice, exposing positive controls.

Host-reported `own` origin is not authentication. A current receipt establishes
byte/config bindings, not truth or authority; coherent fabricated own-origin
feedback remains outside the protection demonstrated here. Lesson approval is
confined to the disposable simulation. No external code, datasets, models,
network calls, shell commands from input, credentials or real-vault mutations are
used. Results should guide regression testing rather than rank systems or claim
production prompt-injection resistance.
