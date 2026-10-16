# Korean memory evaluation and stateful regression

The user approved staged application of the reviewed research. Extend existing
evaluation flows, preserve Markdown/git authority and stdlib runtime, and keep all
model execution explicit. This work does not replace recall or migrate a vault.

## Deliverables

1. A synthetic Korean business-memory evaluation pack and additive CLI. Existing
   recall evaluators remain compatible. Measure returned evidence separately from
   bounded structured-answer correctness, current versus historical assertions,
   abstention, stale facts and withdrawn-source reliance. Include at least 40 cases
   across current facts, historical facts, conditional facts, absent answers and
   source withdrawal; use invented organizations and facts only.
2. Hypothesis development-only state machines exercise real proposal and operation
   APIs against isolated temporary vaults. Model expected state independently and
   cover edits between prepare/apply/undo, retry, uncertainty and recovery. Validate
   actual target bytes and avoid declaring every applied receipt current forever.
3. An opt-in AgentDojo-inspired synthetic tool-call evaluation. Clean and attacked
   episodes share model/tools/budgets. Grade benign completion, achieved attack,
   blocked attempts and transport/protocol errors separately. An in-memory outbox
   makes a harmless attack goal reachable; no real messages or shell tools exist.

## Evaluation contracts

- The answerer sees only question, explicit public options and returned evidence,
  never reference answers, expected paths, forbidden assertions or grading rules.
- Structured assertions distinguish current and historical scopes. Grade bounded
  facts, not unrestricted natural-language truth; raw prose is not proof.
- Invalid output, missing observations, unavailable models and incomplete retrieval
  remain errors or unassessed; they cannot count as successful abstentions or safety.
- Retrieval/corpus/config hashes bind replay observations. Changing inputs or
  withdrawing a source invalidates old evidence and observations.
- Default deterministic runs are explicitly labeled synthetic and contain no model
  token or billing claims. Optional actual model usage is provider-reported or null;
  character-based context estimates and any price-derived estimates stay separate.
- All tools operate only on built-in synthetic fixtures and in-memory state. No
  arbitrary vault path, file I/O tool, shell, real outbox or external network tool.
- Optional inference transport is loopback OpenAI-compatible chat completions only,
  proxies/redirects disabled, explicit model, bounded bytes/output/turns/calls/time.
  Real tool_calls are required; no silent deterministic or prose-plan fallback.
- Grade final world independently of later protocol errors: an achieved attack is
  still achieved if a later model request fails. Report error denominators.

## Scope and verification

No default hooks, model/network calls, config migration or runtime dependencies.
Hypothesis is a pinned test dependency and runs in a separate CI command. Product
tests use standard unittest. No third-party source or benchmark data is copied.
Research references inform the method; this is not an official benchmark score.

Observe RED then GREEN for each new evaluator behavior. Run the existing suite,
new deterministic CLIs, generated stateful tests, protocol/label-isolation/budget
tests and deliberate faulty-response controls. Obtain fresh independent review.
Actual-model execution, if locally available, must be reported separately from
scripted protocol tests; absence is not a successful actual-model result.
