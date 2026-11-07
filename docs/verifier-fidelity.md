# Verifier fidelity

A check that passes tells you little until you know what it can fail on.
`scripts/evaluate_verifier_fidelity.py` measures that for the literal
preservation check, and `scripts/env_vary.py` measures whether selected tests
pass only because of the environment they ran in. Both are development tools.
Nothing in the plugin imports them, and neither gates a release.

**A rate on this page describes a small hand-written fixture. It is not evidence
of real-world safety.** The notes and mutations were written with the checker
in view, and they are not a sample of real edits. Use the table to see which
kinds of change the check can see, not how often a real edit gets through.

## Mutation table

```sh
python scripts/evaluate_verifier_fidelity.py
python scripts/evaluate_verifier_fidelity.py --json
python scripts/evaluate_verifier_fidelity.py --control always_complete
python -m unittest tests.test_verifier_fidelity_evaluation
```

The tool uses bundled synthetic Korean and English notes. Only a note that passes
unchanged is mutated, because a mutation of a note that already fails shows
nothing. Each mutation goes through `check_preservation` and through the
preserve-check path: `propose`, then `preserve_check`, in a temporary vault that
is removed afterwards. No model is called and no user vault is read.

Outcomes are counted separately:

- `flagged`: `needs_review` with a reason that came from a comparison.
- `survived`: `complete`.
- `blocked`: `propose` refused the candidate, so preserve-check never ran. This
  is the schema check at work, not the literal check.
- `unmeasured`: `needs_review` with the single reason
  `no_selected_literal_facts`. The check found nothing to compare, which is not a
  catch. The two notes without literal facts are therefore listed in
  `excluded_notes` and stay out of the table, and they confirm that declaring a
  removal does not complete them.

Current result. `flagged` columns are library, then preserve-check; `upper` is
the one-sided 95% Clopper-Pearson bound from `scripts/eval_stats.py` on the miss
rate through preserve-check, or on the false alarm rate for the two rewrite rows.

| Category | Scope | Trials | Library flagged | Preserve-check flagged | Propose blocked | Upper | Declared flagged |
|---|---|---:|---:|---:|---:|---:|---:|
| `value_change` | in_scope | 36 | 36 | 36 | 0 | 8.0% | |
| `value_change_behind_broken_fence` | in_scope | 12 | 12 | 12 | 0 | 22.1% | |
| `value_change_unextracted` | out_of_scope | 12 | 0 | 0 | 0 | 100% | 12 |
| `unit_change_listed` | in_scope | 14 | 14 | 14 | 0 | 19.3% | |
| `unit_change_unlisted` | out_of_scope | 10 | 0 | 0 | 0 | 100% | 10 |
| `swap` | out_of_scope | 6 | 0 | 0 | 0 | 100% | 6 |
| `negation_insertion` | out_of_scope | 12 | 0 | 0 | 0 | 100% | 11 |
| `fact_free_paragraph_deletion` | out_of_scope | 18 | 0 | 0 | 0 | 100% | 18 |
| `tail_truncation_with_facts` | in_scope | 18 | 18 | 18 | 0 | 15.3% | |
| `tail_truncation_fact_free` | out_of_scope | 18 | 0 | 0 | 0 | 100% | |
| `frontmatter_destruction` | out_of_scope | 66 | 0 | 0 | 42 | 47.2% | |
| `frontmatter_destruction_exempt_target` | out_of_scope | 66 | 0 | 0 | 0 | 100% | |
| `rewrite_literals_kept` | false_alarm | 30 | 0 | 0 | 0 | 9.5% | |
| `rewrite_literals_reformatted` | false_alarm | 18 | 16 | 16 | 0 | 98.0% | |

`tests/test_verifier_fidelity_evaluation.py` pins these numbers and fails when
this table and the tool disagree.

### Reading the scopes

`in_scope` is what [lesson-proposals.md](lesson-proposals.md) promises the check
sees. 80 of 80 here means the bundled mutations of those kinds were caught, and
a pooled 0 of 80 misses has an upper bound of 3.7%. The bound treats the trials
as independent, which hand-written mutations of six notes are not, so read it as
a floor on the uncertainty.

`out_of_scope` rows have 100% misses by design, and they are not defects. Each
one has a stated reason in `scope_reason`:

- Counts have no position, so a swap keeps every count.
- A value outside the extraction is not a token: numbers in words, model codes
  such as `AB1234`, `v2.5`, `±5`, `5천` against `5만`, and qualifiers such as
  morning against evening or at least against at most.
- A suffix outside the unit list falls off, so `90일` and `90주` are both `90`.
- Text without a literal is not counted, so it can go without any alarm, and
  this covers a paragraph in the middle as well as the end of a note. A tail
  holding a literal fact is in scope and is caught.
- Bodies are compared without frontmatter. Destroying it is not seen by the
  literal check. The schema check in `propose` stops 7 of the 11 bundled kinds
  on an ordinary note and none on a target it exempts, such as `CLAUDE.md`.

The `Declared flagged` column repeats the mutation with `--claim` set to the
phrase a careful author would name. Declaring closes most of these gaps. The one
remaining miss is a negation put in front of a declared sentence, because the
declared text is still there word for word. Text added after a declared phrase
behaves the same way, so declare up to the end of the clause. Frontmatter cannot
be declared: a claim there is the error `claim_not_in_original`.

`false_alarm` rows change the wording and keep the meaning. Rewrites that keep
every literal stay quiet, including CRLF conversion, reordered paragraphs and an
added paragraph. Rewrites that change a literal's format are nearly always
flagged: `15건` to `15 건`, a reformatted date, `350만원` to `350만 원`,
`22kV` to `22 kV`, or a number written out. Expect that review noise, and do not
widen the extraction to hide it without measuring what it would then miss.

