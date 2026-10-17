# Hangul syllable-bigram BM25 channel — 2026-10-17

Opt-in `lexical_ngrams="hangul2"` channel for open limit #3, implemented on branch
`feat/v023-hangul2-bm25` from v0.22.1 (`63b4cc9`) and measured at
`46ed6852ff10fb493a84e40970ce4b4aedac29c4`. This work is unreleased. Defaults, CLI default
output and every tests.yml command and gate are unchanged. See
[usage and limits](../memory-patterns.md) and the
[machine-readable observations](hangul2-bigram-20261017.json).

## What changed

- `vault_retrieval.retrieve()` and `vault_recall.recall()` take keyword-only
  `lexical_ngrams=None`. Only `None` and `"hangul2"` are valid; any other value returns
  `invalid_options` before any note is read.
- `"hangul2"` builds a second BM25 view of the same eligible notes in memory for each
  call. Inside each word token, every maximal run of precomposed Hangul syllables
  (U+AC00-U+D7A3) of two or more syllables becomes overlapping syllable bigrams; a
  one-syllable run stays a unigram; non-Hangul segments keep the existing word-token text
  (`handoff를` gives `handoff` and `를`). Query terms are the bounded word terms, after
  explicit expansion when it is on, split the same way and capped at 128 n-grams.
- Its rank is added to RRF (k=60) as `rrf_bm25_hangul2` for the `bm25` and `hybrid`
  backends. With the channel active, `bm25` ranks by the RRF sum instead of the raw BM25
  score. The `lexical` backend has no BM25 rank, so it ignores the channel; diagnostics say
  `ignored_lexical_backend` and `vault_recall.py` text output prints a stderr note.
- A candidate with a bigram BM25 score but no evidence line from the active backend (no
  exact-token BM25 score under `bm25`, no lexical match under `hybrid`) cites the line chosen
  by the existing BM25 passage rule with bigram tokens. Such a candidate may also hold an
  external candidate rank, so it is not necessarily found by the bigram rank alone. All other
  evidence lines are unchanged.
- Diagnostics gain `lexical_ngrams` and `lexical_ngram_channel` (status, RRF key,
  tokenizer, n-grams used and omitted, candidate count, `in_memory_per_call`), and the
  ranking string names the mode, only when a mode is requested. Nothing is persisted.
- CLIs: `vault_recall.py --lexical-ngrams hangul2`; `evaluate_recall.py --lexical-ngrams
  hangul2`, which exits 2 unless `--advanced` or `--compare` is also given.

## Default equivalence

| Check | Observed result |
|---|---|
| Golden unit test | `DefaultPathEquivalenceTests` pins 7 configurations (64, 64, 12, 12, 16, 16 and 16 queries) on LF-normalized fixture copies to digests produced by the unmodified v0.22.1 retriever before any runtime edit. The copies byte-match the committed git blobs; floats are compared at 10 significant digits for cross-platform libm differences. Passes with `lexical_ngrams=None`. |
| Same-machine exact JSON | 12 retrieval configurations on recall_hard, recall_advanced and recall produced byte-identical JSON at `63b4cc9` and `46ed685` with the argument omitted. |
| tests.yml evaluation commands | All 7 retrieval-related commands kept exit code 0, empty stderr and the same stdout JSON after removing only measured timing keys (`elapsed_ms`, `elapsed_seconds`, `mean_elapsed_ms`, `retrieval_elapsed_ms`, `total_elapsed_ms`). The tool-safety output was byte-identical. |
| Mutation checks (temporary, restored) | A `None` diagnostics key on the default advanced path failed 6 of 7 golden configurations; switching the default `bm25` score to RRF failed 3; citing bigram-only candidates with word tokens failed 2 tests; leaving mixed-script words unsplit failed 5 tests. |

## Measurement

