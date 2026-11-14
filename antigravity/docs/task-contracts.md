# Source-bound task contracts

`vault_task_contracts.py` is an opt-in, standard-library helper for reviewing a task before execution. It records caller-supplied declarations and exact excerpts from selected local files in immutable native runtime receipts. It never executes a command, calls a model, accesses the network, or grants permission.

```text
python vault_task_contracts.py --vault <vault> capture --input input/task.json
python vault_task_contracts.py --vault <vault> inspect <contract-id>
python vault_task_contracts.py --vault <vault> check <contract-id>
python vault_task_contracts.py --vault <vault> revise <contract-id> --input input/revision.json
```

The Python API provides `capture(vault, input_path)`, `inspect(vault, contract_id)`, and `revise(vault, contract_id, input_path)`. `check` is a CLI alias for read-only `inspect`. Capture and revision are explicit writes; repeated identical capture or revision reuses the existing immutable receipt. Operational failures raise `ContractError` with a content-free code; the CLI returns JSON and exit code 2. A syntactically valid ID with missing, corrupt, changed, unsafe, or superseded bindings returns `status: "stale"` from inspection, with empty current accepted declarations. Consumers must gate execution review on `ready_for_execution_review`.

## Input

The JSON object has exactly four keys. Unknown keys, duplicate JSON keys, duplicate IDs, invalid enum values, and booleans in place of integers are rejected.

```json
{
  "version": 1,
  "task": "Local reader",
  "fields": [
    {
      "id": "goal",
      "kind": "goal",
      "text": "Create a small reader.",
      "origin": "user_request",
      "status": "confirmed",
      "sources": [{"path": "notes/request.md", "excerpt": "Create a small reader."}]
    },
    {
      "id": "scope",
      "kind": "scope",
      "text": "Only local notes.",
      "origin": "user_request",
      "status": "confirmed",
      "sources": [{"path": "notes/request.md", "excerpt": "Only local notes."}]
    },
    {
      "id": "ac-title",
      "kind": "acceptance",
      "text": "Opening a note shows its title.",
      "origin": "user_confirmation",
      "status": "confirmed",
      "sources": [{"path": "notes/request.md", "excerpt": "Opening a note shows its title."}]
    }
  ],
  "questions": [{"id": "q-format", "text": "Which optional theme?", "blocking": false, "status": "unknown"}]
}
```

Create `notes/request.md` separately with those three exact sentences. Each source excerpt must occur literally in its UTF-8 source file; hashes bind the complete file. A source cannot be the input JSON itself. All paths use a portable vault-relative spelling with forward slashes and inherit the native deny/exclude, credential-file, reserved-store, symlink/reparse-point, hard-link, stable-read, size, and secret policies. An unsafe or changed source fails closed without returning its contents.

Field kinds are `goal`, `scope`, `non_goal`, `constraint`, and `acceptance`. Origins are `user_request`, `user_confirmation`, `repository_fact`, and `host_default`. Field status is `confirmed` or `proposed`. Every field except a host default requires at least one source. IDs begin with an ASCII letter and contain at most 64 ASCII letters, digits, underscores, or hyphens; IDs are unique across both fields and questions. Task labels are bounded to 256 characters, field text and excerpts to 4,096 characters. Questions have exactly `id`, `text`, `blocking` (a JSON boolean), and `status` (`open`, `resolved`, or `unknown`).

Only confirmed fields declared as `user_request` or `user_confirmation` enter `accepted_fields`. Repository facts are returned separately; proposed fields and host defaults never become accepted requirements automatically. A confirmation still requires its explicit source declaration. These origin labels describe what the caller says: an exact excerpt does not authenticate the author or prove the excerpt supports the declaration. The helper cannot prevent a dishonest caller from mislabeling a source; reviewers retain that responsibility.

## Result and readiness

Capture, inspection, and revision return the same stable result shape:

- `contract_id` and `fingerprint`: the SHA-256 identity of the complete bound payload.
- `status`: `ready_for_execution_review`, `needs_clarification`, or `stale`.
- `task`, `fields`, `accepted_fields`, `repository_facts`, `acceptance_ids`, and `questions`: normalized declarations, sorted by ID.
- `input_source`, `source_bindings`, and `config_sha256`: current input/source/configuration byte bindings.
- `parent`: null or the previous `contract_id` and native `receipt_sha256`.
- `receipt_sha256`, `path`, `limits`, `authority: "data_only"`, `limitations`, and `historical_metadata` (normally null).

Readiness requires exactly one accepted goal, at least one accepted scope field, at least one accepted acceptance criterion, and no blocking question whose status is open or unknown. A nonblocking unknown question is allowed. Readiness means the declarations are ready for execution review; it is neither execution approval nor evidence that the task is complete.

The published limits are 64 KiB per input/receipt, 256 KiB per source, 2 MiB across input and selected sources, 64 fields, 32 questions, 64 unique source files, 64 receipts in a revision chain, 256 contracts, and 4,096 runtime directory entries scanned. Reaching a bound fails closed. Capture uses the existing native advisory lock and atomic publication path; inspection writes nothing.

## Revision and freshness

`revise` supplies the parent through its API argument. The input cannot forge a parent or receipt ID. A revision receives a new content-derived ID and preserves the previous receipt unchanged. Its parent binding includes the parent's native receipt checksum. The previous contract becomes stale when a verified child receipt exists, so evaluations must select the revision explicitly. An already superseded parent cannot acquire another different revision. Identical revision calls reuse the same child.

A revision may replace an input or source that made its parent stale; the historical parent's source files need not remain current. Its immutable native receipt and complete parent chain must still verify. Inspection rechecks current input bytes, normalized declarations, literal excerpts, selected source bytes, configuration, receipt integrity, parent receipt bindings, and supersession. Stale results clear current accepted fields, IDs, and bindings. If the receipt and parent chain verify but its source/input/configuration/supersession is stale, `historical_metadata` retains `task`, `acceptance_ids`, `accepted_fields`, `source_bindings`, `input_source`, `config_sha256`, `receipt_sha256`, and `path` for stale display and fingerprint comparison only. It never supplies current acceptance and cannot make an evaluation pass. Corrupt or unsafe receipts and parent chains have no historical metadata. A changed source, even an irrelevant trailing byte, requires an explicit new capture or revision.

Checksums detect ordinary corruption and bind data. They are not signatures, proof of source truth, authenticated identity, execution evidence, or permissions. Retain each revision's input and sources separately when historical current inspection is needed. Staged evaluation consumes the current contract's accepted acceptance IDs and retains its own evidence limitations.
