# Jev-first direct question contract (2026-09-20)

Jev-first mode sends supported judgment work to Jev before the host answers. The host handles unsupported generation, environment observation and tool execution, synthesis, and unavailable or uncertain results. Arithmetic is not excluded merely for being trivial. Activate this mode through an explicit user request or a standing authorized preference.

Official API checked on 2026-09-20 at https://docs.typesafe.ai/api: three native types, noul (yes probability), choice (up to 255 options), score (2–10 ordered rubric levels, weighted score/legend/probabilities/confidence). It is a typed evaluation endpoint, not arbitrary text/code generation or tool execution. A public synthetic probe on model jev-1.13.0 returned arithmetic noul 0.98, choice two/confidence 1, and greeting score 1.99/confidence 0.98. Synthetic checks do not prove general accuracy.

## Additive adapter

Keep ALL previous file-bound input schemas, policies/report hashes, validators and output formats compatible. Do not weaken source rules or fabricate source files for chat questions. Add separate direct CLI: Python scripts/jev_ask.py or Node scripts/jev-ask.mjs. Both prepare|run --input -; run requires --allow-network; no workspace required, no automatic file read/write, bounded JSON stdin, sanitized JSON stdout/stderr. Key from TYPESAFE_API_KEY only, fixed endpoint/model, no retry/redirect, bounded timeout/body. Existing legacy client may be reused without changing its public contracts.

Common input:
{
 "schema_version":1,
 "context":{"kind":"user_input","text":"1+1=2 맞아?"},
 "questions":[
   {"id":"answer","type":"noul","instructions":"보통 자연수 덧셈에서 명제가 참인가?","criteria":{"true":"참","false":"거짓"}}
 ],
 "min_confidence":0.8
}
context.kind is user_input or selected_text; it describes caller-supplied inline material, NOT verified file provenance. Context text required nonblank. 1–12questions. Noul optional criteria {true:string,false:string}; Choice criteria object2–255 labels/descriptions, mandatory abstain label in criteria; Score criteria ordered2–10 nonblank string levels. IDs safe ASCII, category labels Unicode. Reject duplicate keys/IDs, extra schema fields, nonfinite numbers, invalid UTF8, known credential shapes, actual auth key included in selected input.64KiB input/wire/response. Never transmit full chat history, arbitrary workspace/environment, unrelated files or hidden context.

Prepare exposes metadata/hashes without context/instructions. Run returns status reviewed|needs_review|unverified, role advisory, evidence_binding inline_not_file_verified, fixed model, input_hash/request_hash/policy_hash, network_attempted boolean, normalized typed results and sanitized operational error. No context/instructions/free-form provider explanation echoed. Native Score legend may only exactly reproduce supplied rubric levels. Preserve native types: Noul carries noul probability (do not invent native confidence), Choice choice/probabilities/confidence, Score score/legend/probabilities/confidence. Validate exact returned IDs/types/schema/choices and finite bounds, sum/argmax, score legend0..N-1 and weighted score consistency (allow documented rounding tolerance if needed). Default min_confidence .8 is heuristic: Choice abstain OR native confidence belowthreshold =>needs_review; Score confidence belowthreshold =>needs_review; Noul max(p,1-p) belowthreshold or exact.5 =>needs_review (derived decisiveness is not provider confidence). A score is a rubric rating, not an arbitrary computed number. Failure/no key/no authorized network =>unverified. Do not hide fallback as Jev output.

## Host routing

Jev-first mode applies when user requests it, including standing authorized preference. For eligible current requests, use the direct route first for self-contained questions or explicitly selected inline text. Do not skip just because easy, obvious, arithmetic, or outside the seven harness checkpoints. Decompose mixed requests into typed judgments for Jev and unsupported generation/tools for host. Open text/code/art creation and actual browser/file/test operations remain host tasks; Jev may assess candidates using typed judgments after evidence is collected.

Keep file-derived judgments on the existing file-bound route when applicable; never relabel denied/excluded/secret file content as inline user input to bypass controls. Needed current/external facts must be collected first, and lack of evidence is not proven negative. Prefer abstaining Choice for evidence-dependent questions. Native Noul/Score do not guarantee abstention; host must establish applicability/context and keep insufficient evidence for review. Existing transmission authorization is reused; no extra prompt for each eligible ordinary question within scope. This is not permission for whole-vault uploads, hidden/private data or new side effects. Key presence is not authorization. User explicitly authorizes maximum Jev use here; preserve existing permission gates, tests, state writers and completed workflow records.

No double call: use existing current file report if it already answers the same supported question, or reuse exact matching prepared direct request metadata. One batch per unchanged request, no automatic retries; this is host instruction, not enforced global quota. Jev supports all eligible judgments, not only step16/24/25/30/37/45/49; fixed checkpoints remain mandatory places to consider their existing authorized route. Output provenance must be clear: short Jev-used marker for actual validated response, or host fallback + reason when not called/unsupported/unavailable. Never claim native free-form generation, execution/permission authority, or calibrated confidence.

## Verification and delivery

TDD on typed contracts, missing key/no network, UTF8/duplicate/secret rejection, native type/response checks, lowcertainty/abstain, timeout/error body sanitization; preserve all legacy tests. Cross-language same fixtures and mockresponse behavior, official public3-type liveprobe already done; final public smoke newCLI once per implementation. Independent review. Versions agentic-vault0.15.0 and harness502.8.0, existing authorized release/install flow, settings preserved. Root owns NS user preference (AGENTS/CLAUDE), ADR and handoff; implementation agents own their isolated repo changes; no commits/publish until root integrates and reviews.

task_id: jev-first-20260920
artifact_paths: separate direct adapters, host skills/routing/docs/tests, current NS preference
verification_commands_and_results: official 3-type API checked; public synthetic direct CLI batch passed all 3 types in each implementation; Python full suite 480 passed/15 platform skips, Node full suite 1282 passed/2 platform skips; no failures
assumptions: supported typed judging first; generation and tools remain host responsibilities; explicit or standing transmission authorization required
unresolved: representative task accuracy and guaranteed host routing are not established by synthetic tests; remote CI and installation evidence belong to the exact release assets
next_safe_action: load the released plugin in a new host session and use the typed adapter for eligible authorized questions
