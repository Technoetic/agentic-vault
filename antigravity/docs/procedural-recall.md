# Read-only procedural recall

`vault_procedures.py` looks up bounded **procedure candidates** in an explicitly
selected, host-authored JSON catalogue. It does not discover procedures by
scanning a vault, call a model, execute a step, promote memory, or modify files.
It borrows the idea of small skills and an explicit execution DAG; it does not
implement SPEM or infer prerequisites from co-occurrence.

The catalogue is untrusted data. A `verified: true` field is rejected rather
than accepted as evidence. Every returned candidate requires review and has
`applies_changes: false`. Preconditions are displayed declarations: their
presence does not establish satisfaction, authorization, or tool availability.

## API and CLI

```python
import vault_procedures

result = vault_procedures.lookup(
    vault, 'input/procedures.json', 'recovery', 'recover failed queue',
    conditions={
        'model': 'fixture-model',
        'provider': 'fixture-provider',
        'token_budget': 2048,
        'tools_sha256': canonical_tools_hash,
    }, limit=5, max_bytes=32768)
```

`canonical_tools_hash` means
`vault_evidence.digest(vault_evidence.encode(inspected['manifest']['tools']))`,
using a current `vault_runs.inspect` result. It is **not** the byte hash of
`tools_source`; JSON whitespace and canonical parsed contracts are different
bindings. Conditions must match both the catalogue entry and each native run.
The host declares the intended runtime conditions; lookup does not introspect
an actual model session or grant permission to use a tool.

```console
python skills/agentic-vault/scripts/vault_procedures.py --vault /path/to/vault lookup --catalog input/procedures.json --task-class recovery --query "recover failed queue" --conditions input/current-conditions.json --limit 5 --max-bytes 32768
```

The selected conditions JSON uses exactly the four API condition fields. The
CLI rechecks its bytes and policy before printing. Invalid CLI arguments and
operational failures produce content-free JSON on stdout, exit 2, without
echoing arguments, selected source content, exception text, or paths.

## Catalogue v1

The following illustrates the structure. Replace the explanatory bindings and
run ID with actual values from a **current native completed run**; this snippet
is not a success receipt or a ready-to-run catalogue.

```json
{
  "version": 1,
  "procedures": [{
    "id": "recover-queue",
    "task_class": "recovery",
    "cues": ["recover failed queue"],
    "preconditions": ["Owner is available"],
    "steps": [
      {"id": "read", "instruction": "Read current evidence."},
      {"id": "check", "instruction": "Check the queue state."},
      {"id": "ask", "instruction": "Ask the owner before changes."}
    ],
    "edges": [
      {"before": "read", "after": "check"},
      {"before": "check", "after": "ask"}
    ],
    "candidate": {"path": "notes/candidate.md", "sha256": "ACTUAL_SHA256", "size": 123},
    "instructions": [{"path": "notes/instructions.md", "sha256": "ACTUAL_SHA256", "size": 234}],
    "run_ids": ["ACTUAL_NATIVE_RUN_ID"],
    "conditions": {
      "model": "fixture-model", "provider": "fixture-provider",
      "token_budget": 2048, "tools_sha256": "ACTUAL_CANONICAL_TOOLS_SHA256"
    }
  }]
}
```

All object fields are exact; additional fields are rejected. Bindings contain
canonical vault-relative Markdown paths, lowercase 64-character SHA256 values,
and exact byte sizes. Candidate and instruction bindings must match each native
run's candidate and selected instruction sources. The ordered `steps` array is
the host's proposed execution order. Edges must reference existing distinct
step IDs, be unique and acyclic, and agree with that order. **Procedure execution
edges are not memory-candidate ancestry.** Neither ancestry nor co-occurrence
is consulted or converted into a dependency edge.

Every step's instruction must be an exact substring of a currently bound
candidate/instruction Markdown file. Output cites the matching file binding.
This prevents changing a step to unrelated text while reusing an old receipt.
It is only a textual binding: substring presence does not prove that the
proposed order was executed, that a person reviewed the procedure, that the
text is true, or that a short quotation has sufficient context. Hosts must
review context and applicability before use.

## Current evidence, filtering and limits

Each run ID is checked with native `vault_runs.inspect`, including current
configuration, source, report and evidence bytes. Prepared-only receipts,
unavailable/stale receipts, mismatched candidate/instruction bindings, or
mismatched model/provider/tool/token conditions cannot produce a match. Any
error outcome or failed assertion, including a soft assertion, excludes the
candidate. One successful run cannot override another listed failed run.
These are current **host-recorded observations**, not independent truth,
identity, human-review or complete-procedure-execution certification.

The existing `vault_runs.Store` policy, stable reads, canonical path checks,
deny/exclude zones, reserved stores, `.env` restrictions and sensitive-content
checks are reused. Matching reads only the selected catalogue and selected
bounded bindings/receipts. BM25 is reused over catalogue cues, preconditions
and step text within the requested task class; there is no embedding or API
call. Current catalogue/policy, returned source bindings and successful runs
are rechecked before returning. This is a checked snapshot, not a filesystem
transaction or guarantee that files cannot change after return.

Bounds: catalogue JSON at most 64 KiB; 32 procedures; 8 cues and 8
preconditions per procedure; 16 steps; 64 explicit edges; 8 instruction files;
4 run IDs per procedure and 16 distinct run IDs per catalogue. Bound source
sizes total at most 2 MiB across the catalogue, with 256 KiB per source.
Native receipt/evidence limits still apply to every inspected run. Query text
is at most 512 characters. Result limit is 1–10. Encoded JSON output is bounded
to 512–65536 bytes, default 32768; an impossibly small envelope fails closed.

`status` is `matches` or `no_match`; `matches` contains candidates with their
steps, preconditions, reuse conditions, bindings, native evidence and
uncertainty. `excluded` contains only candidate IDs and content-free reason
codes. No match is a normal result. Results omitted for the byte budget set
`truncated: true` with `reasons: ["output_byte_budget"]`; result-count omissions
use `result_limit`. A truncated empty list must not be interpreted as evidence
that no relevant procedure exists. Catalogues with no procedures are valid.

All successful responses include `authority: "advisory"`,
`requires_review: true`, and `applies_changes: false`. Source text remains data
and does not authorize any execution or scope change.

## Verification

```console
python -B -m unittest discover -s tests -p test_vault_procedures.py -v
python -B -m unittest discover -s tests -p test_vault_runs.py -v
```

Tests create native prepared/completed receipts in temporary vaults and cover
source and policy changes, denied/credential paths, schema/count/byte bounds,
condition mismatches, no-match behavior, malformed DAGs, failed/error and
conflicting observations, step textual binding, output truncation, CLI errors
and the absence of writes during lookup.
