# Bilingual experience retrieval fixture

This small synthetic vault evaluates **source retrieval and returned data exposure**, not generated answers or successful prompt injection. It contains 16 English/Korean queries: 12 answerable queries and four questions whose requested facts are absent from the fixture. No private vault content or external service is used.

The paired scenarios cover current versus historical retention rules, an ordered startup workflow, a stale-lock gotcha, correction of a false network premise, and an imported memory fragment with malicious instructions. The fragment is deliberately searchable: `forbidden_paths` labels unwanted evidence, while the vault's `deny_zones` remain access restrictions. Evaluation never executes instructions found in source text.

No-answer questions ask for a liability cap or subscription price while sharing vocabulary with real retention/export notes. Lexical retrieval therefore returns related notes that do not contain the requested facts. These are genuine **fixture no-answer** cases; neither an empty result nor the fixture label establishes absence in the world. The original `tests/fixtures/recall` corpus and its thresholds remain unchanged.

## Labels and observations

`queries.json` must contain at least 12 query objects. Existing `id`, `query`, `language` (`en` or `ko`), and `expected_paths` fields remain required. Optional fields are:

- `expectation`: `answerable` (default) requires nonempty `expected_paths`; `no_answer` requires `expected_paths: []`. An empty label alone is rejected.
- `scenario`: a nonempty grouping name; defaults to `unspecified`.
- `stale_paths`: sources superseded for this question's timeframe. A historical question can legitimately expect a historical source instead.
- `forbidden_paths`: unwanted or untrusted sources for that labeled query. Exposure rates cover queries with explicit nonempty labels, not every possible malicious source in the corpus.

Paths must be unique literal relative `.md` paths using `/`, with no absolute paths, traversal, control characters, or Windows special characters. An expected path cannot also be stale or forbidden. File existence is deliberately not required: a missing expected source is a measurable recall failure. Labels never change what the retriever may access.

`recall_at_3` and `mrr` use **answerable queries only**, including in `by_language` and `by_scenario`. Groups without answerable queries report `null`, not perfect or zero recall. `no_answer.false_positive_rate` measures questions returning any source match; `complete_empty_rate` counts only empty results from complete successful scans. Incomplete empty scans have their own count and appear in `evaluation_errors`; they never count as complete empty observations.

`stale_sources` and `forbidden_sources` report both any returned-source exposure and first-ranked exposure. `top_1_stale` is a source-selection risk indicator, not evidence that an answer used it. Source matches include snippet metadata even with a zero context budget; exposure measures those returned matches, not only rendered context. `failed_queries` lists source misses, no-answer false positives, and labeled exposures; `reasons` identifies each issue.

Each `per_query` entry includes retrieval paths, the full search diagnostics, elapsed milliseconds for the recall call, and `estimated_returned_context_tokens`. Costs exclude module loading and fixture validation. Tokens use the retriever's Hangul/non-Hangul character coefficients and are **estimates**, not model tokenizer counts or billing measurements. `--max-tokens` (default 1500; nonnegative integer) controls rendered context; retrieval remains top three.

## Running and optional gates

```sh
python scripts/evaluate_recall.py
python scripts/evaluate_recall.py --fixture tests/fixtures/recall_experience
python scripts/evaluate_recall.py --fixture tests/fixtures/recall_experience --max-no-answer-false-positive-rate 0 --max-forbidden-exposure-rate 0 --max-stale-top-1-rate 0
```

The first command retains the original defaults: recall@3 >= 0.85, MRR@3 >= 0.75, and no incomplete/error scans. These are also the default gates for other fixtures. A default exit 0 does **not** certify no-answer behavior or source safety; exposure observations remain visible. All-no-answer fixtures have no answerable recall/MRR gate to apply.

The three optional maximum-rate gates make false positives, untrusted-source exposure, and stale first-ranked selection reviewable independently. Each enabled gate appears in `optional_gates` with `observed`, `maximum`, and `passed`. A gate without any applicable labels fails rather than passing vacuously. Rate thresholds must be finite values between 0 and 1. Exit codes: 0 for enabled gates passing, 1 for a metric/diagnostic gate failure, 2 for invalid options or unavailable evaluation.

With the current lexical ranker this fixture yields recall@3 **1.0**, MRR@3 **0.8333**, no-answer false-positive rate **1.0** (4/4), labeled forbidden exposure **1.0** (2/2), and stale first-ranked rate **1.0** (2/2). The second command exits 0 under the existing retrieval gates; the third exits 1. These intentionally visible limitations are not hidden by relaxed new thresholds or changes to ranking. Timing and estimated token totals depend on the run and context budget.
