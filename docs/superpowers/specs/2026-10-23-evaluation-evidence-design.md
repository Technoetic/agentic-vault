# Evaluation identity and improvement cost evidence

The user authorized implementation of the first two recommendations from the RSI review. Extend the existing opt-in tools, keeping ordinary runs, hooks, approvals and model/network behavior unchanged. Python 3.10+ standard library only. No deployment or active installation is included.

## Approach

Extend native runs with an optional evaluator binding. Keep cost accounting in a separate opt-in module that reuses the native store, path policy, locks and immutable receipts. Replacing the v1 run schema outright would invalidate existing receipts; adding an autonomous runner would exceed the requested scope.

## Evaluator binding

- `vault_runs.prepare(..., evaluator_sources=None, evaluator_config=None)` and CLI `--evaluator-source PATH` (repeatable), `--evaluator-config PATH` (optional JSON).
- With no new arguments, manifest, IDs and existing CLI/API outputs remain unchanged.
- When selected, an optional `evaluator` manifest object binds a canonical sorted list of code/support file fingerprints and an optional JSON configuration fingerprint. At least one source is required. All files are explicit allowed vault-relative UTF-8 snapshots; no external paths or source execution.
- Evaluator files must not overlap candidate/prompt treatment files, fixture/tools, report or observation evidence. Existing path, size, secret and currentness rules apply. Combined evaluator bytes are bounded by the existing 2 MiB limit, at most 64 selected files; config is at most 64 KiB.
- Inspection and recording revalidate evaluator snapshots. Comparisons require identical evaluator binding, including rejecting bound/unbound pairs. Old unbound pairs stay compatible.
- Regression tests cover evaluator code/config drift, different evaluator comparisons, treatment overlap, missing/unsafe paths and a candidate/report that claims success after evaluator mutation. Hashes do not attest execution, evaluator correctness, identity or log truth.

## Cost receipts

- Add `vault_run_costs.py`: `record(vault, run_id, report)`, `inspect(vault, cost_id)`, `summarize(vault, cost_ids)` and `compare(vault, baseline_cost_id, candidate_cost_id)`, with corresponding CLI subcommands. Reporting never runs commands/models or reads provider accounts.
- A cost report has exact keys `version:1`, `run_id`, `scope_complete` (boolean declaration), `entries` (1..64). Each entry has `id`, `phase` (`generation|evaluation|selection|recording`), `status` (`complete|error|discarded`), `observation` (`instrument_log|host_report|unknown`), `recorder` (safe label), `evidence` (explicit relative paths), `metrics` (exactly `input_tokens`, `output_tokens`, `calls`, `elapsed_ms`, `cost_microusd`, each nonnegative integer up to 10^15 or null), and `cost_basis` (`billed|estimated|unknown`). Unknown observations have null metrics and no evidence; known observations require evidence. A non-null cost requires billed/estimated basis; unknown cost requires unknown basis. Booleans and floats are not numbers.
- Record binds the native prepared receipt, selected report and referenced evidence snapshots. Use content-derived cost ID and immutable `run-cost-<id>.json` receipt through the existing Store and advisory locks. Native run may still be prepared, so failed/unfinished attempts can retain costs; performance comparisons still require native completed runs and the ordinary compatibility/hard-failure checks.
- Canonicalize entries by ID and evidence by path. Reject duplicate IDs, duplicate evidence within or across entries, self/native-input evidence, reserved/secret/unsafe paths, oversized reports/sets and malformed data. Preserve error/discarded entries in totals.
- Inspection revalidates report, evidence and native prepared receipt, and recomputes all normalized data. Neither recomputed checksums nor path labels alone may sever the report-to-normalized-data link.
- Summary explicitly selects unique cost IDs (max 64), rejects duplicate native run IDs and shared evidence to avoid obvious double counting. No directory scan. Totals are available only where all selected entries have a value for that metric; partial known subtotals and unknown counts are separate. Missing phases and `scope_complete:false` are visible. Full scope is a host declaration, not discoverable proof that no attempt was omitted.
- Comparison returns ordinary native performance evidence plus reported resource deltas. Cost comparison is available only when both reports declare complete scope, include all four phases and have no unknown observations/metrics. Cost comparisons require matching per-entry cost basis across both reports (all billed or all estimated); mixed or unequal bases remain inconclusive. No automatic efficiency or application decision. Instrument-log origin and recorder names are caller descriptions, not authenticated metering. Declared token limits are not actual limit enforcement.

## Validation and scope

Write failing tests before feature code; retain red logs. Exercise APIs and CLIs, stale inputs, tampering, malformed/secret/oversized evidence, unknown versus zero, failed costs, strict old-run compatibility, and all-role improvements with bound evaluators. Add a synthetic offline demonstration and document the exact trust limits. Run affected tests, required existing offline gates, package validation and the complete suite. A read-only reviewer reruns the new tests. No real-model performance or savings claim follows from synthetic tests.

Heldout exposure tracking, ignition probes, cross-plan ancestry, candidate generation and automatic promotion are deferred.
