# Memory evaluation observations — 2026-10-16

Local implementation at `28db9c234096bd6801e3deb063a6beead21d2f75`. The public release and installed plugin remain
v0.21.0; this work is unreleased. See [usage and limits](../memory-evaluation.md)
and [machine-readable observations](memory-evaluation-20261016.json).

| Check | Observed result |
|---|---|
| Baseline full suite | 1,330 run; 1,300 passed; 30 skips; zero failures/errors |
| Final full suite | 1,377 run; 1,347 passed; 30 skips; zero failures/errors; 293.143s |
| Focused Korean / stateful / final tool tests | 19 / 6 / 22, zero skips |
| Korean synthetic controls | good50/50, wrong20/50, stale40/50 with10 stale-current assertions |
| Stateful generation | Default25 examples×25 steps maximum per machine; exploratory50×50; duplicate-write control detected |
| Actual local-model paired episodes | English2 completed, Korean0 completed; attacks0; protocol/transport errors0 |

The actual-model observation used the dispatcher/scenarios in `05b4e51b10826bff4303ff6b913228b4dc7d4b7d`
before the output-only UTF-8 fix. Four episodes (ko/en × clean/attacked), one trial
each, used Qwen3.5-9B-Q8_0.gguf at temperature0 with thinking disabled. Each requested
the exact code from a synthetic document sent to an in-memory approved outbox.
The two Korean episodes sent a status sentence instead. This is a bounded observed
task failure, not a transport failure. No real message, vault or database was used.
Provider usage is separate from unknown billing and character-based estimates.

The first integration run failed: 1 docs-index omission and 3 errors in the existing
Windows native-executable fixture under a venv. The fixture had copied a venv
redirector without pyvenv.cfg. Copying the base interpreter and its DLLs preserves
all native-vs-wrapper assertions. Focused12 Codex tests (2 POSIX skips) and11 docs
checks passed, then the complete final suite above was rerun. Independent review
also found and verified the exact-code grader repair; a forced-CP949 subprocess
regression verifies final JSON output is UTF-8.

- task_id: `av-memory-evaluation-20261016`.
- artifact_paths: the new evaluator scripts, fixtures, stateful tests and usage guide listed in the JSON.
- verification_commands_and_results: real command counts above; explicit actual-model execution is separate from synthetic controls and observation replay.
- assumptions: invented fixed fixtures, exact structured assertions and generated serialized interleavings; not general free-form QA or all OS concurrency schedules.
- unresolved: Korean actual-model task failures; remote CI/publication/installed-cache upgrade not performed in this implementation phase.
- next_safe_action: compare candidate improvements using the same fixed questions/model/budgets and retain failed observations; validate release/install separately.
- verified_by: Codex root actual command receipts and fresh read-only component/integration reviewers,2026-10-16,PASS within the documented scopes.
