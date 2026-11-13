# Note capacity limits and growth checks

Several engine paths refuse or skip a note above a fixed byte size. The shared notes grow with every session, so a vault can cross a limit without any single change looking wrong. This page lists the limits, the read-only report that compares notes with them, the checkpoint read bound and the opt-in staged growth check.

## Limits

| Reader or writer | Limit | Constant | Past the limit |
| --- | --- | --- | --- |
| `vault_state` note read: `patch_note`, `project_handoff` and other callers of `read_note` | 256 KiB | `vault_state.MAX_BYTES` | `input_too_large` |
| `vault_state` result of an edit or projection | 256 KiB | `vault_state.MAX_BYTES` | `output_too_large` |
| `project_handoff` task items | 128 items | `vault_state.MAX_ITEMS` | `too_many_items` |
| Checkpoint source hash (PreCompact, SessionEnd) | 64 MiB | `vault_state.MAX_CHECKPOINT_SOURCE_BYTES` | `input_too_large` on the hook's stderr, exit code 0, no checkpoint written |
| Session start injection read | 256 KiB | `session_start.MAX_NOTE_BYTES` | The note's head is injected; Doctor reports `source_byte_limit` |
| `vault_recall` file | 512 KiB | `vault_recall.MAX_FILE_BYTES` | File skipped, listed in `oversized_files`, `search_complete` false |
| `vault_recall` one search | 16 MiB | `vault_recall.MAX_TOTAL_READ_BYTES` | Later candidates skipped, `search_complete` false |
| `vault_index_fold` input | 512 KiB | `vault_index_fold.MAX_FILE_BYTES` | Preview is `incomplete` with `input_too_large` |

An index over 512 KiB leaves search results and stops the fold preview that could shrink it, so an index has to be folded before it crosses that limit.

Raising one limit does not clear the path behind it. `project_handoff` reads its sources first, then counts items, then measures its output.

## Read failure codes

`vault_state` read failures keep two codes: `input_too_large` and `file_changed_during_read`. Every other source failure stays `unsafe_or_changed_source`, including a missing file, a hard link and an operating system error. The codes carry no path, size or note text.

The checkpoint hook always exits 0 and now names the failure on stderr, for example `agentic-vault: checkpoint unavailable (input_too_large)`. It prints a `vault_state` or evidence code and nothing else. Any other failure prints `unclassified_failure`, because other exception text can quote a path.

## Checkpoint read bound

A checkpoint stores hashes and never note bytes, so its sources no longer use the 256 KiB read bound. `write_checkpoint` fingerprints each source with the same stable read under the 64 MiB evidence file bound and keeps only the digest while reading. The recheck before the replace uses the same bound. The path policy, the deny zones and the 64 KiB bound on the checkpoint file are unchanged, and so is every other reader in the table above.

