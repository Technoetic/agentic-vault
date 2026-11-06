# Recoverable Markdown bundles

`vault_bundle.py` is an opt-in local journal for completing a bounded set of existing Markdown replacements after an interruption. It uses the existing vault policy, stable reads, cooperating note leases and per-file staged replacement. It does not provide multi-file atomic visibility, rollback, semantic merge, or a universal power-loss guarantee. Readers can observe a partly completed bundle. Uncooperative writers and filesystem changes beyond the final observations are not frozen.

A request has exactly this shape:

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "notes/example.md",
      "expected_sha256": "<64 lowercase hexadecimal characters>",
      "edits": [{"start": 0, "end": 3, "text": "new"}]
    }
  ],
  "source_hashes": {"notes/source.md": "<64 lowercase hexadecimal characters>"}
}
```

Offsets are half-open UTF-8 **byte** offsets into the original file, following `vault_state._edited`; splitting a UTF-8 code point and overlapping edits are rejected. Unedited CRLF bytes remain intact. Empty edits and unchanged results are permitted but never authorize any different bytes. Targets must already exist: create, delete and rename are unsupported. Target and source paths must be canonical, mutually disjoint, casefold-distinct and distinct by device/inode. Existing deny zones, excluded directories, runtime reservations, hardlink/reparse checks and configuration checks apply.

Limits are eight targets, sixteen explicit sources, 128 total edits, 256 KiB per original file, 2 MiB total original target bytes, 256 KiB total UTF-8 candidate bytes and 1 MiB encoded journal bytes. Strict JSON decoding rejects duplicate keys and nonfinite values; structural/depth/encoded-byte limits are checked before persistence. Encoding overhead can exhaust the journal limit before candidate bytes do. Runtime owner records retain their existing independent 64 KiB bound.

Prepare a preview, retain its digest externally, then explicitly authorize journal creation:

```console
python vault_bundle.py --vault /path/to/vault prepare --input requests/change.json
python vault_bundle.py --vault /path/to/vault prepare --input requests/change.json --apply --expected-plan-sha256 HASH
```

The JSON request path is relative to the vault and subject to its current policy. Input aliases, repeated options and abbreviated options are rejected. The preview is read-only, including when the runtime directory does not exist. The second command rereads the request and target/source/config observations; a different plan requires a new preview. Creating a journal itself requires `--apply` and the exact digest. Retain the returned `bundle_id` and preview digest outside the journal.

Inspect, preview forward completion, or explicitly perform it:

```console
python vault_bundle.py --vault /path/to/vault status --bundle-id UUID
python vault_bundle.py --vault /path/to/vault apply --bundle-id UUID --expected-plan-sha256 HASH
python vault_bundle.py --vault /path/to/vault apply --bundle-id UUID --expected-plan-sha256 HASH --apply
python vault_bundle.py --vault /path/to/vault recover --bundle-id UUID --expected-plan-sha256 HASH --apply
```

`apply` and `recover` have the same conservative forward-completion behavior. Their defaults only preview actual states. Mutations require an externally supplied matching digest and explicit `--apply`; journal progress is never approval. Status may optionally check the external digest. API equivalents are:

```python
prepare_bundle(vault, request, *, apply=False, expected_plan_sha256=None)
apply_bundle(vault, bundle_id, *, expected_plan_sha256, apply=False)
bundle_status(vault, bundle_id, *, expected_plan_sha256=None)
recover_bundle(vault, bundle_id, *, expected_plan_sha256, apply=False)
```

The immutable digest binds sorted target paths, before/after hashes, exact UTF-8 replacement text, explicit source hashes and the configuration hash. It excludes the random bundle ID and receipts. Journal names derive solely from validated lowercase UUID32 IDs under `00-meta/.agentic-vault/runtime/bundle-UUID.json`. Journals contain local replacement text and must be treated as sensitive vault data; mode 0600 is best effort on Windows and does not establish an ACL guarantee. No network or model call is involved.

Every status or recovery reads **all** target states: an exact before hash is pending, an exact after hash is recognized as applied, and any other bytes are conflict. Identical before/after bytes are already complete without replacement. Missing, now-blocked or unsafe inputs are unavailable. Before each replacement and receipt update, current policy/configuration, all sources/targets, lease ownership/expiry and journal compare-and-swap observations are checked again. Cooperating source/note/journal leases are acquired in deterministic order; no held note lease is reacquired through `patch_note`. Preparation persists its journal before any note replacement. Replacements and receipts are individually staged, fsynced and replaced through existing atomic callbacks, with observed parent identities checked.

An interruption after replacing a note but before recording its receipt is recovered by the observed after hash. Repeated recovery is idempotent. A new unknown target revision halts further replacements without overwriting those unknown bytes. Recovery never automatically rolls backward or rebases edits. A changed or denied source/configuration also prevents completion; resolve the cause and prepare a new explicitly reviewed plan where necessary.

Successful preview/prepared/completed results exit 0 (a read-only pending preview also exits 0). Conflict/interrupted/review-required exits 1; invalid/unavailable exits 2. Standard output contains fixed statuses/reasons, bundle/digest/counts and target-index hashes/states, never note bodies or exception prose. An I/O interruption can leave a partial journal or staged file as evidence. Do not infer successful persistence from a failed prepare command.

Keep journals and the external digest until completion is independently checked. There is no automatic journal deletion or cleanup command. Missing/corrupt journals cannot be used to reconstruct authorization: inspect local files manually, preserve evidence and create a new reviewed request against their current hashes. An interrupted transition guard remains locked for manual handling; this tool does not force-unlock or remove guards on expiry. The underlying note leases retain their existing bounded expiry behavior. Never delete a guard merely because a bundle reports interruption.

Design reference: [ALICE, OSDI 2014 application crash consistency](https://github.com/madthanu/alice/tree/6afda210b96baa2b0b277505d72742420ba89b68), pinned conceptual reference only. Its legacy Linux/Python 2 implementation is not copied or required. This bundle applies the interrupted-operation lesson to a small cooperating-writer Markdown journal, rather than reproducing ALICE or claiming filesystem-wide crash consistency.
