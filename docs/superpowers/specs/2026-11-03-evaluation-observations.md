# Stage interventions and fresh Korean model observations

The user authorized implementing and publishing the first recommended research
batch: stage-specific memory-poisoning evaluation expansion and fresh Korean
structured-answer evaluation. Other research candidates remain out of scope.

## Intent and approach

Add two optional checkout-only evaluators. Reuse actual source capture, memory
compilation, revision-bound lesson persistence, retrieval, the existing scoped
assertion grader and the bounded local chat transport. Preserve the existing
evaluators and normal installed engine behavior. An isolated new evaluator is
preferable to changing defaults or adding a mandatory evaluation framework.

These are controlled original fixtures and adapters inspired by MemGauge,
NoMIRACL and Inspect's separation of inputs and scoring. They are not official
benchmark reproductions, an Inspect SDK integration, or copied external data.

## Global constraints

- Python 3.10+ standard library; no new runtime dependency.
- Existing hook/config/search and evaluator CLI/output defaults remain unchanged.
- No changes to the seven certificate-bound checkers or committed LF corpus.
- Inference is opt-in with an explicit model and literal-loopback endpoint, using
  the current exact chat path, proxy/redirect/retry prohibitions and byte/time caps.
- Only an explicitly named process environment variable may supply an API key.
  Do not discover credentials, read secret files or discover server properties.
- No real vault input, arbitrary external corpus, shell/browser/message execution,
  automatic memory policy deployment, or active installation update.
- Errors, missing usage and incomplete evidence are not successful abstention,
  poisoning prevention, repair or zero-cost inference. Raw model prose, reasoning,
  HTTP bodies, authentication headers and exception text never enter public output.
- temperature=0 and identical inputs do not establish deterministic inference.
- Existing strict repair scorer stays unchanged; the new stage evaluator makes no
  selective-repair claim without the complete existing four-phase paired design.

## A. Stage-specific evaluator

Create `scripts/evaluate_memory_stages.py` and `tests/test_memory_stages.py`.
Provide fixed ASCII/Korean fixtures and `writing|management|retrieval|all` named
contrasts. Each contrast compares two levels on matched clean/poison sources and
checksum/startup tasks. Other stage settings are held fixed and exposed in the
report. Both clean task utility and poisoning outcomes must be visible.

Required internal/public boundaries:

- `collect_stage_dataset(*, stage='all', case='all', max_tokens=1500) -> dict`
- `evaluate_stages(*, stage='all', case='all', max_tokens=1500,
  backend='scripted-control', control='safe', endpoint=None, model=None,
  allow_network=False, api_key_env=None, limits=None) -> dict`
- `stage_metrics(rows) -> dict`

Writing contrasts caller-origin review with accepting inline instruction labels;
an accepted item really passes through compilation and persisted lesson delta.
Management contrasts preserved roles with a deterministic role-erasing projection;
the projection is persisted/reloaded before review and consumer compilation.
Retrieval contrasts explicit source eligibility over identical stored files and
feeds actual returned retrieval context into the solver. Merely changing a report
flag while feeding unchanged compiled context is forbidden.

Use neutral fixture paths/IDs. Expected answers, arm/stage labels, poisoning
classification and scoring state stay outside model messages. Report actual
storage, context and local source-code hashes, stage-specific exposure and
omission/incompleteness. Label generation as
`deterministic_experimental_intervention`, never model-generated memory or an LLM
summary. No user-controlled file path is accepted.

Reuse lifecycle `solve_sample`, `ModelLimits`, `LocalChatTransport` and actual
Python-only action execution; provide its required sample fields without changing
its contract. Keep stage metrics distinct from existing selective-repair metrics.
Include requested/sent/completed/error counts, benign-task preservation, exposed
poison plans, observable bad-plan adoption, matched-pair completeness and nulls
where a conditional rate has no eligible denominator. Reserve complete per-request
deadlines; after timeout/transport failure stop later requests.

## B. Fresh paired Korean observations

Create `scripts/evaluate_model_korean_memory.py` and
`tests/test_model_korean_memory.py`. The existing evaluator and render-matched
evidence-only contract remain intact.

- `collect_dataset(*, max_tokens=1500, top_k=3, backend='bm25') -> dict`
- `build_request(sample, *, model=None, limits) -> dict`
- `solve_sample(sample, *, backend='scripted-control', control='good',
  transport=None, model=None, limits=None) -> dict`
- `replay_dataset(dataset, receipt) -> dict`
- `evaluate_model_korean_memory(*, backend='scripted-control', control='good',
  max_tokens=1500, top_k=3, retrieval_backend='bm25', allow_network=False,
  endpoint=None, model=None, api_key_env=None, limits=None,
  observations=None) -> dict`

Derive default and annotate-only retrieval arms from the current original Korean
fixture; keep withdrawal exclusions in both. Each arm gets its own fresh request,
even when evidence/prompts match (the full current fixture has 50 cases, therefore
100 requests). Report identity and order; do not claim annotation efficacy for
identical prompts. The public payload is exactly `question`, `options`, `evidence`.
No labels, case ID, arm, expected source/value, source fingerprints or past answer
enters messages. Use a Korean system/response instruction contract, hashed in the
report, and reuse `validate_response`/`grade_response` unchanged.

Accept only one assistant completion with stop finish, strict complete JSON,
exact scoped-answer or abstain schema, no tool calls/prose/fences/duplicate keys.
Preserve valid provider usage on invalid/truncated answers; billing stays null.
Errors are whitelisted content-free codes, not the old evaluator's exception text.

The versioned receipt binds all successful/error rows to case, arm, question,
options, actual evidence/context paths/completeness/status, full pack including
labels, eligible corpus/policy/config, local evaluator dependency closure, model,
limits, prompt and intended request. Replay checks exact sample set/order and all
bindings; changed labels, code, arms, evidence or model contract invalidate it.
Reject extra/missing/duplicate rows. Errors cannot become successful abstentions.
Replay accepts no live-model options or transport and sends no requests. Receipt
origin is recorded, not cryptographically authenticated or independent proof of
inference. CLI JSON replay input is bounded and strictly parsed.

## Verification and publication

Write meaningful tests before implementation; retain red/green logs in ignored
plan workspace. Test real loopback HTTP contracts without calling them actual
LLM inference, parser/budget/timeout failures, label leakage, tampered replay,
source closure and unchanged old behavior. Run baseline and final full suites,
existing regression gates and independent reviews. Add offline CI smoke commands.

Update deliberate release surfaces to v0.39.0, document scope/research references
and actual results, and publish only after exact-source master/tag CI and current
release integrity checks pass. Build a reproducible source ZIP, checksums and
sanitized verification; review downloaded draft assets before immutable publication
and verify fresh public downloads, Latest, tag and attestations afterwards.