Fixture `tests/fixtures/recall_hard`: 64 queries (32 ko, 32 en; 56 answerable, 8 no-answer;
10 with stale labels) over 27 notes. Scenarios as labeled by the fixture: particles 12,
short 12, mixed 12, spacing 12, update 8, abstention 8. The main arms ran the tests.yml
options `--compare --expand-query --query-mapping 00-meta/query-map.txt --max-tokens 1500
--max-no-answer-false-positive-rate 0.25 --max-stale-top-1-rate 0` at `--top-k 3` and
`--top-k 5`; every arm ran once without and once with `--lexical-ngrams hangul2`. The
tests.yml commands pass no `--backend`, so they use `lexical`, which ignores the channel. Arms with
`--backend bm25` and `--backend hybrid` measure the channel under the same options.
Supplementary arms drop `--expand-query` and `--query-mapping` to separate the channel from
the fixture's explicit aliases and mapping. Values are for the improved arm, measured on
Windows 11 with Python 3.12.10 on 2026-10-17. p-values come from `paired_randomization`
(seed 1707, 10,000 permutations, two-sided, add-one) over the 56 answerable queries paired
by id. An LF-normalized copy of the fixture returned the same paths in the three top-5
expanded arms.

Arrows read without → with the channel. `exit` is the evaluator exit code without/with.

| Arm | Channel active | recall@k | nDCG@5 | MRR@k | claims in context | p recall / nDCG | no-answer FP | stale top-1 | exit |
|---|---|---|---|---|---|---|---|---|---|
| top3 lexical (tests.yml) | 0/64 | 1.0000 → 1.0000 | 0.9803 → 0.9803 | 0.9732 → 0.9732 | 56/56 → 56/56 | 1.0000 / 1.0000 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top5 lexical (tests.yml) | 0/64 | 1.0000 → 1.0000 | 0.9803 → 0.9803 | 0.9732 → 0.9732 | 56/56 → 56/56 | 1.0000 / 1.0000 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top3 bm25 | 64/64 | 1.0000 → 1.0000 | 0.9948 → 0.9845 | 0.9911 → 0.9732 | 34/56 → 34/56 | 1.0000 / 0.5053 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top5 bm25 | 64/64 | 1.0000 → 1.0000 | 0.9948 → 0.9845 | 0.9911 → 0.9732 | 34/56 → 34/56 | 1.0000 / 0.5053 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top3 hybrid | 64/64 | 1.0000 → 1.0000 | 0.9988 → 0.9939 | 1.0000 → 0.9911 | 56/56 → 56/56 | 1.0000 / 1.0000 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top5 hybrid | 64/64 | 1.0000 → 1.0000 | 0.9988 → 0.9939 | 1.0000 → 0.9911 | 56/56 → 56/56 | 1.0000 / 1.0000 | 2/8 → 2/8 | 0/10 → 0/10 | 0/0 |
| top3 bm25, no expansion | 64/64 | 0.5714 → 0.7500 | 0.4506 → 0.6385 | 0.4375 → 0.6429 | 9/56 → 15/56 | 0.0017 / 0.0003 | 2/8 → 2/8 | 0/10 → 0/10 | 1/1 |
| top5 bm25, no expansion | 64/64 | 0.5714 → 0.7500 | 0.4506 → 0.6385 | 0.4375 → 0.6429 | 9/56 → 15/56 | 0.0017 / 0.0003 | 2/8 → 2/8 | 0/10 → 0/10 | 1/1 |
| top3 hybrid, no expansion | 64/64 | 0.6786 → 0.8571 | 0.5621 → 0.7258 | 0.5625 → 0.7321 | 10/56 → 16/56 | 0.0017 / 0.0017 | 2/8 → 2/8 | 0/10 → 0/10 | 1/1 |
| top5 hybrid, no expansion | 64/64 | 0.6964 → 0.8750 | 0.5712 → 0.7348 | 0.5670 → 0.7366 | 11/56 → 17/56 | 0.0017 / 0.0017 | 2/8 → 2/8 | 0/10 → 0/10 | 1/1 |