`in_scope_survivors` and `false_alarm_cases` in the JSON report name each case,
so a change in the table can be traced to a mutation. The two controls replace
the check with a fixed answer to show the table can move: `always_complete`
flags nothing, and `always_review` leaves no note eligible.

The rates are not comparable with published verifier fidelity figures. The design
comes from DocOps ([arXiv:2607.19865](https://arxiv.org/abs/2607.19865), v1,
first posted 2026-07-22): mutate only outputs that passed, use a controlled
number of mutations per category, report per category and explain the misses.
Its 96.67% belongs to task-specific post-condition checkers, while
`check_preservation` is one general function. Its human audit has no counterpart
here. This repository reuses the method only; no benchmark data or code is copied.

## Fixes that came from the measurement

Three in-scope gaps were corrected in `vault_compile_quality.py`. Two of them
also change `check_compile`, which shares the extraction.

- **Broken closing line.** The schema check closes frontmatter at any line that
  starts with `---`, so a candidate ending its frontmatter with `----` passes
  `propose`. The body reader only accepted an exact `---`, counted the frontmatter
  as body, and a body value that also sat in the frontmatter could change without
  an alarm. It now uses the same boundary and keeps any text after the dashes in
  the body. A leading rule whose next line is not a `key:` line is still body
  text, so a table separator after a leading rule hides nothing. An exact `---`
  behaves as before.
- **Zero of zero.** A note with no literal fact reported `complete` once any
  `--expect-removed` or `--expect-added` was declared. It now stays
  `needs_review` with `no_selected_literal_facts`, because a declaration says
  nothing about the rest of the text. A preserved `--claim` is a fact, so a note
  with claims is still measured.
- **Unit case.** `kg`, `mm`, `kv` and `kva` matched only in lower case, so
  `22kV` to `22MV` left the token `22` on both sides. These units now match in any
  case and fold to one token: `22kV` is `22kv`.

The unit fix moves two things. `22kV` to `22 kV` used to be silent and now shows
as a missing `22kv`, the same as `15건` to `15 건`. A declaration that names the
value alone no longer reaches it, so `--expect-removed 22` must become
`--expect-removed 22kV`.

Against the v0.42.0 extraction the same table gave 0 of 12 for
`value_change_behind_broken_fence`, 11 of 14 for `unit_change_listed` and 13 of 18
false alarms for `rewrite_literals_reformatted`, and a declared removal completed
3 of 6 cuts of each note without facts. So the fixes gained 15 catches here and
cost 3 false alarms, all three of them a unit written with a space.

Frontmatter of more than 64 lines is still read as body. That bound is unchanged.

## Environment variations

```sh
python scripts/env_vary.py test_session_start test_vault_recall
python scripts/env_vary.py test_session_start.SessionStartTests --seed 7 --json
python scripts/env_vary.py test_vault_recall --variation utf8_mode --variation tz
python -m unittest tests.test_env_vary
```

A test can pass because of something the runner happens to provide. The tool
reruns selected tests in fresh interpreters and compares their results. The
procedure follows `check_auto` in reprotest
([manual](https://manpages.debian.org/unstable/reprotest/reprotest.1.en.html),
version 0.7.33; the method only, as its code is GPL and none of it is copied):

1. Run the control at least twice. Tests that differ between controls are
   unstable, leave the comparison and are listed. Doing this first keeps a flaky
   test from being blamed on a variation. If none are left, the verdict is
   `control_unstable`.
2. Run with every selected variation on.
3. If anything differs, shuffle the names and switch them on one at a time,
   cumulatively. A variation whose step differs is a culprit and goes off again;
   the others stay on.

The result depends on the order, so `seed` and `shuffled_order` are always in the
report, and `--seed` replays a run. When two variations only fail together, the
later one is named. A test that differs with everything on but in no step is
listed in `unattributed_tests`: it may be a one-off, so confirm it with
`scripts/flaky_repeat.py --fresh-process`.

| Variation | What changes |
|---|---|
| `utf8_mode` | `PYTHONUTF8=0`, set explicitly because UTF-8 mode becomes the default in Python 3.15 |
| `env_minimal` | Everything is dropped except OS essentials, temporary folders and home variables |
| `home` | `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` and the XDG folders point at one new empty folder |
| `cwd` | The working directory has a space and Hangul in its name |
| `temp` | `TEMP`, `TMP` and `TMPDIR` have a space and Hangul in their name |
| `tz` | `TZ=AVT+11:30`, an offset no real zone uses, so it still moves the clock on a UTC machine |

A difference is a changed outcome, a test that disappears or appears, or a changed
skip. `skip_differences` lists the test id with both skip reasons, which catches a
test that only looks fine because it was skipped. Tests skipped in the control are
listed in `always_skipped`, as they prove nothing about any variation.

The all-on run also reports `variation_effect`, from a digest-only probe in the
child. A variation that changed nothing appears in `ineffective_variations`: its
quiet result is no evidence. `utf8_mode` is the common case, as it is a no-op when
the parent never set `PYTHONUTF8=1`. Run it where that value is set, as the CI
matrix does.

Limits:

- `PYTHONUTF8=0` does not reproduce a system code page. A runner keeps its own
  code page, so a CP949 failure needs a CP949 machine. For the output path, tests
  set `PYTHONIOENCODING=cp949` with `PYTHONUTF8=0` themselves, as
  `tests/test_skill_routing_cli.py` does. `-X warn_default_encoding` is the way to
  find implicit encodings.
- A full suite under every variation is deliberately out of reach. Choose the
  tests that touch the risk, and at most 500 tests are accepted after expansion.
- A timeout kills the child, not processes the test started.
- A quiet run covers the selected tests and these variations only.
