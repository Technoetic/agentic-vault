# Optional ontology contracts

Use an explicit JSON sidecar to describe what selected business entities,
directed relations and metrics mean. The helper binds those host-authored
annotations to current Markdown, then checks a structured plan against them.
It is an optional Python3.10+ standard-library helper, not a new storage engine.

Existing aliases, source proofs, dependency checks and revocation remain useful.
The new contract adds stable entity IDs, declared relation types/direction,
literal metric dimensions and a shared read-only preflight surface. No default
hook, recall behavior, existing memory record schema or vault configuration is
changed. Active installation updates and real-vault configuration are separate.

## A fictitious example

The complete small vault is in
[`tests/fixtures/ontology_contracts`](../tests/fixtures/ontology_contracts).
All names, definitions and dates are fictitious. Its Markdown says Machine Alpha
is located in Factory One; good output counts accepted pieces while planned
output counts planned pieces. Both metrics have the alias `production`.

Run these checkout-relative commands:

```text
python skills/agentic-vault/scripts/vault_ontology_cli.py --vault tests/fixtures/ontology_contracts capture --input 00-meta/ontology/declarations.json
```

Capture prints a source-bound registry; it does not save or edit anything.
To run the remaining example without changing the checkout, copy the fixture to
a temporary directory and explicitly save the output bytes there. This avoids
PowerShell5's UTF16 redirection and preserves source-byte hashes on all platforms:

```python
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile

cli = Path("skills/agentic-vault/scripts/vault_ontology_cli.py").resolve()
with tempfile.TemporaryDirectory() as directory:
    vault = Path(directory).resolve() / "demo"
    shutil.copytree("tests/fixtures/ontology_contracts", vault)
    base = [sys.executable, str(cli), "--vault", str(vault)]
    captured = subprocess.run(base + ["capture", "--input",
        "00-meta/ontology/declarations.json"], capture_output=True, check=True)
    (vault / "00-meta/ontology/registry.json").write_bytes(captured.stdout)
    for operation in (
        ["check", "--registry", "00-meta/ontology/registry.json"],
        ["search-terms", "--registry", "00-meta/ontology/registry.json",
         "--text", "production"],
        ["get-term", "--registry", "00-meta/ontology/registry.json",
         "--id", "metric:good"],
        ["check-answerable", "--registry", "00-meta/ontology/registry.json",
         "--input", "complete-plan.json"],
    ):
        observed = subprocess.run(base + operation, capture_output=True)
        result = json.loads(observed.stdout)
        print(observed.returncode, result["status"])
```

Expected: `0 current`, `1 ambiguous`, `0 matched`, `0 ready_for_evidence`.
The ambiguous search returns both metric IDs and no usable `context`. The
complete plan selects the good-output metric and explicitly names its unit,
grain, population, aggregation and half-open calendar period. Remove a required
slot and the result becomes `needs_clarification`, again with empty context.
Changing even an unrelated byte in the bound Markdown invalidates the old
registry; `get-term` then returns `unavailable` until an explicit new capture.
The published example is exercised by `tests/test_ontology_example.py`.

## Declaration schema v1

Exact root keys are `schema_version` (integer1), `ontology_id`, `version`,
`types`, `entities`, `relations`, `links`, `metrics`. IDs are lowercase ASCII
`[a-z][a-z0-9_.:-]{0,95}`. A version is a host-authored nonempty label; it does not
hide edits when left unchanged. Types are unique host-declared schema categories.
Record IDs across entities, relations, links and metrics must be globally unique.

Every record has `review` (`candidate` or `reviewed`) and one to four `sources`.
Each source is exactly `{path,line,quote,expected_sha256?}`: relative Markdown
path, positive integer physical line, literal quote and optional expected raw
file SHA256. Quotes split only on LF; CR in CRLF is literal and must be present
in the quote. Captured proofs retain raw file binding and a `span_sha256` over
literal UTF8 quote bytes, without newline/Unicode normalization.

| Collection | Exact record keys |
|---|---|
| entities | id, type, label, aliases, review, sources |
| relations | id, label, domain, range, review, sources |
| links | id, subject, predicate, object, review, sources |
| metrics | id, label, aliases, definition, unit, grain, population, aggregation, review, sources |

Entity types and relation domain/range must reference declared types. Links must
reference existing entities/relations with the correct direction and types;
duplicate triples are invalid. Metric dimensions are nonempty strings treated
as literals; the helper does not execute SQL, formulas or validation expressions. Definitions are
source-bound annotations; matching quote bytes does not prove the annotation is
semantically justified. Review state is supplied by the host, not an identity,
signature, approval capability or a status inferred from source text.

At most32 types and128 records total are accepted. Generic JSON work is bounded
to8192 primitive/container nodes, depth12, lists512, dictionaries32 keys and a
64KiB canonical encoded size including the output newline. Schema-specific
limits remain stricter (for example128 aliases per record,4 proofs and32 plan
items). Existing source readers additionally limit unique files, bytes per file
and aggregate source bytes. Compiled proof metadata can make an otherwise small
declaration exceed the output budget; this fails closed.