The supplementary arms exit 1 in both runs because each run misses at least one of the
evaluator's default recall@3 ≥ 0.85 and MRR@3 ≥ 0.75 gates; the channel does not cause
those exits. The two
no-answer false positives are the same in every arm: `en-abstention-04` (switchgear
director salary) and `ko-abstention-04` (격리절차 담당자 급여), both already matched by
exact tokens. Stale exposure was 0/10 in every arm.

Per category, top 5 (answerable n). With the tests.yml options recall@5 stayed 1.0000 in
every scope for both backends, so only nDCG@5 is shown there.

| Scope (n) | bm25 nDCG@5 (p) | hybrid nDCG@5 (p) |
|---|---|---|
| particles (12) | 1.0000 → 1.0000 (1.0000) | 1.0000 → 1.0000 (1.0000) |
| short (12) | 1.0000 → 1.0000 (1.0000) | 0.9943 → 0.9957 (1.0000) |
| mixed (12) | 1.0000 → 1.0000 (1.0000) | 1.0000 → 1.0000 (1.0000) |
| spacing (12) | 0.9758 → 0.9275 (0.5063) | 1.0000 → 0.9758 (1.0000) |
| update (8) | 1.0000 → 1.0000 (1.0000) | 1.0000 → 1.0000 (1.0000) |
| ko (28) | 1.0000 → 0.9896 (1.0000) | 1.0000 → 1.0000 (1.0000) |
| en (28) | 0.9896 → 0.9793 (1.0000) | 0.9975 → 0.9878 (1.0000) |

Without expansion (supplementary, top 5):

| Scope (n) | bm25 recall@5 | bm25 nDCG@5 | p recall / nDCG | hybrid recall@5 | hybrid nDCG@5 | p recall / nDCG |
|---|---|---|---|---|---|---|
| particles (12) | 0.2500 → 0.7500 | 0.1447 → 0.6034 | 0.0307 / 0.0307 | 0.2500 → 0.7500 | 0.1447 → 0.6034 | 0.0307 / 0.0307 |
| short (12) | 1.0000 → 1.0000 | 0.7480 → 0.7480 | 1.0000 / 1.0000 | 1.0000 → 1.0000 | 0.7810 → 0.7527 | 1.0000 / 1.0000 |
| mixed (12) | 1.0000 → 1.0000 | 0.7894 → 0.8740 | 1.0000 / 0.2550 | 1.0000 → 1.0000 | 0.8223 → 0.8218 | 1.0000 / 1.0000 |
| spacing (12) | 0.0833 → 0.0833 | 0.0874 → 0.0874 | 1.0000 / 1.0000 | 0.6667 → 0.6667 | 0.5844 → 0.5844 | 1.0000 / 1.0000 |
| update (8) | 0.5000 → 1.0000 | 0.5000 → 1.0000 | 0.1263 / 0.1263 | 0.5000 → 1.0000 | 0.5000 → 1.0000 | 0.1263 / 0.1263 |
| ko (28) | 0.4643 → 0.8214 | 0.3534 → 0.7291 | 0.0022 / 0.0004 | 0.6429 → 1.0000 | 0.5313 → 0.8705 | 0.0022 / 0.0022 |
| en (28) | 0.6786 → 0.6786 | 0.5479 → 0.5479 | 1.0000 / 1.0000 | 0.7500 → 0.7500 | 0.6112 → 0.5991 | 1.0000 / 1.0000 |
| ko-particles (6) | 0.0000 → 1.0000 | 0.0000 → 0.9173 | | 0.0000 → 1.0000 | 0.0000 → 0.9173 | |
| ko-update (4) | 0.0000 → 1.0000 | 0.0000 → 1.0000 | | 0.0000 → 1.0000 | 0.0000 → 1.0000 | |
| en-particles (6) | 0.5000 → 0.5000 | 0.2894 → 0.2894 | | 0.5000 → 0.5000 | 0.2894 → 0.2894 | |

