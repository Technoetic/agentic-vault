# Recall support and lesson observation design

## Authorized goal

The user approved implementation and public release of the research proposal:
real-recall evidence coverage, boundary/distractor/multi-record evaluation, and
observed lesson effects. This approval covers the plugin and Reference Atlas
public evidence update. It does not upgrade the active installation or company
vault, copy restricted datasets, or grant permission to execute business actions.

## Chosen approach

Keep the standard-library runtime and existing recall bytes unchanged. Add an
opt-in, in-memory authored-evidence assessor; call real recall on original
synthetic vaults to measure evidence loss; and score explicitly recorded actual
host trials separately from offline scripted controls. This follows the approved
proposal. Adding a vector database or uncalibrated model gate would change the
product/dependencies without evidence, so neither is included.

The assessor observes exact authored quotations in actually emitted, attributed
recall blocks. A matching document title, a truncated quotation, or an omitted
record cannot satisfy a requirement. A fully visible quotation in a shortened
block may be observed, while output incompleteness remains explicit. Multiple alternatives are OR; requirements
are AND. Declared blockers remain distinct from missing evidence. Report scan
coverage and output coverage independently. These are lexical observations on
caller-declared evidence, never semantic truth, authority or permission.

## Components and interfaces

1. `skills/agentic-vault/scripts/vault_recall_support.py`:
   `assess_support(recalled: dict, contract: dict) -> dict`. No filesystem/network
   reads or hidden additional retrieval. Contract v1: exact keys `schema_version`,
   `requirements`, `blockers`; each list contains `{id, alternatives}`; each
   alternative contains exactly `{path, line, quote}`. Paths are portable vault
   relative Markdown paths, line is a positive integer, quote is bounded nonempty
   literal single-line text. IDs unique across both lists. Output exposes observed
   requirement IDs, missing IDs, blocker IDs, scope/output completeness, explicit
   observation state and attributable evidence locators/hashes. It does not echo
   raw quotations or claim source freshness. Strict bounded validation, including
   duplicate/unsafe fields, nonfinite numbers and malformed actual recall data.
2. `scripts/evaluate_recall_support.py`: real recall + assessor in fresh synthetic
   vaults. No user-vault argument; default local deterministic operation. Cases:
   lexical distractor, complete evidence, missing condition, conflicting blocker,
   distributed multi-record chain, order change, answer outside snippet, context
   truncation, no match, and UTF-8 files below/at/above 524288 bytes with facts at
   multiple positions. Independently authored expected visibility. Measure
   retrieval matches separately from authored evidence found, scope, false
   assurance, and evidence completeness. Include a deliberately naive
   any-match control that demonstrably fails. Do not claim retrieval improvement.
3. `scripts/evaluate_lesson_cue_effects.py`: deterministic scoring of bounded
   trial records, with no trial executor or shell/credentials access. Each paired
   scenario needs baseline/hint/guard observations, prompt/task and model/host
   configuration bindings, lesson source and compiled hash bindings, timing,
   actual attempted action sequence, final task check, false interruption and
   usage observations. Verify conditions and pair identity; report missing,
   stale, invalid and unpaired trials without zero-filling. Demo records must be
   labeled scripted and cannot count as actual-host evidence. An actual-host
   importer may accept explicitly supplied transcripts; it must not infer
   effectiveness from cue injection alone, execute embedded commands, or claim
   authenticity from caller labels/hashes. Sample-size and observational limits
   accompany deltas; no statistical/causal efficacy certification.

## Research and publication constraints

RULER 2404.06654 and NVIDIA/RULER canonical alias provide evaluation patterns;
NoLiMa 2502.05167 provides lexical-distractor design only. No NoLiMa code/data is
copied (noncommercial research license). Sufficient Context 2411.06037 was already
reviewed in the third research note; explicit new application is the real-recall
connection. Its repository is README/images with no runnable autorater or license.
lessons-learned 3b39f38152fe, MIT, was already used for hint/guard; this release
adds observation scoring, not a new lesson system. Paper results never become
plugin measurements. No new production dependency, hook/config default change,
automatic model call, external dataset, live-vault read, or security guarantee.

## Acceptance and publication

Tests must distinguish positive and negative evidence, missing versus unread,
matched versus emitted, correct source versus same quote elsewhere, malformed
paths, budgets, truncation, forged/stale/unpaired lesson observations. Preserve
legacy recall snapshots. Run focused and full suite, independent review, exact
branch/tag CI, disposable native installation/resource/certificate checks, ZIP
rebuild/member verification and immutable release draft-asset review before
publication. Verify public downloads and release attestations after publication.
Update Atlas pinned uses and inspect both plugins' earlier uses. No Sites hosting.
