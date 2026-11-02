# Memory trust and actual-model lifecycle evaluation

User approval: the user requested implementation after the recommendation to
first combine provenance/approval laundering controls, poisoned-experience
controls, and actual-model memory-repair evaluation. This is the authorized first
bundle; quote re-anchoring and Markdown AST diagnostics remain later work.

## Deliverables

1. An optional standard-library helper that preserves host-supplied message roles,
   IDs and exact UTF-8 content hashes in a typed JSON envelope. Reviewing proposed
   memory claims checks exact source evidence and prevents a tool/external quote
   from being promoted into a user instruction. Experience claims need a distinct,
   bound host observation; self-asserted `safe`/`validated` content is insufficient.
   Outcomes are advisory source-binding assessments, never authorization or truth.
2. Offline original bilingual fixtures covering forged role labels, repeated
   summarization, poisoned successful experiences, benign retention, duplicate or
   stale proofs, bounded context and operational failures. Positive vulnerable
   controls must actually exhibit the intended failure. Do not copy attack code.
3. An opt-in actual-model lifecycle runner using fresh subprocesses and the actual
   capture/lesson/compile/revocation paths already used by the offline evaluator.
   Same model and prompt contract in paired retained/withdrawn arms; evaluate
   checksum behavior and unrelated startup behavior before repair, after repair,
   after declared-copy replay, and after independent new learning. Only explicit
   literal-loopback inference is supported. Default execution is offline and must
   be labelled synthetic; errors are not successful abstention or forgetting.

## Constraints

- Python 3.10+ and standard-library runtime only; no default hook/config/output
  changes, real-vault input, external model endpoint, shell execution or messages.
- Host-origin metadata is caller-attested, not authenticated. Hashes bind bytes,
  not actual execution, authority, semantic equivalence, or correctness.
- JSON field/key/byte/depth/count bounds; no raw error/model/key content in errors.
- Never slice provenance off content during truncation; emit whole bounded units.
- New evaluator records source/config/prompt/model bindings, usage only when
  actually supplied, latency and errors. Never claim actual billing or official
  benchmark results. Do not report script/mock controls as a model measurement.
- Local integration and commits are authorized; publication/version bump/active
  plugin installation and company-vault activation are outside this task.

## Acceptance

Meaningful red/green tests, independent security review, complete existing unit
suite, existing offline lifecycle regression, new safe and vulnerable controls,
CLI validation and clean committed local integration. Actual model execution is
reported separately with exact model and sample counts, or explicitly unavailable.
