# Handoff folding

`vault_handoff_fold.py` shrinks a long `handoff_note` by moving old sections, byte for byte, into archive notes. A short stub stays where each section was. `analyze` reads only, and `apply` alone writes. `apply` needs the hash `analyze` reported. Nothing runs by itself: no hook, session command or schedule calls this tool. A subcommand is required, and a call without one exits 2 with `invalid_arguments`.

Host-agnostic Python 3.10+ standard library. No network, model or subprocess is used.

```console
python skills/agentic-vault/scripts/vault_handoff_fold.py --vault /path/to/vault analyze --threshold-bytes 262144 --protect-sections 20
python skills/agentic-vault/scripts/vault_handoff_fold.py --vault /path/to/vault apply --threshold-bytes 262144 --protect-sections 20 --expected-sha256 HASH
python skills/agentic-vault/scripts/vault_handoff_fold.py --vault /path/to/vault apply --threshold-bytes 262144 --protect-sections 20 --expected-sha256 HASH --summaries 00-meta/scratch/summaries.json
```

`--threshold-bytes` and `--protect-sections` have no default and are required for `analyze` as well as `apply`. A run without one exits with `fold_threshold_required` or `fold_protect_required`. Below the threshold nothing happens; a handoff of exactly that size is eligible. `--newest top|bottom` (default `top`) says which end of the file is recent. `--archive-max-bytes` defaults to 256 KiB. `--folded-at` fixes the stub time; it defaults to the current UTC time.

Exit codes are 0 for a completed run, including `below_threshold`, `nothing_to_fold` and `no_reduction`; 1 for a refused or failed run, with `{"error":"<code>"}` on stderr; and 2 for invalid arguments. Errors are fixed codes and never carry paths or note text. `apply` without a hash exits with `expected_sha256_required`, and a changed handoff with `stale_base`; run `analyze` again.

## What moves and what stays

Sections start at level-two headings outside code fences, using the same heading and fence rules as `hooks/session_start.py`, on raw bytes so that no line ending changes. Frontmatter and everything before the first section stay. So do the newest N sections, sections with a title in `protect_titles`, sections holding the managed handoff block, earlier stubs and the collected section. Every other section is a candidate; there is no closed-state or age rule.

A folded section keeps its heading line and gains one stub line:

```markdown
## 2031-02-27 Write path limits
- [handoff-fold] 2031-04-01T00:00:00Z sha256:0f1e2d3c4b5a [[Lantern handoff archive 0003]] unresolved=2
```

The line holds the fold time, the first twelve hash characters, the archive link and the unresolved line count. A host summary, when given, is one more line under it. Without one, only the title and the unresolved lines stay.

Unresolved lines are copied, not removed, into one `## 미결 모음` section placed before the first folded section, each with `[fold-source: sha256:<12 hex>]` added. More deeply indented lines follow their match, stopping at a code fence, so a bare `- unresolved:` keeps its nested items. Lines inside fences do not match. Later folds extend the same section. This section preserves, and it may exceed the injection window. With `--newest top` the fresh sections sit above it for that reason. With `--newest bottom` it still goes before the first folded section, so it and the stubs sit above the fresh sections, and the session hook, which keeps the head of the note, can fill its window before it reaches them. How much of the window it takes was not measured. When this section takes the head of a file that starts with a byte order mark, the mark stays the first bytes of the file.

## Archive notes

Notes go in `handoff-archive/` beside the handoff, named `<handoff stem> archive 0001.md`, and are split by size. A section larger than the limit gets a note of its own, which can exceed the limit and the recall file limit. Existing notes are never rewritten; each fold takes the next numbers. The frontmatter uses `archive` values where the configured enums allow them, links back with `related`, and copies other required keys and `domain`/`project` from the handoff; a required key the handoff lacks stops the run with `archive_frontmatter_unavailable`.

The note body after the frontmatter is a run of frames: a begin comment with the full hash and byte length, the section bytes, and an end comment. Parsing uses the length, so section text cannot break a frame.

