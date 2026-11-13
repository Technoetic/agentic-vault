# Real recall support evaluation

Run the original synthetic evaluation with Python 3.10 or newer:

```console
python scripts/evaluate_recall_support.py --format json
python scripts/evaluate_recall_support.py --control naive_any_match
python -m unittest tests.test_recall_support_evaluation -v
```

JSON is also the default format. Exit status is `0` when every authored
visibility expectation and support decision agrees, `1` for a failed measurement
gate, and `2` for invalid arguments, fixtures or unavailable recall. The naive
control is expected to exit `1`: it calls the same real retriever and assessor,
but claims support whenever any lexical match exists. There is no user-vault,
dataset, model, command-execution or network option.

The fixture at `tests/fixtures/recall_support/pack.json` contains 20 original
cases. Expected visible requirement IDs, blockers and scope/output completeness
are authored separately from retrieval results. Each case gets a fresh temporary
vault, its own minimal config and its exact UTF-8 note bytes. The evaluator calls
the production `vault_recall.recall` and passes that result to `assess_support`;
no synthetic recall result is scored as a measurement. Temporary vaults are
removed when their case finishes.

The cases cover complete evidence; a strong lexical distractor containing the
right quote at the wrong path; a missing condition; an explicit blocker; a chain
distributed across three notes; a changed note/ranking order; a required line
that loses the per-document snippet selection; a required quote beyond the
500-character snippet; context omission and context truncation; and no lexical
match. Nine more cases put a fact at the start, middle or end of valid UTF-8
files of exactly 524287, 524288 or 524289 bytes. Multibyte filler makes a
character-count implementation fail. Required lines in positive multi-fact
controls are in separate notes because current recall selects one line per note.

## Read the measurements separately

- `retrieval.match_count` and `matched_paths` describe selected lexical matches.
  `context_matches`, `context_truncated` and `context_omitted` describe rendering.
- `assessment.observed_requirement_ids` counts only authored quotations actually
  visible in the corresponding emitted path and original line. Blockers and
  missing requirements remain separate. Scope completeness describes eligible
  search coverage; output completeness describes rendering of selected matches.
  Neither says every useful fact in a read file was emitted.
- `source_requirement_ids` is an independent direct inspection of the generated
  fixture bytes. It distinguishes absent fixture evidence from evidence that
  exists in the corpus but was lost before observation. It is never supplied to
  the assessor or used to invent missing retrieval evidence.
- `evidence_completeness` is observed requirements divided by all declared
  requirements across cases. An expected negative case can pass its expectation
  while still lowering this retrieval visibility metric.
- `false_assurance_count` counts support claims where authored expectations lack
  some requirement, show a blocker, or declare incomplete search/rendering.
  `false_assurance_rate` divides that count by the number of such unsafe-to-claim
  cases, rather than by all cases. This is a fixture decision diagnostic, not a
  probability that a real-world answer is wrong.

The assessor control claims support only with every requirement observed, no
observed blocker, complete scan scope and complete rendering. The naive control
uses only whether `matches` is nonempty. Both controls report identical actual
assessments, so a changed decision cannot be mistaken for improved retrieval.

## Recorded local result

On the authored pack, the assessor control matches all 20 expectations, with
17/27 required quotations visible (62.96%), 17/20 complete search scopes and
18/20 complete rendered outputs. Eight requirements present in fixture bytes
remain unobserved. The default control reports zero false-assurance cases; the
naive control produces seven among 11 unsafe-to-claim cases (63.64%) and fails
its gate. These counts include intentional negative controls and are not a
benchmark estimate for user vaults.

Files above 512 KiB remain wholly unread by current recall, including facts at
the beginning of the oversized file. Files at and below the limit are readable
at all three authored positions. These cases measure the existing boundary;
this change does not repair the limit, improve ranking or add retrieval.

Every case records configuration, file byte sizes and SHA-256 hashes, plus
separate bindings for the complete case, expected labels, contract and actual
context. These hashes enable deterministic comparison; they do not authenticate
the author or independently certify the labels. Repeated runs produce identical
reports without timing or temporary-path noise.

## Bounds and provenance

The loader reads only the bundled fixture, with a 256 KiB JSON ceiling, rejects
duplicate JSON fields and validates exact object fields. Direct Python packs
are also validated: at most 64 cases, 16 files and 2 MiB generated bytes per
case; each file is at most 524289 bytes; query, result and token budgets are
bounded. Fixture write paths must be portable relative Markdown paths below
`facts/`; traversal, drives, network paths, streams, device names, duplicate
case-insensitive paths and config replacement are rejected. No input text is
interpreted as a shell command or model instruction. Contract checking before
materialization uses the assessor with an empty, valid result solely for
validation; reported assessments always consume actual recall.

All fixture text and generators are original to this project. RULER-style
position/length coverage and NoLiMa-style lexical distractors informed the
evaluation questions only; no external dataset, benchmark code or published
paper score is copied. In particular, no NoLiMa code/data is included. These
are literal caller-declared evidence observations, without semantic truth,
authority, freshness, permission or real-world safety certification.