The opt-in `checkpoint_drift_check` keeps its own 1,000,000-byte read bound. A note above it reports `changed` without a hash, as [before](hardening.md#checkpoint-이후-바뀐-엔진-노트). Such a note now has a checkpoint, so with that check turned on it reports `changed` on every run.

## Capacity report

```console
python skills/agentic-vault/scripts/vault_doctor.py --vault /path/to/vault --capacity --format json
```

`--capacity` adds a `capacity` object to the Doctor report. Without the flag the report is unchanged. The check never changes the Doctor exit code and fails soft with `probe_failed`. It writes nothing, makes no network or model call and runs only local `git log` and `git cat-file`.

The targets are the configured `handoff_note`, `hot_note`, `log_note`, `tasks_note` and `index_note`. Each passes the same path policy as a state read: a target in a deny zone, an excluded folder or the runtime folder, a link and a hard link are named as `unsafe_path` and not measured. A target is compared with both the state and recall limits. The values are read from `vault_state` and `vault_recall`, so the report cannot drift from them.

| Field | Meaning |
| --- | --- |
| `status` | `ready`, `warning`, `fail` or `unavailable` |
| `reason_code` | `within_limits`, `near_byte_limit`, `forecast_within_horizon`, `source_byte_limit`, `note_path_unsafe`, `note_unreadable`, or a config code |
| `history` | `available`, `git_unavailable`, `git_timeout` or `git_history_unavailable`, or `not_read` when the check could not start and made no Git call |
| `complete` | True only when every enabled target was measured and history was read |
| `targets[].limits[]` | `limit_bytes`, `used_percent`, `status`, `estimated_days_to_limit` |
| `targets[].history` | `status`, `samples`, `window_days`, `slope_bytes_per_day`, `estimate` |

A limit is `over_limit` past its value, which matches the enforcing comparison: a file of exactly the limit is still accepted. It is `warning` at 80 percent or when the estimate is 14 days or fewer.

Without Git, or outside a repository, the report still gives usage and has no estimates. The first run failure stops further Git calls, each of which is bounded at 5 seconds.

### Estimate

`estimated_days_to_limit` follows the shape of [Prometheus `predict_linear`](https://github.com/prometheus/prometheus/blob/dd7aa6b0f19f4e952a55cbebd6e15458fe0ad9c6/docs/querying/functions.md) (commit `dd7aa6b0f19f`). `statistics.linear_regression` fits committed blob sizes against commit time, and the value is the bytes left under the limit divided by the slope. For each note, one `git log -n 40` and one `git cat-file --batch-check` supply the samples. No code is taken from Prometheus.

There is no estimate in these cases, and the reason is in `targets[].history.status`:

| Status | Cause |
| --- | --- |
| `insufficient_history` | Fewer than two commits with the note |
| `same_commit_time` | All commits have one timestamp |
| `not_growing` | The slope is zero or negative |
| `history_unreadable` | Git output did not match the request |

A limit already passed also has no estimate.

Treat the number as a prompt to look, not as a date:

- The 40 samples are a commit window, not a time window. A busy note covers a few days and a quiet one covers months. `window_days` shows which.
- A line follows the recent pace. A cleanup bends the slope down, and a burst of large commits bends it up.
- The current size is the working file, while the slope uses committed blobs. Line ending conversion or a clean filter makes the two differ.
- Commit time is what the committer's clock said.
- History follows the current path. A renamed note restarts its history.

## Staged token growth check

`vault_healthcheck.py --staged` can compare a budgeted note in the index with its version in `HEAD`. The check is off unless configured:

```json
{"staged_growth_check": "warn", "staged_growth_max_tokens": 500}
```

| Key | Values | Effect |
| --- | --- | --- |
| `staged_growth_check` | `off`, `warn`, `block`; absent means off | `warn` prints a `staged-token-growth` warning and the commit goes on. `block` stops the commit. |
| `staged_growth_max_tokens` | Integer of 0 or more; absent or 0 means no growth limit | Also reports a note inside its budget that grows by more than this many estimated tokens. |

Only `hot_note` with `hot_max_tokens` above 0 and `handoff_note` with `handoff_max_tokens` above 0 are checked. A note is reported when it grew and its staged size is over the budget, or when it grew past the growth limit. A note that shrinks or stays the same passes, so a vault already over budget can turn this on at once and is held only against further growth. This ratchet follows [Betterer](https://github.com/phenomnomnominal/betterer/blob/69a83c1497/website/docs/introduction.md) (commit `69a83c1497`), except that `HEAD` is the baseline: no result file exists and no code is taken from it.

- A renamed note is compared with its old path in `HEAD`.
- A new path has no baseline and passes, even over budget. The first commit of a note sets its baseline.
- Sizes come from `estimate_tokens`, the same approximation as the session budgets. It is deterministic, so one extra estimated token over budget is reported.
- If the baseline cannot be read, `warn` prints `staged-token-growth-unverified` and `block` stops the commit. The message does not repeat Git's error text.
- Moving text to another note passes, because the check is per path. It measures size and says nothing about what the text is worth.
- The check runs inside `validate_staged`, so it works from the copy of `vault_healthcheck.py` installed in a vault with no extension modules. A vault that keeps its own modified copy gets it only when that copy is merged by hand.

Start with `warn`, watch what it reports on your own vault, and switch to `block` as a deliberate choice. Under `block`, an over-budget note can only be committed at or below its `HEAD` size.