## Currentness and whole-registry failure

Capture binds the exact declaration file, config, source files and quoted spans.
Check validates strict cached shapes/digest, then re-reads and recompiles the
current declarations under current policy; it does not trust a self-authored
cache checksum. A new digest is not evidence of a verified author or true fact.

Deny/exclude rules, `.git`, symlink/reparse/hardlink restrictions, configured
revocation, and unconditional `00-meta/.agentic-vault/runtime` exclusion apply
before input data is read. Capture/check do not scan the vault. The runtime
guard also applies before configured revocation inputs can be read.

Any malformed, stale, missing, blocked or revoked definition invalidates the
entire registry, including an unselected record: no silent dropping into a
partial pass. All lookups/preflight revalidate that complete registry. Current
policy and exact declarations are part of currentness even when the version
label or quoted text is unchanged. Sources are rechecked after building results;
CLI JSON inputs are checked before that final sweep and metadata-swept afterward.
These are point-in-time observations, not an OS-enforced multi-file freeze.

## Structured plan preflight

`check-answerable` accepts an exact object with `schema_version` (integer1),
`entities`, `relations`, `metrics`. It does not parse a natural-language question.
The host must supply the required plan; its interpretation of a question is not
authenticated by the helper.

- `entities`: unique explicit entity IDs.
- `relations`: unique `{subject,predicate,object}` triples, supported by a
  reviewed link and reviewed dependency closure.
- `metrics`: unique `{id,slots}` objects. Slots must explicitly contain `unit`,
  `grain`, `population`, `aggregation`, `period`. The first four must match the
  metric's literal definition. Period is `{start,end}` in canonical YYYY-MM-DD,
  start strictly before end, with half-open `[start,end)` interpretation.

Unknown or missing IDs, candidate dependencies, unsupported links, missing or
conflicting dimensions, or an empty plan produce stable `gaps`,
`needs_clarification` and empty context. Malformed shapes, duplicate JSON keys,
invalid dates, extra slots and stale input produce `unavailable`.
Search uses NFC/casefold exact ID/label/alias comparison without fuzzy merging;
source text is never normalized. ID lookup of a link includes its reviewed
entities and relation. Other unrelated records are not query context.

`ready_for_evidence` means the supplied plan fits current reviewed annotations.
It proves neither that business data exists for the period nor that a value,
formula, interpretation or eventual answer is correct. No percentage,
`answerable=true`, computed value, SQL or authority to execute is returned.

## Read-only interfaces and status

API modules are `vault_ontology.py` and `vault_ontology_query.py`; the CLI is
`vault_ontology_cli.py`. All commands print canonical ASCII-escaped JSON with
LF and never write registry/source files. Host code may explicitly save output.
There is no management/delete/promotion/SQL/command execution surface.

| Command | Input | Successful status / exit |
|---|---|---|
| capture | --input relative.json | ontology_registry /0 |
| check | --registry relative.json | current /0 |
| search-terms | --registry ... --text ... | matched /0 |
| get-term | --registry ... --id ... | matched /0 |
| check-answerable | --registry ... --input plan.json | ready_for_evidence /0 |

Ambiguous, not-found, needs-review and needs-clarification results exit1.
Unavailable/invalid operations exit2 with empty context/matches/gaps. Every report
is data-only; no hashes or review labels grant execution permission. This
interface is a narrow CLI/API, not an implemented MCP server. It adds no model,
network or database dependency and does not turn on existing optional services.

## Inspiration and evaluation boundary

NVIDIA's [October21,2026 Agent Toolkit article](https://blogs.nvidia.com/blog/enterprise-platforms-agent-toolkit/)
and [Auto Ontology at2a24f5d](https://github.com/NVIDIA/auto-ontology/tree/2a24f5d34ef6b6e168a7f22d308504d3ab36a198)
inspire the business-definition, review/preflight and limited-tool patterns.
Its [Term model](https://github.com/NVIDIA/auto-ontology/blob/2a24f5d34ef6b6e168a7f22d308504d3ab36a198/auto_ontology/semantic/models.py)
and [curated tool surface](https://github.com/NVIDIA/auto-ontology/blob/2a24f5d34ef6b6e168a7f22d308504d3ab36a198/mcp/auto_ontology_mcp/tools.py)
are pinned references, not adopted code or dependencies. Typed domain/range,
file sidecars and structured literal slots are this repository's scoped
application. Existing source/alias/impact/revocation mechanisms are reused.

The upstream [coverage implementation](https://github.com/NVIDIA/auto-ontology/blob/2a24f5d34ef6b6e168a7f22d308504d3ab36a198/auto_ontology/retrieval/entity_coverage/agents/coverage.py)
measures entity-hit coverage, not answer correctness. This helper emits no such
score. Synthetic tests and the fictitious example establish code contracts;
they do not measure model search quality, hallucination reduction, company
data quality or production effectiveness. See [release scope](releases/v0.41.0.md)
and [verification](verification/v0.41.0.md) for recorded gate status.
