# Selected knowledge exchange and change review

The approved first implementation is an opt-in change-impact inventory, selected-note OKF export, and a small offline longitudinal fixture. The user approved this scope with “구현해” after the comparison. Existing vault storage, startup hooks, authority, warning certificates and default retrieval remain unchanged. This work is local implementation; release, network push, host installation and company-vault conversion are separate operations.

## Approach

Reuse the existing Python path/config/stable-read guards. Add independent CLI modules rather than install the OpenWiki runtime. OpenWiki's versioned claim evidence and OKF v0.2 motivate the behavior; this is an original scoped implementation, not upstream code transplantation. Copying the upstream framework would introduce a second runtime and overlapping memory policies; replacing the vault format would break existing wiki-link rules. These alternatives were considered in the preceding comparison; the selected approach preserves the existing vault and adds explicit tools.

### Change impact

`vault_impact.py` exposes `capture_inventory(vault: Path, declarations: dict) -> dict` and `check_inventory(vault: Path, inventory: dict) -> dict`. Declaration schema v1 has `claims`, each with a unique `claim_id`, an owning vault-relative Markdown `note`, and a non-empty list of vault-relative Markdown `sources`. Dependencies are explicitly host-authored; the tool does not discover claims or infer their truth. Capture binds current config, note and source SHA256/size using policy-safe reads. It emits a portable JSON inventory, without writing notes or a runtime store. Check returns stable-ordered claim/source diagnostics and affected owning notes. Changed, missing, blocked, malformed and current states must be distinguishable. Owner drift also requires review. Unavailable policy/config or an invalid inventory must never return an empty successful review. All captured dependencies are rechecked before success. No source quotes or contents in diagnostic output. CLI: `--vault PATH capture|check --input RELATIVE.json`, JSON on stdout, exit 0 for complete current success, 1 for a valid review with changes, 2 for invalid/unavailable input. JSON baseline checksums are integrity signals, not signatures or authorization.

### OKF export

`vault_okf.py` exposes `export_bundle(vault: Path, notes: list[str], destination: Path) -> dict`. It reads only explicitly selected allowed Markdown paths. The output is a new directory outside the vault, never an overwrite or a note mutation. The bundle has a root index declaring `okf_version: "0.2"`, concept Markdown with a non-empty `type`, standard relative Markdown links between selected concepts, and a JSON manifest. It preserves original frontmatter in a producer extension, rather than silently equating internal status/dates/review labels with OKF meanings. No invented human verification or generation timestamps. Manifest records source/output hashes, path mapping, profile/spec commit and every known conversion loss. Ambiguous/missing links or unexported embeds are reported without reading their targets; preserve code literals and fences. Reserved concept filenames, traversal, duplicate/case-folded collisions, deny/exclude/symlink escapes, existing destinations and source/config drift fail safely. A failed run must not publish a partial destination. This is a minimal producer profile, not certification of all OKF document types, full semantic equivalence, safe executable computation, or authorization to share data.

### Offline evolution fixture

`scripts/evaluate_knowledge_evolution.py` and checked-in fixtures replay small, independently authored source checkpoints in temporary synthetic vaults. Exercise real capture/check/export behavior over unchanged, changed, missing and restored evidence and owner drift. Separately classify explicit structured claims against authored current/historical fact maps into supported, stale, invented or unverified. Current contradiction plus past support is required for stale; an evidence hash change alone is not falsehood. Show per-checkpoint counts, pooled denominators, and coverage/completeness limitations. No LLM calls, network, production-vault mutations or claimed semantic extraction from prose. Generated output is not used to construct its own expected labels.

## Boundaries and acceptance

- Python standard library only; use the existing library guards. Tests use local synthetic fixtures and real filesystem operations.
- Bounded input counts, file sizes, JSON depth and total bytes; errors never echo hostile paths or secrets unnecessarily.
- Concurrent/source/config mutation is rejected or explicitly reported; a stale inventory never grants permission.
- Existing commands and default hooks retain their behavior and byte-bound certificate inputs.
- Package and both host skill documentation expose the opt-in commands; implementation success is not installation or publication.
- Meaningful tests first, observed failure, implementation, focused tests, full unittest discovery, independent review and local integration.
