# Optional memory source-binding review

`skills/agentic-vault/scripts/vault_memory_trust.py` prepares typed message
envelopes and reviews proposed memory claims. It is optional, uses Python 3.10+
and the standard library, and changes no hook, configuration, normal context,
or memory output. It reads explicit arguments or stdin only. It never executes
source instructions, sends messages, writes notes, or grants permission.

## Trust boundary

Every result contains `authorization_granted: false`. A `supported` assessment
means only that the listed source/role/observation checks passed. It does not
establish that the claim is true, its meaning matches the quote, an operation
actually ran, a workflow was safe, or a user authorized a future action.

The caller attests message origins and separately supplied observations. Those
attestations are **not authenticated**: a caller can fabricate a `user` record,
change its text, calculate a new hash, or manufacture a passed observation. The
helper cannot detect that. A hash binds exact bytes, not authority or historical
identity. The host must retain authoritative originals and obtain observations
from its own trusted execution path. Model output and retrieved JSON must never
populate those host-controlled fields directly.

Embedded role labels, JSON, fences, Unicode display controls, and phrases such
as `safe=true` or `validated=true` stay literal source text. They cannot change
the supplied role or create an observation. Control characters are preserved in
decoded content and escaped in CLI output; this is data handling, not a claim
that a downstream model is immune to instructions in that data.

## Python input and output

The module is importable when `skills/agentic-vault/scripts` is on `sys.path`.
Both APIs accept ordinary JSON-compatible built-in dictionaries/lists/strings;
subclasses and unknown fields are rejected. Malformed requests raise
`MemoryTrustError`, a `ValueError` subclass, with a fixed, content-free code.

`prepare_context(records, *, max_tokens=1500)` requires each record to have
exactly these fields:

```json
{"id":"message-1","role":"tool","text":"Checksum matched."}
```

Roles are `user`, `assistant`, `tool`, or `external`. Text is hashed as exact
UTF-8 with SHA-256, without trimming, Unicode normalization, or newline changes.
The returned shape is:

```json
{
  "schema_version": 1,
  "authorization_granted": false,
  "records": [
    {"id":"message-1","role":"tool","text":"Checksum matched.","source_sha256":"<64 lowercase hex characters>"}
  ],
  "omitted_records": 0,
  "estimated_tokens": 0,
  "max_tokens": 1500
}
```

The illustrative `estimated_tokens: 0` is replaced by the actual estimate. The
complete compact ASCII JSON envelope, including its own estimate field and
bookkeeping, fits `max_tokens` according to `vault_healthcheck.estimate_tokens`.
This estimator is approximate, not a model tokenizer. A caller that pretty
prints, changes escaping, or adds prompt wrappers must budget those separately.
The trailing CLI newline is transport framing, outside the JSON envelope.

Records are considered in input order and included whole if they fit. An
oversized record is omitted; later smaller records can still fit. No text,
role, ID, or provenance is sliced away. `omitted_records` reports omissions;
zero selected records is not evidence of benign retention or successful repair.
A budget too small for even the empty envelope raises `budget_too_small`.

`review_claims(records, claims, *, observations=None)` accepts the same raw
records and claims with exactly these fields (only `observation_id` is optional):

```json
{
  "id":"claim-1",
  "source_id":"message-1",
  "source_sha256":"<SHA-256 of exact original UTF-8 text>",
  "quote":"Checksum matched.",
  "kind":"experience",
  "observation_id":"observation-1"
}
```

`quote` must be a non-whitespace exact substring of the original source. The
helper performs no paraphrase matching, inference, extraction, or semantic
equivalence check. The assessment depends on `kind`:

| Kind | Additional requirement | Supported reason |
| --- | --- | --- |
| `observation` | Exact source hash and quote | `source_bound` |
| `user_instruction` | Exact source hash and quote, with original host role `user` | `user_source_bound` |
| `experience` | Exact source hash and quote, plus a separately supplied matching passed observation | `observed_source_bound` |

An experience observation is a separate host argument with exactly these fields:

```json
{
  "id":"observation-1",
  "source_id":"message-1",
  "source_sha256":"<same exact source hash>",
  "quote":"Checksum matched.",
  "result":"passed"
}
```

