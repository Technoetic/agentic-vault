# Memory hardening and durable collaboration

## Purpose and completion

Extend the existing selected memory patterns with the complete D1–D4/P1–P6 brief. The user requested execution after a read-only audit. Completion includes implemented command paths, meaningful regressions, independent review, local integration and installed-resource/runtime checks. External-provider credentials, corporate-data migration, actual-vault semantic indexing and remote publication are separate operational actions; absence of a provider must remain explicit rather than fabricated.

## Invariants

- Python 3.10+ standard library core; Markdown/Git remains the source of truth.
- Preserve existing valid lexical defaults, deny/exclude policies, byte/token/host limits and non-vault silence.
- Optional tools/models/tokenizers remain opt-in. No dependency or implicit model call is added to startup, compaction or session end.
- Existing user content, identifiers and activation scopes remain intact. New writes bind source hashes and policy; reports and frontmatter do not grant authority.
- New lint findings start as warnings. Explicit promotion requires a reproducible benign corpus with zero false positives and matching checker/corpus hashes.
- Cooperating writers use exclusive locks with owner tokens, PID and UTC time, bounded waits and conservative TTL/mtime recovery. Never use Windows os.kill(pid, 0).
- Do not send original vault material, credentials, synthetic credential patterns or complete conversations to external services.

## D1: shared sensitive-input normalization

Extend the existing 검사 normalization only: NFKC, Cf, the four Hangul filler code points, explicit tag and variation-selector ranges. Preserve original text and current raw-plus-normalized checks. Never discard all Lo/Mn or reject every Cn character. Regress four token families across four fillers, ordinary Korean, accents, emoji selectors and positive controls at both detection and final payload boundaries.

## D2: compaction constraints

Add a separate SessionStart compact group without healthcheck. Parse only bounded hook stdin; direct/non-vault invocations remain safe and silent. Prepend a bounded, source-bound fixed constraint block before budgeted hot/handoff for compact, using explicitly configured task/rule notes or deterministic handoff sections. Preserve total 9,500 UTF-16 output units and zero-budget semantics. Keep startup/clear/resume bytes unchanged. Plugin hooks deploy with plugin installation; vault-upgrade handles only vault-owned engine assets. Report actual host event support separately from fixture execution.

## P1/D4: measurement and deterministic query expansion

Add hard Korean-English labels for particles, short terms, mixed language, spacing, temporal update and abstention. Report returned-context claim coverage and nDCG@5 with a configurable top-k, language/scenario denominators, costs and reproducible paired randomization. Keep existing evaluations compatible. Opt-in query expansion consumes bounded frontmatter aliases and a vault-owned plain mapping file, follows policy and reports every expansion; aliases never change wiki-link destination identity. The original scan remains an ablation baseline.

## D3: warning extensions

Validate heading/block anchors, distinguish duplicate filenames by allowed relative paths, and warn on invalid/empty created/updated dates. Resolve explicit paths and deterministic unique names; do not invent destinations or read denied files. Keep existing fatal gates unchanged. Warn on ambiguous unqualified references and avoid treating correctly path-qualified references as errors. Test harmless fixtures, fences, Unicode headings, escaped pipes, missing anchors and invalid dates.

## P2: provenance across capture and derivation

Host-generated capture metadata records origin own/forwarded/url, captured_via, timestamp and original body SHA before quarantine. Own content retains its classification; forwarding and external URL content remain untrusted. Derived manifests bind input hashes and inherit the least trusted input. A staged warning checks privileged notes consuming untrusted derivatives for current independent evidence, not merely a self-asserted verified_by string. No new memory database or blanket read-time quarantine.

## P3: durable state and collaboration

Add a bounded advisory-lock and hash-bound atomic partial-edit helper for hot/handoff/tasks; retain untouched bytes and detect stale bases. Generate machine handoff projections from explicit current log/tasks/anchor data while the host retains narrative ownership. New item IDs use deterministic content/namespace hashes with collision checks; preserve historical sequential IDs. Detect disappearance of ID-tagged blocked items against the exact previous anchor. Model-free PreCompact/SessionEnd hooks persist only minimal hashed runtime state in an ignored engine directory, never inventing completion or modifying business notes. Runtime files must not enter retrieval or lint inventories.

## P4: temporal SSOT v2

Provide a Markdown fact ledger with subject/relation/value, valid_from/valid_until, recorded, source and superseded_by. Accept documented legacy column aliases, distinguish disjoint history from overlapping contradictory active facts and require source for confirmed rows. Do not infer dates or resolve corporate conflicts. Migration is an explicit hash-bound proposal/apply path preserving all original values and unrelated bytes; vault-upgrade documents/dry-runs it and never silently migrates user ledgers.

## P5: lesson delta and declarative rules

ADD/INC/EDIT/RETIRE operates on stable IDs and counters through bounded proposal/apply receipts; unchanged lines stay byte-identical and RETIRE changes state rather than deleting. Preserve explicit approval and base hash checks, use P3 locks, and connect observation IDs without treating reported outcomes as verified. Declarative existence/substitution/occurrence rules are data, not executable code. Literal rules are default; any regex has hard input/pattern limits and a real execution timeout, with unavailable/timeout diagnoses rather than fabricated pass results.

## P6: compile coverage, egress and token calibration

Before quarantine, compare required numbers/names/dates and host-specified claims against compiled notes, binding originals and descendants. Semantic judgments remain advisory and Jev-first when authorized. Add warning-level staged secret/Korean identifier scanning using D1's shared normalization; optional external scanners are never prerequisites. Token calibration consumes generation/model-bound measured samples, exposes error/drift and uses coefficients only when explicitly selected; provide optional real tokenizer/count adapters and distinguish local tokenizer counts, external API counts, reported samples and synthetic fixtures. No automatic changes to default budget coefficients.

## Review and local rollout

Use isolated feature work, red/green logs, per-unit independent review and a final broad review/full suite. Integrate only verified local commits. Install into unique persistent sources/cache versions, preserve older caches/checkouts/settings/scopes and test installed code. Record six handoff fields, exact evidence and limitations. Leave remote publication to an explicitly authorized release action.
