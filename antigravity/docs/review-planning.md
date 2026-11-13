# Declared dependency review planning

`vault_review_plan.py` builds a read-only worklist from an existing `vault_impact.py` inventory and a caller-declared complete dependency graph. It does not infer truth or discover lineage. A graph declaration is not authentication that all real dependencies have been supplied.

```console
python skills/agentic-vault/scripts/vault_review_plan.py --vault /path/to/vault --inventory inventory.json --graph graph.json
```

Both inputs must be explicit, distinct, safe relative JSON files inside the vault. No output-file, recapture, edit, model, subprocess or network action is supported. Exit codes are 0 current, 1 review required and 2 invalid/unavailable. `--help` describes arguments. Duplicate options, abbreviated flags, duplicate JSON keys, nonfinite numbers and unsafe input aliases are rejected. Error output uses fixed codes, never exception text or note bodies.

## Graph and API

```json
{"schema_version":1,"dependencies":[
  {"claim_id":"A","depends_on":[]},
  {"claim_id":"B","depends_on":["A"]},
  {"claim_id":"C","depends_on":["B"]}
]}
```

Every inventory claim must appear exactly once, including roots with empty parents. There are at most 128 claims and eight parents per claim. Unknown, duplicate and self edges, cycles, missing claims and extra schema fields invalidate the entire plan. Identifiers are exact inventory identifiers; names and aliases are not merged.

`plan_review(vault: Path, inventory: dict, graph: dict) -> dict` snapshots bounded JSON inputs before validating them, then rechecks caller input bytes before returning. Successful results have type `knowledge_review_plan`, authority `data_only`, schema version 1, config/inventory/graph digests, `claims`, `review_order`, `affected_notes` and `actions`. Graph digest binds the supplied JSON declaration; changing list order changes that digest even when the dependency relation is equivalent.

Each claim preserves the exact existing impact result in `direct`, including owning-note and individual-source statuses. `inherited_status` and `upstream` explain the additional review requirement. For each directly affected ancestor, the planner retains one shortest dependency path, breaking equal-length ties lexicographically. It never enumerates all possible paths. Explanations traverse at most 128 roots by 128 nodes by eight parents, with paths bounded by 128 nodes. Output exceeding 64 KiB, including portable CRLF newline allowance, becomes unavailable with no usable partial order.

If A's source changes, B and C require review even when their own Markdown bytes remain identical. `review_order` uses a lexicographically stable topological ordering of affected claims. Owning notes are sorted and deduplicated. `review_then_recapture` actions are data for a caller to consider, not commands or authorization to execute. Direct and inherited causes coexist; unrelated branches remain current.

## Guard scope and limitations

The planner reuses inventory schema validation, actual `check_inventory` direct results and current policy/read guards. It observes source content hashes and identities before direct checking and after worklist construction, and rechecks policy/configuration and observed bytes. Missing and blocked sources retain their direct review requirement and propagate it without bypassing deny rules. Blocked files use only policy-permitted metadata observations; denied bytes are never opened. Changed observations, configuration or CLI input identity/bytes invalidate the whole result. Existing source-count, byte, runtime, deny/exclude, hardlink and reparse restrictions remain in force; adding graph capacity does not expand those limits. JSON input/output is limited to the existing 64 KiB budget.

Checks are point-in-time observations. A/B/A changes or malicious ancestor replacement outside final observations are not prevented, and no filesystem freeze is claimed. Inventory bindings establish byte versions, not semantic correctness. Ontology definitions may be represented by claims owned by Markdown notes; whole-registry recapture and revalidation remain separate caller operations. Existing ontology registry validation is unchanged.

The conceptual reference is DVC's declared computation graph, previously researched generally and now applied specifically to explicit review propagation: [`treeverse/dvc` pinned source](https://github.com/treeverse/dvc/tree/1c06f9860af61d3947adafac99d8206f713dbfdd), formerly `iterative/dvc`. No DVC code is copied and no computational execution or cache semantics are implemented.
