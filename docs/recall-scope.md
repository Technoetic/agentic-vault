# Recall read scope

`vault_recall.py` reads Markdown notes under fixed budgets. On a large vault the
reader can stop before it reaches notes that matter. This page describes the two
optional settings that widen or steer the read, and the diagnostics that say what
was not read. Without the `recall` block the reader, the ranking and every limit
behave as before.

## Optional settings

Add a `recall` object to `00-meta/vault-config.json`. Neither key is in the
template, and `vault-upgrade` does not add them.

```json
{"recall": {"max_total_read_bytes": 67108864,
            "priority_paths": ["50-projects/atlas/decisions.md", "50-projects/atlas"]}}
```

| Key | Value | Effect |
|---|---|---|
| `recall.max_total_read_bytes` | Integer from 512 KiB (one file) to 1 GiB. Absent: 16 MiB | Replaces the total read budget of one recall call |
| `recall.priority_paths` | Up to 32 vault-relative file or folder paths without a trailing slash, 1,024 characters each. Absent: none | Reads the covered notes first and prefers them in score ties |

A wrong type, an out-of-range number, an unknown key inside the block, or a priority
path that is unsafe or falls under a deny or exclusion rule stops recall with
`status=invalid_config` and the omission `recall_config_invalid`. The error names no
path and no value. `vault_links.py` reports the same status through recall.
`vault_healthcheck.py` does not read the block.

### `recall.max_total_read_bytes`

The same value governs every read of one call: the lexical reader, the advanced
retrieval reader, source rechecks, query-mapping reads and the evaluator's note
scan. `diagnostics.limits.total_read_bytes` shows the value in force.

The per-file limit (`MAX_FILE_BYTES`, 512 KiB) is not a setting. Raising it would
let large logs take result slots, would spend the total budget sooner on large files
so the reader stops earlier, and would shrink link proposals. A note
over that limit is therefore still skipped, so a vault that holds such a note keeps
`search_complete=false` however large the total budget is, and features that require
a complete inventory, such as `--expand-query` aliases, stay off there.

Read time and memory grow with the bytes read. Advanced retrieval holds the note
text in memory for the whole call. Raise the budget only as far as the vault needs.

### `recall.priority_paths`

An entry is a path prefix from the vault root, compared by whole segments and without
regard to letter case. `core` covers `core/a.md`, not `src/core/a.md` and not
`corehouse/a.md`. Earlier entries come first. An entry that matches nothing is harmless.

Priority acts in two places only:

- **Read order.** Covered notes move to the front before the file-count cut and the
  byte budget apply, so they are read even when the budget stops the run.
- **Tie-break.** Among equal scores, covered notes sort ahead of the path order that
  decides otherwise. This includes the per-channel ranks that feed RRF.

A priority path never lifts a note over a higher score, never adds a note that a deny
zone, `exclude_dirs` or `--exclude-source` removes, and never grants trust. A
configured list is reported as a count in `diagnostics.limits.priority_paths`, and
advanced `diagnostics.ranking` says that ties use it. Whether a given list improves
results on a given vault is not measured here: compare it on labeled queries with
[`evaluate_recall.py`](memory-evaluation.md).

## Scope diagnostics

Every recall result carries three keys in `diagnostics`. They are diagnostics only:
matches, ranking and `context` do not depend on them, and no result is hidden.

| Key | Meaning |
|---|---|
| `omitted_paths_sample` | Up to 10 candidate paths that did not reach the result, in path order and evenly spaced over the whole omitted list, so the end of the list is represented |
| `scope_state` | `complete` when `search_complete` is true, otherwise `partial` |
| `answer_state` | `matches`, `partial_matches`, `no_match_in_scope` or `not_enough_scope` |

`answer_state` is a derived label, not a new judgement. It combines `search_complete`
with whether anything was returned:

| | Returned something | Returned nothing |
|---|---|---|
| `search_complete=true` | `matches` | `no_match_in_scope` |
| `search_complete=false` | `partial_matches` | `not_enough_scope` |

`no_match_in_scope` means only that the words were not found in the eligible notes.
It does not show that the vault lacks the fact, and scores carry no sufficiency
threshold. `not_enough_scope` means an empty result proves nothing, because some of
the scope was not searched. On an unavailable result (`status` other than `ok`) read
the status first: the labels then report `partial` and `not_enough_scope`.

`omitted_paths_sample` counts a candidate as omitted when it was skipped by the file
count, the total budget, the file limit, or a read, safety or revocation failure, and
when advanced retrieval read and ranked it but dropped it at the source recheck. It
excludes what is not an omission: deny zones, exclusions and notes rejected by
`--as-of`. Notes beyond a directory-entry limit have no known path, so that omission
shows only in `omissions`. A failed or policy-changed run lists no path. The sample
holds vault paths, so remove it before sharing a report.

The counters `omitted_total_byte_limit`, `omitted_file_limit` and `skipped_oversized`,
the bounded `oversized_files` list and `omissions` are unchanged.

## Interaction with other limits

`vault_links.py` keeps its own binding budget of 16 MiB. That budget pays for reading
the source and the proposed targets, and its reserve is a multiple of the per-file
limit. Raising `recall.max_total_read_bytes` changes neither, so proposal counts stay
the same. `tests/test_recall_scope.py` holds the inequality that keeps ten proposals
affordable at the unchanged file limit.

Both settings change what an evaluation sees. When the block has any setting,
`vault_snapshot_sha256` of `--abstention-calibration` binds it, and `vault_snapshot`
adds `total_read_bytes` and `priority_path_count`, so a threshold calibrated under
other settings no longer matches. The configured paths themselves are not printed.
Without the block the digest keeps its definition. Gold-set label listing and the
twin note scan use the same read order and budget as recall.

## Sources

The split between "not searched" and "searched and absent", and the shape of a bounded
sample of what was skipped, follow these public sources. Only the ideas are used; no
code is copied.

- Sourcegraph, `Skipped` in
  [`internal/search/streaming/api/types.go`](https://github.com/sourcegraph/sourcegraph-public-snapshot/blob/c864f15af264f0f456a6d8a83290b5c940715349/internal/search/streaming/api/types.go),
  archived snapshot, commit `c864f15af264` of 2024-08-22: a skip reason with a count and
  a sample of the missing items. The snapshot declares no clear license, so only the
  structure is referenced.
- Zoekt, `Stats` in
  [`api.go`](https://github.com/sourcegraph/zoekt/blob/a5a933bc043e/api.go), commit
  `a5a933bc043e` of 2026-11-03, Apache-2.0: shards skipped because a query was canceled
  are counted apart from shards that a filter proved to have no match.
