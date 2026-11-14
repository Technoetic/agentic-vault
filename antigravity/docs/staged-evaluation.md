# Staged evaluation

`vault_staged_evaluation.py` is an opt-in, offline, read-only adapter over
[task contracts](task-contracts.md), [native verification receipts](evidence.md)
and the native coverage checker. The host runs checks, obtains reviews and
submits the observations. The adapter executes no method text, invokes no model,
makes no network call and writes no vault state.

```text
python vault_staged_evaluation.py --vault /path/to/vault evaluate --input input/staged.json
```

The Python interface is `evaluate(vault, report_path)`. `report_path` is a
vault-relative JSON path. CLI exit codes are `0` for `passed`, `1` for another
evaluated status and `2` for a content-free input/operational error.

## Input

The report has exactly these fields. Replace the illustrative identifiers and
hashes with the selected native receipts and SHA-256 of the actual source bytes.
The example is synthetic.

```json
{
  "version": 1,
  "contract_id": "<64 lowercase hexadecimal contract ID>",
  "evidence_id": "<32 lowercase hexadecimal native evidence ID>",
  "coverage_manifest": "input/coverage.json",
  "mechanical": [
    {
      "check_id": "C-001",
      "status": "passed",
      "source": {
        "path": "output/check.log",
        "sha256": "<64 lowercase hexadecimal SHA-256>"
      }
    }
  ],
  "reviews": [
    {
      "kind": "semantic",
      "status": "support",
      "acceptance_ids": ["AC-001"],
      "source": {
        "path": "output/semantic.md",
        "sha256": "<64 lowercase hexadecimal SHA-256>"
      }
    },
    {
      "kind": "independent",
      "status": "not_run",
      "acceptance_ids": ["AC-001"],
      "source": null
    }
  ],
  "independent_required": false
}
```

The corresponding native coverage manifest uses the existing version 1 schema:

```json
{
  "version": 1,
  "task": "T-001",
  "requirements": ["REQ-001"],
  "acceptance": [
    {"id": "AC-001", "requirements": ["REQ-001"], "checks": ["C-001"]}
  ],
  "required_checks": ["C-001"]
}
```

The contract's accepted acceptance IDs must exactly match the manifest's AC IDs,
and its task label must match the manifest and native evidence receipt. Native
coverage retains its `REQ-` and `AC-` ID conventions. Unknown mapping/check IDs,
duplicate records, extra fields, duplicate JSON keys, non-finite numbers and
boolean versions are rejected. Every review names the full fixed set of accepted
AC IDs; support for a different criterion set cannot substitute for it.

## Mechanical observations and reviews

Each declared mechanical record names one native required check. Its status is
`passed`, `failed`, `not_run`, `skipped` or `no_evidence`. An omitted record becomes
`no_evidence`. `passed` and `failed` require a source binding to a log actually
listed by that native check and included in the receipt's evidence bindings.
Other statuses require `source: null`.

A `passed` declaration needs a native `verified` result and current log bytes.
Native `failed` or `regression` results veto a pass even if a mechanical row is
missing, relabeled `passed`, or supported by both reviews. Native `gap`, an
unrecorded receipt, a missing check, and skipped/unexecuted checks stay
unverified. Every check mapped to every accepted criterion must pass; one passing
check cannot conceal another missing or failed check.

Exactly one `semantic` and one `independent` review record are required. Review
statuses are `support`, `withhold` or `not_run`. Support and withholding bind a
separate review file; `not_run` uses `source: null`. Semantic support is required
for a pass. Independent support is additionally required when the explicitly
declared `independent_required` flag is true. Either review's `withhold` blocks a
pass, including an optional independent review.

Review sources must be separate from the staged report, coverage manifest,
contract input/sources, native candidate, native report and native evidence logs.
Semantic and independent reviews cannot reuse the same source path. This checks
structural separation only: distinct files, role names and execution claims do
not authenticate reviewer independence or demonstrate actual execution. Model
support alone always remains unverified.

## Results and bounds

The result status is `passed`, `failed`, `unverified`, `blocked` or `stale`.
Staleness takes precedence; current failed checks then take precedence over
withholding or missing reviews. A failed criterion does not erase the separate
result of another criterion. Unresolved consequential contract questions block a
pass. Superseded contracts and changed contract sources are stale. Historical
criterion metadata is used only to display stale results and stable criterion
identity; it never becomes current acceptance authority. A corrupt or missing
contract has no historical accepted criteria.

Results include `contract_id`, `acceptance_ids`, `failed_acceptance_ids`,
`acceptance_results` with per-AC `check_ids`, `mechanical_results`, review records,
`criteria_fingerprint`, `contract_fingerprint`, `report_source`,
`source_bindings`, the native `coverage` result and a combined `fingerprint`.
Bindings use `{path, size, sha256}` and cover selected inputs, native receipt and
contract receipt bytes, and relevant public source files. A stale-contract
short circuit returns `coverage: null` and historical source metadata where
available. Recompute evaluation to check contract supersession; source bindings
alone do not capture that lifecycle. No submitted claim, method, observation or
review body is copied into the result.

`authority` is always `data_only`; `execution_verified` and
`semantic_fulfillment_verified` are always false. `passed` means all declared
current data satisfied the staged structural rules. It grants no permission and
does not independently establish task completion, command execution, source
truth or reviewer identity. Native receipts and hashes preserve their existing
declaration and checksum boundaries.

The adapter reuses native deny/exclude, path, worktree, symlink, hardlink,
credential-path, secret-value and stable-read rules. JSON inputs are limited to
64 KiB each, review sources to 256 KiB, selected mechanical logs to 1 MiB, and
unique staged inputs/logs/reviews to 2 MiB. Native evidence retains its existing
per-file and set bounds. There are at most 64 mechanical records and two review
records. Inputs, selected source bytes, policy, native receipts and contract
lifecycle are rechecked before a current pass can be returned. These checks are
point-in-time observations, not a workspace freeze.