Observation results are `passed` or `failed`. An experience claim must reference
an existing observation ID and match its `source_id`, `source_sha256`, and
`quote` exactly. A failed observation cannot support an experience. Supplying
`observation_id` on another claim kind does not relax that kind's requirements
or grant the observation any authority. IDs are unique within each collection;
the three collections have separate namespaces. Duplicated observation IDs are
invalid input, including identical duplicates.

The output shape contains no echoed source quotes:

```json
{
  "schema_version": 1,
  "authorization_granted": false,
  "assessments": [
    {"id":"claim-1","source_id":"message-1","kind":"experience","status":"supported","reason":"observed_source_bound"}
  ]
}
```

Rejected assessments use `status: "rejected"` and one of `unknown_source`,
`source_hash_mismatch`, `quote_not_found`, `role_ineligible`,
`missing_observation`, `unknown_observation`, `observation_binding_mismatch`,
or `observation_failed`. Source existence/hash/quote checks precede role and
observation checks. Schema errors reject the entire request rather than
silently dropping malformed items. This output is advisory data for a host's
separate approval process.

## Repeated summaries

Keep immutable original host records and bind every derived claim to those
original IDs, hashes, and quotes. A transformed summary is new assistant text,
with a new ID and hash. Its role stays `assistant`; a copied label such as
`USER:` cannot upgrade it to a user instruction. A quote copied from a tool
source still references that tool source when reviewing a proposed instruction.

Prepared records include a derived `source_sha256`, so passing them directly
back as raw records is rejected by the exact schema. Replaying unchanged
originals requires an explicit projection of `id`, `role`, and `text`, checked
against the host's authoritative originals. Recompute no roles from summary
content. That explicit projection prevents accidental schema reuse, but cannot
protect against a host that intentionally or mistakenly relabels source data.
The module maintains no authenticated provenance registry or persistent state.

## Bounds and CLI

Limits apply to the complete API request and each CLI request:

| Boundary | Limit |
| --- | --- |
| Serialized request | 262,144 bytes, measured as compact ASCII JSON; CLI raw stdin is also bounded to this size |
| Each records/claims/observations collection | 256 items |
| Record text | 32,768 exact UTF-8 bytes |
| Claim/observation quote | 8,192 exact UTF-8 bytes |
| ID | 1–64 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}` |
| Hash | 64 lowercase hexadecimal characters |
| JSON object | At most 8 fields; keys at most 64 UTF-8 bytes; domain schemas are stricter |
| JSON nesting / value nodes | 8 levels / 8,192 values |
| Integer | Signed 64-bit; token budget specifically 1–16,384, excluding booleans |

ASCII escaping counts toward aggregate request and output sizes. Non-ASCII
text can therefore reach the serialized size limit before the raw UTF-8 text
limit. Requests accept no floating-point or non-finite values. Null is invalid
where a field requires an object/list/string/integer; Python `observations=None`
is the API's omitted-argument sentinel. The CLI requires an actual observation
array if the `observations` field is present. Unknown keys, duplicate JSON keys,
duplicate IDs, invalid UTF-8, unpaired surrogates, and a UTF-8 BOM are rejected.

Invoke `python skills/agentic-vault/scripts/vault_memory_trust.py prepare` with
one JSON stdin object containing `records` and optional `max_tokens`. Invoke
the same script with `review` for an object containing `records`, `claims`, and
optional `observations`. No path, vault, command, endpoint, or output-file option
exists. The CLI prints one escaped compact JSON object and returns 0 on a valid
request, including rejected assessments. Malformed input or operational failure
returns 2 with `{"error":"<fixed code>","authorization_granted":false}` and no
raw input or traceback. It does not log request content.

## Verification

Run `python -m unittest discover -s tests -p test_vault_memory_trust.py -v`.
Tests cover forged labels, bilingual fact retention, three preparation passes,
raw-byte hash differences, duplicate and stale evidence, independent failed or
mismatched observations, content-free failures, CLI parsing, and token budgets
that count metadata and escaping. These are deterministic synthetic controls,
not an actual model's resistance or forgetting measurement.