The stub's wiki link is the recovery path. `vault_recall.py` is a secondary route that depends on its file and total limits, so do not rely on it to find archived text.

`restore_handoff(handoff, {note name: note bytes})` rebuilds the handoff from the stubs and frames. `apply` runs the same check on its own output and refuses with `restore_mismatch` before writing. Host summaries are derived text and do not come back. A frame whose bytes changed fails with `archive_hash_mismatch`, which includes line-ending conversion, so add `**/handoff-archive/** -text` to `.gitattributes` in a vault used across platforms.

## Summaries and configuration

Run `analyze`, write summaries for any candidate hashes you choose, then `apply`:

```json
{"schema_version":1,"summaries":{"<section sha256>":"One line of host text."}}
```

The file must be JSON inside the vault, outside deny and excluded paths. A summary is one line of at most 2,048 characters. A summary for a section that is not a current candidate stops the run with `unknown_summary_section`. Summaries are not checked against the section.

Unset configuration keeps the defaults. The tool only reads this optional key from `00-meta/vault-config.json`:

```json
{"handoff_fold":{"unresolved_prefixes":["unresolved:","open item:"],"protect_titles":["▶ NEXT — next steps"]}}
```

`unresolved_prefixes` replaces the default `unresolved:`. A line matches case-insensitively after any indent, quote mark, list marker or open checkbox, ignoring backticks and asterisks. `- [x]` lines do not match. `unresolved: none` is lifted like any other line. `protect_titles` names sections by exact title, ignoring case; use it for the template's `▶ NEXT`, `§2` and `§3` headings when they are not inside the protected count.

## Guarantees and limits

`apply` takes the same advisory lock as `patch_note`, reads the handoff under its own 32 MiB limit, and rechecks the hash, lease and policy before each atomic replace. Only cooperating writers observe the lock. Archive notes and the handoff are separate replaces, not one transaction. On POSIX, `apply` syncs the archive folder and its parent before it replaces the handoff, so the notes' names reach the disk first where the file system supports folder sync. Windows has no such call. No power-cut test was run. Deny zones and exclusions apply to the handoff, the archive folder and the summary file.

A failed or interrupted run removes the notes it created only while the handoff on disk provably holds the original bytes, and it removes only files that still hold what the run wrote. Once the handoff replace may have run, the notes stay, whatever the error was, because a stub has no other copy of its section. So an error or Ctrl-C can arrive with the fold already complete: run `analyze` and look at `sha256` and `already_folded` before you try again. A killed process, or a removal the system refuses, can leave unreferenced notes behind.

On Windows a read-only handoff cannot be replaced, so the run ends with `replace_failed`, removes its notes and leaves the handoff as it was. On POSIX the read-only mode does not stop the replace, and the archive notes copy the mode.

The tool does not update the handoff's `updated` key, `index_note` or `log_note`; the caller's usual note rules apply. The handoff must be valid UTF-8. A hand edit under a stub is lost on restore, because the stub section is replaced whole. It folds nothing it cannot rebuild, including a hand-made collected section that ends the file without a newline. A fold reduces the current size only: new entries keep growing the file, so this does nothing for the rate of growth.

## After a killed run

A process killed during `apply` loses no bytes. The handoff is either unchanged or folded with its notes in place. It can leave four things behind:

| Left behind | Where | What it does |
|---|---|---|
| Guard | `00-meta/.agentic-vault/runtime/lock-<hash>.json.guard` | Never expires, on purpose. Every later `apply` and every `patch_note` on this handoff stops with `lock_busy` until it is removed by hand. |
| Lease | `00-meta/.agentic-vault/runtime/lock-<hash>.json` | Expires by itself 300 seconds after the run took it. Until then the same calls stop with `lock_busy`. |
| Staged file | `.vault-state-*` beside the handoff or in `handoff-archive/` | Does nothing. Safe to delete. |
| Unreferenced note | `handoff-archive/` | Takes up one archive number. Costs nothing. |

To recover:

