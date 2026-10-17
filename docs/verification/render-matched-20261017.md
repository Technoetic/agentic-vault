# Render-matched temporal control — 2026-10-17

Local implementation on `feat/v023-render-matched`, based on `63b4cc9` (v0.22.1). All measurements
below ran at source `bc4c55e` on Windows with Python 3.12. The work is unreleased and leaves
versions and release metadata unchanged. See [usage, results and limits](../memory-evaluation.md)
and the [machine-readable observations](render-matched-20261017.json).

The control follows [Presentation, Not Mechanism](https://arxiv.org/abs/2607.16019)
(arXiv v1, submitted 2026-07-17). Its abstract reports 2,907 high-agreement questions, an
apparent ledger gain of +0.182 over flat retrieval, and a mechanism residual of +0.021 to +0.025
across two judge families once rendering is matched. Those values are reported by the source,
were read from the abstract page on 2026-10-17, and were not reproduced here. The abstract names
neither the metric nor the question language.

| Check | Observed result |
|---|---|
| Baseline suite, `63b4cc9` | 1,375 run; 32 skips; OK. The run overlapped the first edits, so it is cited for the test count |
| Implementation suite, `9fb885e` | 1,393 run; 32 skips; OK; 382.104 s |
| Final suite, `python -m unittest discover -s tests` | 1,393 run; 32 skips; zero failures/errors; OK; 382.946 s on `bc4c55e` plus these records. Two subprocess reader-thread `UnicodeDecodeError` messages (byte 0xbf) also appear in the baseline run; no test fails |
| Focused tests at `bc4c55e` | `tests.test_render_matched`: 18 run, OK. Seven related retrieval and evaluator modules: 154 run, 5 skips, OK |
| Mutation checks | Forcing the control marker onto the default path, or keeping exclusion under `annotate_only`, each produced 8 failures (subtests counted); source restored |
| Default equivalence | 7/7 CI evaluation commands give identical JSON after removing wall-clock fields; all exit codes 0 at both sources |
| `python -m compileall -q hooks skills/agentic-vault/scripts scripts`; `git diff --check` | exit 0; exit 0 |

Default equivalence compared a `git archive` of `63b4cc9` (repository line-ending conversion,
fixture bytes identical to the worktree) with the measured source. It covered all seven CI
evaluation commands with their gates. Only `elapsed_ms`, `total_elapsed_ms`, `mean_elapsed_ms`
and `retrieval_elapsed_ms` were removed.

## Decomposition

Each row adds `--render-matched` to the CI command for that fixture. `display = render_matched −
legacy` and `mechanism = advanced − render_matched` are paired means on the same queries. Their
sum equals `advanced − legacy`; every decomposition error was below 1e-12. Parentheses give the
two-sided p value from paired sign randomization (seed 1707, 10,000 permutations, add-one Monte
Carlo, smallest value 1/10,001), then the number of nonzero mechanism pairs.

| Configuration | Metric (pairs) | legacy | render_matched | advanced | display | mechanism |
|---|---|---|---|---|---|---|
| recall_advanced top3 | nDCG@5 (14 answerable) | 0.707 | 0.885 | 0.938 | +0.179 (0.24) | +0.053 (0.51, 2) |
| recall_advanced top3 | recall@k (14) | 0.786 | 1.000 | 1.000 | +0.214 (0.24) | 0 (1.00, 0) |
| recall_advanced top3 | stale top-1 (4 stale-labeled) | 1.000 | 1.000 | 0.000 | 0 (1.00) | −1.000 (0.13, 4) |
| recall_advanced top3 | no-answer false positive (2) | 1.000 | 1.000 | 0.000 | 0 (1.00) | −1.000 (0.50, 2) |
| recall_hard top3 | nDCG@5 (56 answerable) | 0.570 | 0.980 | 0.980 | +0.411 (0.0001) | 0 (1.00, 0) |
| recall_hard top3 | recall@k (56) | 0.696 | 1.000 | 1.000 | +0.304 (0.0001) | 0 (1.00, 0) |
| recall_hard top3 | stale top-1 (10 stale-labeled) | 0.300 | 0.200 | 0.000 | −0.100 (1.00) | −0.200 (0.51, 2) |
| recall_hard top3 | no-answer false positive (8) | 0.500 | 0.500 | 0.250 | 0 (1.00) | −0.250 (0.50, 2) |
| recall_hard top5 | nDCG@5 (56 answerable) | 0.580 | 0.980 | 0.980 | +0.401 (0.0001) | 0 (1.00, 0) |
| recall_hard top5 | recall@k (56) | 0.714 | 1.000 | 1.000 | +0.286 (0.0002) | 0 (1.00, 0) |
| recall_hard top5 | stale top-1 (10 stale-labeled) | 0.300 | 0.200 | 0.000 | −0.100 (1.00) | −0.200 (0.51, 2) |
| recall_hard top5 | no-answer false positive (8) | 0.500 | 0.500 | 0.250 | 0 (1.00) | −0.250 (0.50, 2) |

Nonzero mechanism pairs: `en-current`/`ko-current` (nDCG@5, stale top-1),
`en-retired`/`ko-retired` (stale top-1, no-answer) in recall_advanced; `en-abstention-03` and
`ko-abstention-03` (stale top-1, no-answer) in recall_hard. All gates on the advanced arm passed
and every exit code was 0. The render-matched arm would fail the stale top-1 gate (1.000 in
recall_advanced, 0.200 in recall_hard against a maximum of 0), which shows that the arm is
observational and never gated.

**Decision.** The pre-set rule (alpha 0.05, decision metrics only, beneficial direction) gives
`defer_richer_ledger_typing` for all three configurations. The nDCG@5 and recall@k residuals are
exactly zero on all 56 pairs in recall_hard, as is recall@k on all 14 pairs in recall_advanced.
Every other decision metric has 2 to 4 nonzero pairs.
With k nonzero pairs the smallest exact two-sided p is 2^(1−k), so these metrics could not reach
p < 0.05 at any effect size (floors 0.125 to 0.5). The defer outcome therefore means
"not distinguishable on these fixtures", not "no mechanism effect".

**Descriptive only.** Stale exposure at any returned rank was added after the outputs were
inspected and does not enter the decision. In recall_hard top3 it was 6/10 legacy,
9/10 render_matched and 0/10 advanced: the mechanism residual is −0.9 (p = 0.003, 9 pairs).
In top5 it was 6/10, 10/10 and 0/10 (−1.0, p = 0.0016, 10 pairs). Query expansion, the only
non-temporal advanced option in that fixture, surfaces more stale notes (display +0.3 and +0.4),
and the coarse exclusion removes all of them. Returned-context claim coverage was 56/56 in both
the render-matched and advanced arms of recall_hard, with zero per-query differences;
recall_advanced has no claim labels.

**Korean evaluator (evidence only).** `python scripts/evaluate_korean_memory.py --render-matched`
exited 0. Evidence was identical in 50/50 cases. The mechanism residual was 0 with p = 1.0 for
expected-source recall (30 answerable cases), withdrawn-source exposure (50) and evidence
completeness (50). The default arm rejected 0 sources for temporal status and the control kept 0
extra sources. Answers are not compared: controls and callbacks see only question, public options
and rendered context, which never carries temporal annotations. The evaluator passes no `as_of`,
withdrawal is an annotation-free deny scope, and replays are bound to the evidence hash.

## Limits

- Fixtures are synthetic. recall_advanced and recall_hard contain stale notes built to win lexical
  ranking, so the results show that the mechanism acts on constructed cases, not how often such
  cases occur in a real vault or how large the effect is there.
- No reader model ran. Temporal annotations are not rendered into context, so reader-side
  presentation effects were not measured; `display_effect` here is the non-temporal advanced
  machinery, not the paper's presentation effect.
- The paper is written in English and its numbers are source-reported; they do not transfer to
  Korean answers or to this deterministic scorer.
- The decision rule needs at least six nonzero pairs per decision metric (floor 2^(1−6) = 0.031)
  before it can detect a residual.

## Handoff

- task_id: `av-render-matched-20261017`.
- artifact_paths: `skills/agentic-vault/scripts/vault_retrieval.py`, `scripts/evaluate_recall.py`,
  `scripts/evaluate_korean_memory.py`, `tests/test_render_matched.py`, `docs/memory-evaluation.md`,
  this record and its JSON.
- verification_commands_and_results: the table and decomposition above; exact commands, outputs and
  per-metric randomization results are in the JSON.
- assumptions: per-query paired values with the same queries, options and budgets in every arm;
  alpha 0.05 two-sided, fixed before measuring; existing seed and permutation defaults.
- unresolved: no fixture has six or more validity-sensitive pairs per decision metric; reader-model
  effects are unmeasured; merging with the parallel `--lexical-ngrams` branch may conflict in the
  `evaluate()`/`retrieve()` signatures and the `main()` options.
- next_safe_action: keep the defer default. To decide with evidence, add at least six
  validity-sensitive paired queries per decision metric and rerun the same commands. A reader-model
  arm needs explicit user approval for model calls.
- verified_by: producing agent only, 2026-10-17. Independent re-execution by another session is pending.
