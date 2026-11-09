# Memory preservation and configured revocation at egress

User authorization: implement the preceding research recommendations and publish a GitHub release (2026-11-09). The existing recommendation prioritizes relation-preservation checks and existing Hypothesis extensions, then a bounded multi-session poisoning pilot; general context-budget optimization remains exploratory. Routine implementation choices are settled here within that authorization.

## Intent and approach

Ship a small, explicit, source-bound extension rather than introducing a model, vector database or new production runtime. A bag of preserved numbers/names cannot establish that quantities still belong to the same subjects. Configured revocations must also be honored where selected vault context is handed to Jev or Jarvis generation, with the original filtering policy rechecked immediately before dispatch.

Considered: generic automatic semantic extraction (too broad and no verified language model contract); another global exact-phrase requirement (already present); authored anchored-record probes (chosen: unique scope, current-source binding, conservative observable differences). For poisoning, an evaluator-only claim would leave the two identified egress readers unchanged; implement those real readers and separately label the generated lifecycle evaluator's simulated sink.

## T1: authored record probes

- `check_preservation(..., relation_probes=None)` and `check_compile(..., relation_probes=None)` preserve legacy results exactly when omitted.
- A manifest is schema1 with 1..64 probes. Each probe: bounded unique `id`, nonempty literal `anchor` <=256 characters with no controls/newlines, `scope` equal to `line` or `paragraph`. Compile probes additionally name `source_path` and `target_path`, restricted to already selected hash-bound roles. No discovery or caller regex.
- Source locator must select exactly one body record; absent/ambiguous source is invalid input. Candidate absent/ambiguous or different canonical record requires review. Count records, not occurrences of the anchor within one record. Compare whole selected record, not independent tokens; normalize NFC and line endings, optionally strip boundary indentation/trailing horizontal whitespace only as documented. Do not casefold/NFKC meaningful unit/case distinctions.
- Report hashes/counts/reasons, not unnecessary raw source text. This is scoped lexical preservation, never semantic truth or edit permission; paraphrases and exceptions outside the selected scope are not verified.
- Expose optional request field for compile-check and a bounded policy-checked JSON sidecar for proposal `preserve-check`. Reuse existing pre/post policy and original/candidate hash guards.

## T2: configured revocation and lifecycle tests

- Reuse `RevocationPolicy` and only its configured opt-in scope. Filter complete raw UTF-8 source text before Jev BOM/CRLF normalization or Jarvis prefix truncation; keep original raw hash metadata.
- Jev excerpt binding must use the projected source. Carry the same policy plus source/config bindings into the pre-request and post-response rechecks. A stale/malformed configured policy must not authorize an unfiltered fallback or a fabricated success.
- Jarvis context construction must carry an envelope containing the exact policy/source bindings that created the prompt. Recheck that envelope immediately before `run_with_stdin` and after completion. Preserve the existing default string prompt API/bytes when no revocation is configured; do not reassemble a fresh policy after generation and call that proof of the earlier prompt.
- Extend Hypothesis with generated revoke/declared-derivative/rebuild/fresh-retrieve/prepare/simulated-send sequences. Inspect actual current consumers. Include benign retention, stale evidence, no-observation and an unfiltered negative control. There is no persistent engine index or real outbox: call these fresh reads/rebuilt derived context and a simulated sink. Already-sent information cannot be withdrawn; unregistered semantic copies are not automatically discovered.
- Synthetic inputs only; no production DB, company context or live model/API calls in tests/evaluators.

## T3: paired validation and release

- Add a small public synthetic relation fixture pack with independently authored expected outcomes. Compare legacy literal observations and record probes on the same immutable original/candidate pairs; keep confusion counts, scope/pack fingerprint and explicit limits. No model-score-as-truth or transferred paper-performance claim.
- Reuse existing Hypothesis dev dependency; run new state machines in required CI rather than silently relying on skip-only unit jobs. Default runtime remains stdlib.
- Add documentation and clean-room CLI examples. Cite pinned public papers and upstream licenses without copying unlicensed research code/data. Keep general budget optimization and 512KiB streaming redesign out of this release.
- Target v0.44.0 unless remote state reserves that tag. Update version/package/native inventory metadata, run focused and full checks, independently review code and public assets, publish only exact tested commits, immutable release ZIP/checksums/verification with honest fresh results, and update the published reference registry with pinned public use evidence. No active installation or company-vault settings migration is part of this task.

## Acceptance boundaries

Quantity swaps, appended negation, removed multiline exception, duplicated source wording elsewhere, unselected paths, malformed manifests and Unicode case distinctions must have meaningful tests. Config-disabled outputs stay unchanged. UTF-8 BOM/CRLF and a change between prepare/dispatch must not bypass revocation. Missing observations never count as safe success. Public claims name only the scopes actually executed.
