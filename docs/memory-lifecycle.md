# Cross-process synthetic memory lifecycle and repair controls

The optional standard-library runner `scripts/evaluate_memory_lifecycle.py` checks
fixed synthetic behavior across22 fresh Python worker invocations:11 phases for
each of two bilingual cases, with paired retention/forget arms in every case.
It reads no real vault, accepts no external dataset, executes no model, and makes
no network request. Worker input is a bounded internal protocol confined to a
marked disposable vault; it is not an arbitrary command interface.

```console
python -B scripts/evaluate_memory_lifecycle.py
python -B scripts/evaluate_memory_lifecycle.py --control vulnerable
python -B scripts/evaluate_memory_lifecycle.py --control wipe-all
python -B scripts/evaluate_memory_lifecycle.py --max-tokens 0
python -B -m unittest discover -s tests -p test_memory_lifecycle.py -v
```

The public API is `evaluate_lifecycle(control='bounded', max_tokens=1500)`.
Controls are `bounded`, `vulnerable` and `wipe-all`; budgets are integers0–4096
excluding booleans. Completed measurements return JSON and exit0, even when the
vulnerable control exhibits failures. Invalid options/worker failures return only
`{"error":"lifecycle_evaluation_invalid"}` and exit1. An execution error is not
reported as successful protection.

## What is new relative to formation tests

Existing [formation controls](memory-formation.md) already write captures,
compile provenance-bound units, mutate lessons, reread them and execute follow-up
tasks. This runner adds actual process exit/reload boundaries and a sustained
sequence: capture → three separately persisted wrong-feedback updates → pre-repair
probe → explicit correction/withdrawal → post-repair probe → replay a declared old
copy → probe → an independent later observation → probe.

The parent orchestrates fixed operations; each worker imports current core
modules. It never prefilters original unit proofs to simulate the engine's
revocation behavior. Exposure is measured from both real `compile_units` context
and actual recall context. Actions use the deterministic workflow executor.

## Observations and controls

- Verify persisted captures/lesson changes rather than trusting a write claim.
- Establish that the paired control actually stored and could use the target;
  never reward failure to remember as successful forgetting.
- Inspect direct and declared-derived context exposure, subsequent task output,
  correction success, residual contamination and recovery steps.
- Replay an old copy with an explicit declared derivation and verify its persisted
  hash/proof before evaluating reintroduction.
- Re-test an unrelated previously successful task immediately after repair. A
  wipe-all strategy fails preservation before any later relearning can hide it.
- Add an independent later fact to measure usable future learning separately from
  revival of a declared withdrawn copy.
- `vulnerable` deliberately omits the registry setting, producing a positive
  residual-exposure/action-failure control. `wipe-all` actually removes the
  synthetic memory and measures collateral loss.

## Interpretation

Results are fixed **deterministic cross-process functional controls**, not real
LLM agent robustness, production effects, privacy deletion guarantees or rankings
of external memory systems. The declared graph covers only known copies. English
and Korean fixtures are deliberately small, with scripted tasks and known
feedback; no semantic judge infers truth. The registry is enabled only within the
disposable evaluation vaults.

The design borrows evaluation questions from [MemSecBench](https://arxiv.org/abs/2607.27080)
(persistence→consequence→selective repair), [MPBench](https://arxiv.org/abs/2606.04329)
(misleading facts/precedents and benign preservation), and
[AgentMemoryBench Repair](https://github.com/solomoon313/AgentMemoryBench)
(wrong feedback→correction→retention). No paper code/data is copied, official
scores are not reproduced and the projects' tool/model assumptions do not
automatically apply to this vault. [Revocation](memory-revocation.md) documents
the actual supported consumers and limitations.
