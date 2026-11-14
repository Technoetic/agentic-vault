# Bounded improvement observations

`vault_improvement_cycles.py` observes an explicit, bounded sequence of staged
evaluation reports. It returns advice and never runs the next generation,
executes a check, invokes a model, promotes a lesson, or changes vault files.
Default vault workflows are unchanged.

```text
python vault_improvement_cycles.py --vault /path/to/vault audit --input input/cycle.json
```

The Python API is `audit(vault, input_path)`. Paths are vault-relative and use the
existing deny, exclusion, credential, symlink, stable-read, secret and size
policies. Failures raise `CycleError` with a content-free code. The CLI prints
JSON and exits 2 for invalid or unsafe inputs; a valid audit exits 0 even when
its advisory result recommends stopping without success.

The version 1 input has these exact fields:

```json
{
  "version": 1,
  "contract_id": "<64 lowercase hexadecimal characters>",
  "max_generations": 3,
  "max_cost_microusd": 1000000,
  "generations": [
    {
      "id": 1,
      "evaluation_report": "input/evaluation-1.json",
      "cost_microusd": 100000
    }
  ]
}
```

`max_generations` is explicit and ranges from 1 to 64. The sequence has at least
one observation and cannot exceed that bound. Generation IDs are contiguous
one-based integers in the supplied order. Reversed IDs, duplicate report paths,
unknown fields, booleans used as integers, and unsafe or overlapping inputs are
rejected. The optional cost limit and known costs are nonnegative integer
micro-US dollars, each no larger than 2^63 - 1. Every generation includes
`cost_microusd`; use `null` when it is unknown. Zero is a known zero cost.

Each report uses the [staged evaluation](staged-evaluation.md) schema. The
observer recomputes every selected report through that evaluator, then
recomputes them again before returning. It also rechecks its selected input and
coverage bytes and current vault configuration. It freezes the contract ID,
accepted criteria fingerprint, acceptance ID set, acceptance-to-check mapping,
coverage manifest bytes and path, and the independent-review requirement across
generations. Reports for a different contract or weakened rubric cannot form a
single history. The cycle input cannot itself provide review support.

There is no saved pass/fail summary field. A caller-supplied summary cannot
replace native evidence and current source bindings. A valid staged `passed`
result requires the evaluator's native-evidence-backed observations and review
data, while still leaving execution, reviewer identity and semantic fulfillment
unverified. Source hashes do not prove that a command ran or a task is true.

The output includes `status`, `success`, `reason`, `advisory`,
`terminal_generation`, per-generation observations, source bindings and explicit
authority limitations. `authority` is `advisory_observer_only`;
`runtime_limits_enforced`, `history_authenticated`, `execution_verified` and
`semantic_fulfillment_verified` are always false.

| Condition | Status | Reason |
| --- | --- | --- |
| First currently passing staged report within the observed cost limit | `success` | `verified_success` |
| Previously passed acceptance criterion now explicitly fails | `stopped` | `regression` |
| Consecutive identical nonpassing acceptance outcomes | `stopped` | `no_progress` |
| Four outcomes alternate A, B, A, B | `stopped` | `oscillation` |
| Known cost subtotal exceeds the declared limit | `stopped` | `cost_limit` |
| Nonpassing generation reaches the cost limit | `stopped` | `cost_limit` |
| Nonpassing final allowed generation | `stopped` | `max_generations` |
| Stale or blocked staged report | `blocked` | `stale_evaluation` or `blocked_evaluation` |
| Supplied observations end with capacity remaining | `continue` | `observations_exhausted` |

Passing on the first or final allowed generation succeeds. A passing generation
at exactly the cost limit also succeeds; an already exceeded known cost limit
takes priority and produces no success. An unverified, skipped, missing or
failed check never becomes success through exhaustion. Changing evidence-file
names or review prose without changing acceptance outcomes does not count as
progress. Regression uses every acceptance criterion that previously passed,
including criteria that passed within an otherwise failing generation.

Version 1 uses a strict history: observations after any terminal result are
invalid (`observations_after_terminal`). In particular, a later historical
failure cannot overturn an earlier success. Submit the prefix ending at that
first terminal result.

The cost result contains `known_subtotal_microusd`, `unknown_count` and
`budget_safe`. Unknown costs contribute to the count and remain unknown rather
than becoming zero. `budget_safe` is `null` when any cost is unknown or no cost
limit was supplied; otherwise it compares the declared known subtotal with the
limit. A successful data observation can still have unknown spending.

Costs, generation order and report history are caller declarations. A caller
can rewrite and renumber an untrusted ledger; numeric ordering validation does
not authenticate history. The observer has no persistent execution state or
spending meter and cannot enforce a runtime limit. Its `continue` advice only
means the current declared prefix has no terminal condition; a host must decide
whether to run or pay for any further work. Existing frozen run comparisons
remain separate and retain their own evaluator-contract checks.
