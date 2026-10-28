# Memory boundary evaluation and evidence diagnostics

The user approved implementation of the first three recommendations on 2026-10-28.
The base is public v0.32.0, commit `446249062ee45193064e255ea71a0eb7e9e0a1f8`.
The research handoff is `D:/ns-automation/downloads/av-research-20261028/research-handoff.json`.

## Goal and scope

Add independently authored, offline synthetic evaluations for cross-fact state
revision and evidence insufficiency, plus opt-in complete support-row references
and subject/value binding diagnostics. Keep existing core behaviour unchanged
unless a new explicit option is supplied.

### A: state revision evaluation

Create `scripts/evaluate_state_revisions.py` and a small synthetic JSON fixture.
Public case payloads contain ordered revisions and declared arithmetic dependency
rules; expected labels are separate and never passed to an answerer. Evaluate
both an earlier and a revised snapshot so simply copying the final answer is not
the tested control. Use bounded decimal arithmetic, not eval or executable rules.
Classify each answer as current, superseded, mixed/dependency-stale, other, or
unassessed/error as appropriate; report dependency errors separately. Include
direct updates, unchanged facts, chained updates, and a derived quantity after
an input update. Existing current/historical fixture scenarios are not new here.

### B: evidence insufficiency evaluation

Create `scripts/evaluate_evidence_insufficiency.py` and a small synthetic JSON
fixture. Separate supported answers, acknowledgement of insufficient evidence,
unsupported premise denial, and other errors. Include answerable, missing,
partially covered, incomplete/truncated, and explicitly negative evidence cases.
An absent fact must not justify asserting it does not exist. Incomplete evidence
must not certify a negative/absence finding. Coverage diagnostics describe only
declared fixture slots, not universal semantic relevance or proof of absence.
Structured responses have an explicit bounded kind and claims; this is not a
free-form semantic classifier. Existing answer-rate metrics remain unchanged.

### Shared A/B boundary

Each standalone CLI defaults to a named deterministic synthetic control, accepts
an explicitly bound observation replay, and can export replay observations.
Replay binds fixture, case/question, evidence/snapshot and evaluator configuration
digests; altered, missing, extra and malformed observations cannot score as good.
Callbacks get only copied public payloads; no labels or other-case observations.
JSON reads are bounded and reject nonfinite values, duplicate keys and unsafe
links. Use finite bounded strings/lists/numbers and no shell/eval/network/model
execution. CLI errors are safe and bounded. Reports label synthetic controls and
caller-attested external observations; model requests and costs are not invented.
Use only Python 3.10+ standard library. Do not copy upstream datasets or code.

### C: support row references and subject binding

Extend `check_support` and CLI with `row_references=False` / `--row-references`
and `bind_subject=False` / `--bind-subject`. With neither flag, preserve the exact
legacy output and warning/count semantics. `row_references` adds every parsed
ledger row, including supported, unsupported, unresolved and skipped rows, using
content-free path/line/status/reason/via references and a directly usable generic
`audit_population` for `scripts/real_vault_audit.py draw`.

`bind_subject` adds separately labelled binding observations and warnings when
a literally supported value lacks its subject in the same original physical
source line/table row. Reuse existing NFKC/casefold/markup and literal-boundary
rules, including explicit wiki display aliases for subjects. Do not infer entity
aliases from unrelated metadata. This is a conservative physical-line diagnostic,
not sentence understanding, semantic support, contradiction or truth verification.
Do not let frontmatter, a removed fact table, or separate lines create a binding.
Empty subjects and unreadable/malformed/incomplete sources remain unassessed.
Keep original literal support counts and `warnings` unchanged; put new warnings
in their own opt-in field. Honour `primary_text_only`, self-citation exclusions,
budgets, source policy, bounded output and stable-read/freshness checks.

## Out of scope

No autonomous rewriting/deletion, live-vault measurement, default retrieval
cutoff, network/model provider, new database, memory-poisoning lifecycle engine,
CI build provenance, dependency installation, deployment or remote push.
No upstream benchmark or model robustness/effectiveness claims.

## Validation and integration

TDD for each new feature; strict schema/adversarial input and real CLI tests;
independent spec/code review per component and final integrated review. Run the
full repository unittest suite and existing default-output golden tests. Changing
`vault_ssot.py` changes the warning certificate checker hash even with flags off;
document the existing coordinated upgrade/re-certification requirements. Do not
upgrade installed plugins or the D:/NS engine copies as part of implementation.
Finish on a clean committed local feature branch; local master integration is
permitted when fast-forward and clean. Publishing remains a subsequent request.
