# Knowledge impact inventory

This opt-in tool records explicitly declared note dependencies and checks their file versions. It does not extract claims, judge truth, or authorize actions. Hashes provide integrity checks, not authenticity or signatures. Results describe a point-in-time observation; review is needed when any owner or source changes, disappears, or is blocked.

Declare a non-empty JSON object with `schema_version: 1` and `claims`, each containing a unique `claim_id`, an owning vault-relative Markdown `note`, and a non-empty array of Markdown `sources`. Paths must be portable, allowed by the current deny and exclude policy, and contain no symlinks or reparse points. Duplicate sources within a claim and a source equal to its owner are rejected. The same source can be shared across claims.

For example, save this as UTF-8 `declarations.json` after replacing the two paths with existing allowed notes:

```json
{
  "schema_version": 1,
  "claims": [{
    "claim_id": "retention-policy",
    "note": "20-knowledge/policy-summary.md",
    "sources": ["20-knowledge/policy-source.md"]
  }]
}
```

```console
python skills/agentic-vault/scripts/vault_impact.py --vault VAULT capture --input declarations.json
python skills/agentic-vault/scripts/vault_impact.py --vault VAULT check --input inventory.json
```

Both inputs are vault-relative JSON files. Output is JSON on stdout; neither command writes a file. Save a captured inventory explicitly if needed. Capture emits `knowledge_dependency_inventory` with SHA-256 and size bindings, configuration hash, and `inventory_sha256`, computed over the canonical compact ASCII JSON encoding of the remaining fields with sorted keys. Check emits `knowledge_impact_review`, `authority: data_only`, claim and source statuses (`current`, `changed`, `missing`, `blocked`) and sorted affected owner paths. Claim status is `current` or `review_required`. A changed or unavailable configuration, malformed inventory, race or input failure returns a structured `unavailable` result. Exit codes are 0 for capture/current, 1 for review required, and 2 for unavailable.

Inputs are bounded to 64 KiB JSON, 128 claims, eight sources per claim, 64 unique files and 512 KiB per file. Shared memory guards also limit total read bytes and input depth. Capture reserves two trailing bytes for portable CRLF stdout and rejects inventories that exceed the JSON or schema limits, so successful output can be used as check input. Store captured output as UTF-8; a shell's default redirected encoding may differ. Capture rechecks every file and the configuration; check repeats observations, including missing and blocked paths, and rechecks successfully read files and the configuration. A file changing during a read makes the result unavailable. Concurrent changes after the final observation remain possible.
