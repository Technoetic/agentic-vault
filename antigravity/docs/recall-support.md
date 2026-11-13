# Authored evidence observations

`vault_recall_support.assess_support(recalled, contract)` is an opt-in, pure
Python 3.10+ API. Give it the result of `vault_recall.recall` and a caller-authored
contract. It performs no file, network, subprocess or additional retrieval
operation, and changes neither argument. Default recall output stays unchanged.

```python
from vault_recall_support import assess_support

contract = {
    "schema_version": 1,
    "requirements": [{
        "id": "review",
        "alternatives": [{
            "path": "20-knowledge/permit.md",
            "line": 4,
            "quote": "permit requires review",
        }],
    }],
    "blockers": [],
}
observation = assess_support(recalled, contract)
```

The three top-level contract keys above are exact. Each requirement or blocker
has exactly `id` and `alternatives`; each alternative has exactly `path`, `line`
and `quote`. Requirements are AND; alternatives are OR. A blocker is separately
observed negative evidence, never a missing requirement. Callers must author
quotations and expected source positions independently of retrieval results.

Evidence counts only when the entire case-sensitive literal quote occurs in one
actually emitted snippet under its matching `[Source: path:line]` label and the
same match metadata. Paths and original source lines must match exactly. Titles,
unemitted match metadata, another record with the same quote, and text assembled
across snippets do not count. The parser checks block structure, match order and
rendering counters; malformed attribution or injected multiline snippets fail
validation. Match metadata is an input binding, not authenticated provenance.

A full quote still visible inside a shortened block can count; a quote whose
tail was cut cannot. Output completeness remains false for shortened output.
Leading and trailing `...` are conservatively excluded from evidence, including
literal edge ellipses: recall cannot distinguish those from generated crop
markers. The context renderer's added suffix is removed first, then the retained
prefix is intersected with the original snippet's eligible character interval.
This also excludes a crop marker when only its first one or two periods survived
context truncation. Other fully visible source characters remain eligible.

The result contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `observed_requirement_ids` | Requirement IDs with an observed alternative, in contract order |
| `missing_requirement_ids` | Requirement IDs without an observed alternative |
| `observed_blocker_ids` | Blocker IDs with an observed alternative |
| `scope_complete` | Recall's declared `search_complete`, independent of output |
| `output_complete` | No selected match omitted or shortened by context rendering |
| `observation_state` | `blocker_observed`, else `requirements_missing`, else `requirements_observed` |
| `evidence` | First observed alternative per entry: `kind`, `id`, `path`, `line`, `quote_sha256`, `block_sha256` |
| `limitations` | `literal_observation_only`, `source_freshness_unverified`, `permission_not_assessed` |

Hashes use SHA-256 of UTF-8 bytes: `quote_sha256` binds the authored quote;
`block_sha256` binds the full emitted label, newline and visible snippet. Raw
quotations are not echoed. Hashes do not authenticate the caller or source.
Observed evidence can coexist with partial scan coverage or incomplete output.
`scope_complete` refers to recall's configured eligible scan, not every document
or every possible fact. `output_complete` refers to selected matches only, not
all ranked candidates or whole source documents. Even both true do not imply
semantic support, source freshness, authority, task sufficiency or permission.

Invalid inputs raise `SupportValidationError`, a `ValueError` subtype, with only
`invalid_contract`, `invalid_recall` or `recall_unavailable`. Non-`ok` recall
status (including policy/revocation failure) is rejected. Partial scans with an
`ok` status remain assessable. A caller cannot replace failure with an empty
observation and call that success.

Contracts require 1–128 requirements, 0–128 blockers and 1–16 alternatives per
entry. IDs use 1–64 ASCII letters/digits, periods, underscores or hyphens, starting
with a letter/digit; they are unique across both lists. Duplicate alternatives
are rejected. Quotes contain 1–500 characters with non-whitespace content and no
control, format or line separator characters. Lines are integers 1–2147483647,
excluding booleans. Paths use `/`, end in `.md` (case-insensitive), have at most
1024 characters, and obey the portable vault path rules; control/format
characters, absolute paths, backslashes and traversal are rejected. Bracketed
filenames are safe because labels are matched exactly against validated metadata.
Path identifiers are validated without accessing or applying a vault's policy.

The API accepts plain built-in JSON-shaped objects only, with finite numbers,
bounded integers, at most 20 container levels, 50000 nodes and 2097152 total
string characters per argument. Cycles, custom objects and unpaired surrogates
are rejected. Shared acyclic containers are permitted and counted each time.
Recall context is bounded to 262144 characters and 50 matches. Recall results
must contain exactly `context`, `matches`, `diagnostics`; extra nested advanced
recall metadata is permitted within these bounds. As this API takes an already
parsed dictionary, duplicate JSON keys must be rejected by the caller's JSON
loader before invocation; a dictionary has already lost that information.

Run the focused checks with:

```console
python -m unittest discover -s tests -p test_recall_support.py -v
```
