# Memory diagnostics and reviewed lesson changes

The user approved the first two recommendations from the OpenClaw comparison:
read-only memory diagnostics and recorded, revision-bound lesson proposals.
Source lineage and upgrade planning remain separate follow-up work.

## Constraints

- Python 3.10+, standard library only; Windows, Linux and macOS.
- Existing Markdown files remain the source of truth. Existing config works unchanged.
- Preserve the session hook's silent non-vault behavior, fail-soft exit status and current context output/budgets.
- All vault accesses use the existing path resolver, deny zones and bounded reads. Never read secrets or execute commands recorded in a proposal.
- No network, model invocation, automatic skill promotion, installed-plugin changes, push or release publication.
- User-owned targets only; preserve engine ownership and managed blocks. Approval belongs to the calling workflow; a metadata field is not proof of human approval.
- Concurrent unrelated filesystem edits are outside the resolver's OS security guarantee. Check the target again immediately before replacement and lock cooperating proposal writers.

## Doctor

Add `vault_doctor.py --vault PATH [--format text|json]`. It diagnoses the same
configuration, paths, bounded reads and rendered output as the session hook.
Report `not_vault`, `invalid_config`, and per-section `ready`, `disabled`,
`missing`, `empty`, `unreadable`, `unsafe_path`, `truncated`, `budget_too_small`.
Distinguish JSON corruption from validated config errors without echoing config
values, arbitrary key names, note text, denied paths, or exception strings.
Return safe known field names/reason codes and a useful next action. JSON must
include aggregate status, per-section state, configured/effective token budget,
estimated emitted tokens and whether the entire hook would emit context.
If any fatal section error makes the current hook suppress all context, say so
even if another section is independently readable. Exit 0 for usable/intentional
states, 1 for degraded warning states, 2 for invalid/unusable setup. The CLI is
read-only including no report/cache/health-history write. It cannot attest that
the host trusted or executed a hook; label that boundary explicitly.

## Lesson proposals

Add `vault_proposals.py` with explicit `propose`, `inspect`, `check`, `apply`,
`reject` commands. Use JSON receipts under `00-meta/proposals/` (JSON is not fed
to lexical Markdown recall). A proposal changes exactly one existing UTF-8
Markdown target, supplied as a complete candidate Markdown file inside the vault.
Target/candidate/receipt access must be contained and deny-zone checked; targets
cannot be receipt storage, `.git`, or engine-managed files. Reject engine rule,
generated-agent markers and edits to CLAUDE managed blocks; user-owned commands
and the user-owned portions of CLAUDE.md are valid. No creation/deletion/rename.

CLI contracts (global `--vault` before subcommand):

```text
vault_proposals.py --vault PATH propose --target CLAUDE.md --candidate 00-meta/candidate.md --lesson "L-001" --summary "Clarify the recovery procedure"
vault_proposals.py --vault PATH inspect ID
vault_proposals.py --vault PATH check ID
vault_proposals.py --vault PATH apply ID --approve
vault_proposals.py --vault PATH reject ID --reason "Keep the existing procedure"
```

Receipts retain immutable proposal id, lesson reference, summary, target, owner,
base SHA-256 and exact original/candidate bytes (UTF-8 representation), candidate
SHA-256, creation time, and event history for check/decision/application including
validation results and applied hash. `inspect` includes the exact diff so callers
can review the actual proposal. `check` is read-only. Never claim Git commit
creation or human identity that the tool cannot establish. The ordinary workflow
commits receipts and target together afterward.

Apply requires the explicit `--approve` flag, revalidates receipt integrity,
config/path/ownership and current target SHA-256, and rejects stale proposals
without writing the target. Hash mismatch is an edit-conflict signal, not an
authentication mechanism. Validate candidate Markdown frontmatter with the
existing checker where required; honor CLAUDE/AGENTS exemptions. No shell/model
validation is run. Store explicit approval and an applying checkpoint before
atomic target replacement. If event persistence fails after replacement, report
incomplete accounting, and let an explicit apply retry reconcile only that
applying checkpoint when the target equals the recorded candidate hash.
Rejected/applied proposals cannot be newly applied; completed apply retries are
idempotent. Lock proposal mutators; interruption leaves evidence, never a false
success or automatic lock deletion. Atomic publication protects each file, not
a whole-vault transaction. Keep permissions of the replaced target.

## Acceptance

Doctor: normal/zero/small/large budgets; missing vs empty; malformed JSON and
schema errors; denied/traversal/link paths; one bad section suppresses all hook
output; no note/config secret in diagnostics; tree contents unchanged.

Proposals: real filesystem propose→inspect→check→apply and reject; no approval;
stale target; tampered receipt; invalid candidate; denied/engine-managed targets;
managed block preservation; bounded input; concurrent writer; failures before
and after replacement; recovery retry; exact Unicode/CRLF preservation.

Integration: documented Claude command and Codex skill routes, no version bump
or claims of publication, full regression suite and independent review.