Language-by-scenario cells for every arm, and the top-3 arms, are in the JSON.

## Interpretation

On this fixture the explicit aliases and query mapping already resolve the particle,
short and mixed queries, so with the tests.yml options the channel added nothing: recall
stayed 1.0000 and nDCG@5 fell slightly (bm25 −0.0104, p 0.5053; hybrid −0.0049, p 1.0).
Without expansion it recovered the Korean particle and update queries (ko-particles 0/6 →
6/6, ko-update 0/4 → 4/4 at top 5) and left English recall unchanged. The particles
scenario (12 queries, p 0.0307) and Korean overall (28 queries, p 0.0022) changes were
significant at 0.05; the update scenario (8 queries, p 0.1263) was not. Spacing queries of
single syllables did not benefit. Observed precision costs:

- Unrelated bigram overlaps add candidates. Without expansion, `실패복구은` also returned
  both Cedar recovery notes through `복구`, and `경보확인은` also returned the failed-queue
  note through `확인` in `확인한다`. These queries carry no `as_of`, so the dated old Cedar
  note was not filtered; they have no stale labels.
- The bigram view changes BM25 length normalization. For `en-spacing-01` and
  `ko-spacing-01` (bm25, expansion on) the two views ranked the procedure note and its
  overview 1/2 and 2/1, the RRF sums tied at 1/61 + 1/62, and the existing path tie-break
  put the overview first.

No default change follows from this measurement.

## Reference only

Apache Lucene [LUCENE-8231](https://issues.apache.org/jira/browse/LUCENE-8231) (2018,
HANTEC) reported BM25 MAP 0.1044 with the standard analyzer, 0.1924 with CJK bigrams and
0.2094 with the Nori morphological analyzer; the same case grew the index from 131MB to
164MB. These are the source's reported values as relayed in the task brief. They were not
re-measured or re-fetched here and are not comparable with the fixture numbers above.

## Tests

| Command | Observed result |
|---|---|
| Baseline `python -m unittest discover -s tests` at `63b4cc9` | Ran 1375 tests in 305.866s; OK (skipped=32). |
| `python -m unittest tests.test_hangul_bigram_retrieval -v` | Ran 25 tests in 10.627s; OK; exit 0. |
| Related modules (`test_vault_retrieval`, `test_recall_hard_evaluation`, `test_recall_evaluation`, `test_vault_query_expansion`, `test_vault_recall`) | Ran 123 tests in 16.224s; OK (skipped=5); exit 0. |
| Final `python -m unittest discover -s tests` | Ran 1400 tests in 371.283s; OK (skipped=32); exit 0. Python 3.12.10; `hypothesis` is not installed, so the stateful lifecycle test is among the skips. |
| `python -m compileall -q hooks skills/agentic-vault/scripts scripts` and `git diff --check` | Exit 0 for compileall, `git diff --check` and `git diff --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD`. |

## Handoff

- task_id: `av-hangul2-bigram-20261017`.
- artifact_paths: `skills/agentic-vault/scripts/vault_retrieval.py`, `skills/agentic-vault/scripts/vault_recall.py`, `scripts/evaluate_recall.py`, `tests/test_hangul_bigram_retrieval.py`, `docs/memory-patterns.md`, this record and its JSON.
- verification_commands_and_results: the tables above; the JSON lists every measurement command per arm.
- assumptions: fixture labels are the relevance truth; the channel is judged against the same options without it, not against a morphological analyzer.
- unresolved: no default change is proposed; an independent re-run by another session is pending.
- next_safe_action: keep the default off; before any default change, measure on a larger Korean corpus with particle-attached and no-answer queries and compare with explicit alias/mapping expansion.
- verified_by: not yet independently verified.
