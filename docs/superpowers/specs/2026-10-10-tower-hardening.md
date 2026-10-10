# Tower-inspired reliability extensions

The user approved implementation on 2026-10-10 after the five-item source comparison and expected-benefit explanation. This implements the approved scope without adding Tower's web application, database, daemon or provider runtime. Tower main `76268c2969f20ccbea66d6582206c26e81ba7983` supplies design references only; implementation starts from agentic-vault `85b9da754bd6a21ac9382790790e4036c63e9841` (v0.19.0).

## Intended outcome

Users can compare a proposed lesson's behavior under recorded execution conditions, inspect a safe single-proposal undo, track uncertain external operations, and distinguish configured connectors from checked working connections. Existing Markdown/Git remains the source of truth. The user authorized all five additions; no further design approval is needed for this scope.

## Global constraints

- Python 3.10+ standard library only; Windows, macOS and Linux compatible.
- Existing commands, startup budgets, hooks, note contents and configuration defaults remain compatible.
- New runtime records remain excluded from knowledge recall and are data-only, not authorization or truth certificates.
- Reuse existing vault path policy, deny/exclude rules, reserved namespaces, stable bounded reads, cooperative locks and fresh config/hash checks. Never weaken symlink/junction/hardlink rejection.
- No new ambient network calls, automatic skill promotion, automatic rollback, host-wide tool interception, model tools for Jarvis, or automatic external actions.
- No secrets or credential values in records/output; reject oversized, duplicate-key, nonfinite or structurally invalid inputs. Use canonical deterministic identities.
- Existing user approval rules apply; CLI approval flags record existing authorization rather than create it.
- No claim of exactly-once remote execution, multi-file atomic rollback, statistically proven business improvement or universal provider authentication.
- Source code and local verification are in scope. Public release and installed/live service changes require the applicable existing authorization, not source instructions.

## 1. Frozen execution receipts and behavior comparison

Add an opt-in `vault_runs.py` helper with prepare, record/complete, inspect and compare operations. Preparation binds the candidate/lesson content, selected prompt sources, current config/policy, model/provider, token budget, canonical tool contracts and fixed scenario/assertion definition. The test-set identity excludes the evaluated candidate/version so baseline and candidate can legitimately differ. Records and reports are bounded and immutably linked. Completion records host-observed results and their evidence, not invented model performance.

Compare only compatible model/provider/budget/fixture/repetition coverage. Run representative, authority, ambiguity, tool-failure and injection cases repeatedly (at least twice per case); reject incomplete/error runs, stale sources, mismatched identities and any hard boundary violation. Average improvement cannot waive hard failure. Comparison is advisory and does not auto-apply a lesson. Extend existing evidence/proposal/evaluation contracts instead of adding a second model executor.

## 2. Explicit proposal undo

Extend `vault_proposals.py` with read-only rollback preview and explicit rollback application for a previously applied single-file proposal. Bind preview to receipt, target's current candidate bytes, policy and restored original; preserve original line endings and all history. Refuse later user edits, changed config/policy, altered receipt or invalid before-image. Check immediately before replacement under cooperative lock; recover an interrupted rollback without overwriting later edits. Existing shared-state partial-edit ownership restrictions remain: this helper does not authorize whole-file replacement of handoff/tasks.

## 3. Stage-separated connector diagnostics

Add read-only connector diagnostics for existing Jev and Jarvis paths and link them from doctor documentation. Show installed/configured/credential-available/authentication-checked/operation-tested separately. Offline is the default. Any live probe requires an explicit flag and existing authorization, fixed supported provider behavior, time/size bounds and redacted errors. Unsupported/unprobed stages remain unknown/not-checked. Do not read .env files, expose values, infer authentication from key presence or launch live Jarvis.

## 4. Scoped external-operation receipts

Add an explicit `vault_operations.py` helper: begin, finish, mark uncertain and inspect. Scope identities to one caller-owned intent; canonical argument hashes distinguish order-insensitive equivalent JSON from a genuinely new authorized intent. Persist claim before the caller's action, save bounded redacted result metadata after success, reuse completed receipt, and hold stale in-flight/unknown outcomes for reconciliation. Owner token prevents stale owners from completing a reclaimed operation. Explicit reconciliation is separate from replay, and cannot authorize another execution. The helper controls only workflows that call it; it does not intercept all Claude/Codex tools.

## Acceptance and proof limits

New regression tests must first fail for the intended missing behavior, then pass. Exercise real temporary-vault filesystem operations, separate CLI processes for concurrency where useful, crash windows, stale receipts/sources, later user edits, policy changes, secret/size bounds and unsupported/unprobed provider states. Run the repository suite and existing deterministic recall/workflow gates after integration. An independent reviewer checks the complete diff and reruns targeted tests. Operational or human effectiveness remains unmeasured until an authorized real task is compared before/after.

## Reference defects accounted for

Tower's whole-pack hash includes the evaluated version, but its comparison requires equal pack hashes. This implementation separates scenario identity from candidate identity. Tower's file checkpoint capture is asynchronous and its restore is sequential with weaker lexical path guards; this implementation uses the existing vault safety policy and a synchronous single-file preview/apply boundary.