1. Make sure no writer is running on this vault. A live writer holds the guard only for a moment, so a guard that stays while no process is running is a leftover.
2. Find the file name. `<hash>` is the SHA-256 of `note:` plus the vault-relative handoff path in case-folded form:

   ```console
   python -c "import hashlib,sys; print('lock-' + hashlib.sha256(('note:' + sys.argv[1].casefold()).encode('utf-8')).hexdigest() + '.json')" "50-projects/Demo/Demo handoff.md"
   ```

3. Delete `<that name>.guard` from the runtime folder. Wait for the lease to expire if one is there.
4. Run `analyze`. The same `sha256` as before means the fold did not land. A new `sha256` means it did.
5. Delete `.vault-state-*` files. Delete an archive note only when no stub in the handoff links to it. A note that a stub links to holds the only copy of that section.

## Evaluation

`scripts/evaluate_handoff_fold.py` is an offline loss alarm. It calls no model.

```console
python scripts/evaluate_handoff_fold.py
python scripts/evaluate_handoff_fold.py --control truncate
python scripts/evaluate_handoff_fold.py --control unlinked
python scripts/evaluate_handoff_fold.py --fixture local-pack.json
python scripts/evaluate_handoff_fold.py --vault /path/to/vault --probes 00-meta/scratch/probes.json
python -m unittest discover -s tests -p test_handoff_fold_evaluation.py -v
```

Each folded section goes through `vault_compile_quality.check_preservation` against what its stub reaches by following the wiki link once. A section the check cannot take, such as one over its 256 KiB input limit, counts as `skipped`. Each probe names one exact string and one of five kinds: `recall`, `artifact`, `continuation`, `decision` or `constraint`. It lands on the shortest route that holds it: `injected` (the first `window_bytes` of the handoff), `handoff`, `one_hop` or `lost`. The report has per-kind counts and probe identifiers, never note text. Exit 0 means no lost probe and no section with loss; 1 means a loss was found, which is the expected result for both controls; 2 means invalid input, reported only as `{"error":"invalid_input"}`.

The bundled pack in `tests/fixtures/handoff_fold/` is synthetic. A pack has `schema_version: 1`, a boolean `synthetic`, `settings` (`threshold_bytes`, `protect_sections`, `window_bytes`, optional `newest`), `probes` of `{id, kind, expected}` and, unless `--vault` is used, `handoff` as a list of lines. Keep probes drawn from real events as local files in the vault, never in this repository. `--vault` previews one fold in memory, follows archive notes the handoff already links to, and writes nothing.

String reachability shows that text is gone. It does not show that an agent can continue the work: a paraphrased answer reads as lost, and a reachable string can still be useless. A functional probe asks a model after the fold and scores its answer. That second stage is not part of this tool. Passing this check is not evidence that a fold is good, only that nothing checked went missing.

## References

No code was copied from these sources.

- [LCM: Lossless Context Management](https://papers.voltropy.com/LCM), paper PDF generated 2026-02-14, with [lossless-claw v1.2.0](https://github.com/Martian-Engineering/lossless-claw/tree/v1.2.0): summaries as a derived cache over an immutable source, a threshold below which nothing runs, and a deterministic fallback when no summary exists. The fallback here is title plus unresolved lines, not the paper's truncation. LCM leaves its source untouched; this tool moves bytes out of a file people edit, which is why it rebuilds the original before writing.
- [Beads `bd admin compact`](https://github.com/gastownhall/beads/blob/v1.3.4/docs/cli-reference/admin.md), v1.3.4: the analyze, host summary, apply sequence. Beads discards the original and limits compaction to closed items; this tool keeps every byte and folds sections with unresolved lines by lifting those lines.
- [Evaluating Context Compression for AI Agents](https://factory.com/news/evaluating-compression), published 2025-12-16: the recall, artifact, continuation and decision probe kinds. It is a vendor post on its own product, so only the method is borrowed. It scores model answers, which the evaluator above does not. The `constraint` kind is added here, after the loss of constraints under compression measured in [arXiv 2606.22528](https://arxiv.org/abs/2606.22528).
