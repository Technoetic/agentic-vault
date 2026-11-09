# Explicit memory revocation and selective preservation

This opt-in feature masks **declared, source-bound line spans and declared
derivations** at supported consumption endpoints. It does not infer equivalent
facts, discover every paraphrase, rewrite notes, erase Git history or retract
copies already outside the vault. A natural-language request must first become a
reviewable selection of source spans; the registry is not an authorization source.

Without `memory_revocation_file`, existing output and normal hooks are unchanged.
No database, model, network call, background watcher or automatic configuration
change is introduced. Introduced in v0.37.0; updating an active installation and activating a real-vault
registry remain separate host actions.

## Scope and existing mechanisms

Source-path exclusions and lesson `RETIRE` already exist. Retirement preserves
text and history; the new registry supplies an explicit use-withdrawal scope.
Source bytes and line numbers remain unchanged. Mixed notes retain unrelated
lines, while a revoked line is replaced only by its original newline. Granularity
is a whole declared line, not an individual fact embedded in that line.

The following endpoints read the same optional registry:

| Endpoint | Enabled behavior |
| --- | --- |
| Lexical, BM25, hybrid and wiki-graph recall | Mask before ranking and snippets; keep original line numbers and raw source hashes |
| `vault_memory.compile_units` | Reject proofs whose original quote overlaps a masked line; retain unaffected proofs |
| `vault_procedures.lookup` | Recheck bound procedure instructions after masking; exclude procedures with unsupported steps |
| `vault_assembly.extract/assemble` | Exclude revoked ledger rows while retaining later unaffected rows and original hash/line identity |
| SessionStart startup/resume/compact | Mask before normalization and budget truncation, including configured compact tasks/constraints |
| New selected OKF exports | Mask the new projection and retain raw source fingerprints; do not alter earlier exports |
| `vault_links.propose_links` | Mask target snippets; refuse diffs for an affected source so review neither discloses withdrawn context nor proposes physical deletion |
| Jev selected vault excerpts and Jarvis direct context (v0.44.0) | Project full raw UTF-8 before normalization/prefix limits; retain a source/config/policy-bound envelope through pre-dispatch and post-response checks |

See [configured egress and synthetic lifecycle checks](revocation-egress.md) for
the two added context boundaries, generated-order tests and the simulated sink's
limits. Already-sent information is not withdrawn.

Other direct file readers, audit/history/diff tools, backups, external memory
services and arbitrary host tool calls are not an engine-wide forgetting boundary.
Unregistered copies remain unverified. These endpoints enforce only the currently
configured registry; edit rights to the configuration/registry remain a host trust
boundary. Hashes bind bytes, not truth or user authority.

## Declaration and capture

Create a UTF-8 declaration JSON in an allowed location. Paths are vault-relative
Markdown files. Lines are inclusive, one-based physical LF/CRLF lines; Unicode
line separators are not alternative physical line boundaries.

```json
{
  "schema_version": 1,
  "nodes": [
    {"id": "old-procedure", "path": "20-knowledge/procedure.md", "start_line": 6, "end_line": 7},
    {"id": "summary-copy", "path": "00-meta/hot.md", "start_line": 12, "end_line": 12}
  ],
  "derivations": [{"parent": "old-procedure", "child": "summary-copy"}],
  "revoked": ["old-procedure"]
}
```

Capture validates selected paths, current policy and bounds, adds `sha256` to each
node, verifies source/config freshness and emits a registry without writing it:

```console
python skills/agentic-vault/scripts/vault_revocation.py --vault PATH capture --input 00-meta/revocation-declarations.json
```

The host reviews the selection and saves the captured JSON as UTF-8 in a new
allowed file, then explicitly configures:

```json
{"memory_revocation_file": "00-meta/revocations.json"}
```

An empty string disables the feature. No config key is injected when absent.
PowerShell 5 redirection may write UTF-16; use an explicit UTF-8 writer rather than
redirecting the JSON with `>`. For example, from an environment with the plugin's
`skills/agentic-vault/scripts` on `sys.path`:

```python
import json
from pathlib import Path
from vault_revocation import capture_registry

vault = Path("YOUR_VAULT").resolve()
declarations = json.loads((vault / "00-meta/revocation-declarations.json").read_text(encoding="utf-8"))
registry = capture_registry(vault, declarations)
with (vault / "00-meta/revocations.json").open("x", encoding="utf-8", newline="\n") as output:
    json.dump(registry, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
```

The writer example is a deliberate host action; the engine does not install or
activate a registry. Verify it explicitly:

```console
python skills/agentic-vault/scripts/vault_revocation.py --vault PATH check --input 00-meta/revocations.json
```

`check` validates all source bindings and reports the selected graph closure. It
does not activate its input unless configuration points to that file. Capture
and check exit 0 on success and 1 with content-free JSON on invalid input; no note
payload or raw exception is emitted. Successful CLI JSON intentionally has no
trailing newline, so a registry at the 64 KiB limit can be saved and checked
without exceeding that limit. The Python API raises `RevocationError`.

## Safety and availability

Registry JSON has strict fields, unique keys, 64 KiB serialized size, at most128
nodes/source files and256 edges. IDs are bounded ASCII identifiers. Same-file
nodes share one fingerprint; unknown edges and inconsistent bindings are rejected.
Cycles terminate through a visited set. Reads use existing path, deny/exclude,
reparse-point, regular-file and hardlink guards.

Each source is at most4 MiB; a policy reader has a cumulative32 MiB read budget,
including freshness checks. CLI capture can use two readers, at most64 MiB total.
Other consumer-specific limits still apply. With revocation enabled, SessionStart
reads and checks the whole source under the 4 MiB cap before its 256 KiB per-note
output truncation. A stale affected source is omitted or rejected as a whole
rather than interpreting old line offsets
in new text. Refreshing its binding requires a new reviewed capture; refreshing a
hash must not silently approve moved content.

A missing, malformed, unsafe or changed configured registry fails closed. Consumers
recheck registry, sources and current configuration before returning context or
publishing a new bundle. This is a bounded point-in-time check, not a filesystem
freeze. The registry contains paths, IDs, hashes and ranges rather than revoked
text; identifiers themselves still require an appropriate disclosure policy.

## Tests and research connection

```console
python -B -m unittest discover -s tests -p test_vault_revocation.py -v
python -B -m unittest discover -s tests -p test_memory_revocation_integration.py -v
python -B scripts/evaluate_memory_lifecycle.py
```

[Bemula](https://arxiv.org/abs/2610.18052) motivates paired stored-target controls,
selective removal, unrelated-memory preservation and relearning checks.
[Memora](https://arxiv.org/abs/2604.20006) motivates inspecting forbidden-memory use
as well as correct recall. This implementation uses original synthetic fixtures,
not copied benchmark datasets or an official benchmark reproduction. The public
Bemula repository did not expose a license at review time; its code is not copied.
See [cross-process lifecycle controls](memory-lifecycle.md) for measured scope.
