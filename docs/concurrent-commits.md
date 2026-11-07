# Concurrent commits

`vault_commit.py` commits a declared list of files from a working tree that several sessions share. It is an opt-in command: no hook, skill or session step runs it unless someone calls it, and without it nothing about a vault changes. It uses only the Python standard library and calls the `git` executable.

```console
python skills/agentic-vault/scripts/vault_commit.py --vault /path/to/vault \
  --path "20-knowledge/concepts/Example note.md=<sha256>" \
  --path "20-knowledge/concepts/Old note.md=absent" \
  --message "Update example note" --actor session-a
```

Each `--path` is `RELATIVE=SHA256` for the working file the caller means to commit, or `RELATIVE=absent` for a tracked file the caller deleted. The text splits at the last `=`. `--actor` is recorded as a `Vault-Actor:` trailer. Exit codes are 0 committed, 1 refused or failed and 2 invalid arguments. Output is one JSON object with fixed reason codes; it carries no paths or note text, and `path_index` names a refused entry by its position. `--show-git-output` copies the last failing git output to stderr, which is how to read a pre-commit refusal, and that text can hold paths.

## Why plain commit is not enough

Plain `git commit` writes the whole index as the next tree. An index that is out of date, whether a temporary one built earlier or a shared one another session committed around, removes files from the new commit with exit code 0. No race is needed: one sequential run reproduces it.

`git commit -- PATH` takes only the named paths. The Git manual describes the mode as "taking the updated working tree contents of the paths specified on the command line, disregarding any contents that have been staged for other paths". Two consequences shape this tool:

- It is safe only for paths no other session writes. It commits the whole working file, so another session's unfinished lines are committed too, including half a line with no newline.
- It holds the shared `index.lock` for the whole time the hooks run. That lock is what keeps commits from interleaving, and it is also what makes concurrent writers wait.

## What the command does

1. Validates input. Paths use the shared vault path rule, deny zones and `.git` are refused, symbolic links, junctions and Windows short names are refused, and folders are refused because a folder commit would carry whatever else is under it. Anything inside a linked working tree of the repository is refused with `denied_path`, in both modes and for the patch file, because a copy of a note or a deny zone there is not a path of this vault. Git runs from an absolute path found on `PATH`, never an implicit current folder, and without the variables that would move the commit or its gate: `GIT_DIR`, `GIT_INDEX_FILE` and the like, the pathspec variables, and `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*` and `GIT_CONFIG_PARAMETERS`, which could replace `core.hooksPath`. `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` and repository settings can still change it: this tool does not block that path, and its own tests rely on `GIT_CONFIG_GLOBAL`. Pathspecs are `:(literal)` per path: the global `--literal-pathspecs` option is exported to hooks, where it turns the pathspec magic of the pre-commit gate into plain text and the gate passes without checking anything.
2. Refuses a merge, rebase, cherry-pick or revert in progress. Unresolved conflict entries in the index are refused with `unmerged_paths`, because `git stash pop` leaves them without a marker file and `git add` would mark them resolved with the conflict lines still in the file.
3. Compares each working file with its declared sha256 on every attempt.
4. Runs `git add -- PATHS`, then `git commit -- PATHS`. Hooks run, and `--no-verify` is not available. Ignored files stay out because `git add` is not forced.
5. Reads the commit hash from the output of that commit and checks that commit: the changed paths against the declared paths, each committed blob against the blobs the declared bytes can become, and the subject against the message. HEAD is not consulted, because another session may have moved it.

The declared bytes can become two blobs: the result of the repository's filters, and the bytes unchanged. `git add` leaves CRLF alone in a file whose indexed blob already has it when `core.autocrlf` or `text=auto` applies, and `git hash-object`, which reads no index, cannot tell. Either blob comes from the declared bytes, so another writer's bytes still match neither.

A commit that differs from the declaration still exists. The report says `committed_mismatch` with `paths_differ`, `content_differs` or `message_differs` and exit code 1, and the caller inspects that commit. A declared path that had no change is a mismatch for this reason, not a skipped entry.

`nothing_to_commit` also covers the case where another session's plain commit already carried the staged file.

### Retries

| Failure | Handling |
| --- | --- |
| `index.lock`, `HEAD.lock`, a ref lock | Wait with full jitter, starting at 20 ms and doubling to `--max-delay-ms` (default 1000), then start again from the hash check. |
| `pathspec ... did not match` | Start again from `git add`. An entry staged with exit code 0 can vanish when another commit overwrites the shared index, so retrying only the commit cannot recover. |
| Anything else, including a hook refusal | Stop. |

Hook output does not decide this. A hook prints to git's stderr, so a lock or pathspec failure counts only on a line git starts with `fatal:` or `error:`, and "nothing to commit" only on git's stdout. A hook that prints `index.lock` or `nothing to commit` is reported as `commit_rejected`.

`--max-attempts` bounds the attempts (default 60). A lock is never deleted, because a live lock and an abandoned one look the same from outside. After the last attempt the report gives `index_lock_timeout` and `lock.present` and `lock.age_seconds`. Check that no git process is running before removing a lock by hand. A refused or failed commit can leave the files staged. `left_staged` is read from the index after any `git add` ran, and `git reset -q -- PATH` undoes it.

## Shared notes

The notes configured as `hot_note`, `handoff_note`, `log_note` and `index_note` are refused by default, since every session writes them and a path commit would sweep other sessions' lines. Their writers use `--own-lines`:

```console
python skills/agentic-vault/scripts/vault_commit.py --vault /path/to/vault \
  --path "00-meta/log.md=<sha256>" --path "20-knowledge/concepts/New note.md=<sha256>" \
  --own-lines /tmp/my-lines.json --message "Add new note" --actor session-a
```

```json
{"schema_version":1,"edits":[
  {"path":"00-meta/log.md","old":"## Log\n","new":"## Log\n- my line\n"}
]}
```

Each `old` must occur exactly once in the file as it stands in HEAD, compared as exact bytes of the committed blob, so one edit cannot anchor on the text of an earlier edit in the same patch. `new` takes the line ending of the blob: CRLF becomes LF in a blob without CRLF, and a bare LF becomes CRLF in a blob with only CRLF. A blob with both keeps `new` as written. The sha256 for the working file is still required.

The next `git add` writes the working file over this commit, so the working file must already agree with the edit, ignoring CRLF differences. Each `new` must be in it, and text an edit removes must be gone from it. Otherwise the result is `patch_not_in_worktree`. That comparison is on the bytes of `old`, so a short anchor can also match another session's line.

An edit that has already landed is refused with `already_applied`: its `new` is in HEAD and its `old` is either gone or inside that `new`. A retry after `committed_index_stale`, or a run started again after a process died between `update-ref` and the index update, ends here and does not commit the lines twice. Nothing is committed by that refusal, so run the `git reset` below before any plain commit. To add a line that already follows the same anchor, use a different anchor. The check reads content, so an edit that rebuilds its own `old` at a boundary, such as `ab` to `a` in `abb`, can apply again on a retry. Whole-line edits do not do that.

The command applies the edits to the HEAD blob, builds a tree on a scratch index under the git directory, runs the `pre-commit` hook on that tree, and creates the commit with `commit-tree`. `update-ref` then moves HEAD only if it still has the value that was read. When it has moved, the whole sequence repeats on the new HEAD, inside `--max-attempts`. The working files are not touched, so other sessions' unfinished lines stay as local changes.

Afterwards the shared index entries for the declared paths are pointed at HEAD. Without this the next plain commit would write the old entries and revert the commit. A writer finishing late must not leave an older blob behind a newer commit, so entries are read from HEAD and the write counts only if HEAD is still that commit afterwards.

Other paths may travel in the same commit. Only shared notes may have edits, and ignored new files are refused as they are in the default mode. Those paths get their blobs from `git add` on the scratch index, which holds HEAD, so their line endings follow the same rule as a path commit. That add reads the working file again, so a blob that does not come from the checked bytes is refused with `hash_mismatch`. Only the blob is taken: the file mode stays as it is in HEAD.

Differences from the default mode:

- `commit-tree` runs no hooks, so only `pre-commit` is run, by `git hook run`, which needs Git 2.36 or later. An older Git gives `hook_runner_unavailable`, not a commit without the gate. `commit-msg` and `post-commit` hooks do not run, and `commit.gpgsign` does not apply.
- An unborn branch is refused with `no_head`.
- `staged_replaced` counts declared paths whose staged entry matched neither the old nor the new HEAD. That staged content is replaced in the index, and it remains in the working file. Concurrent `--own-lines` writers can raise this count without any staged work being lost.
- `committed_index_stale` means the commit exists but the index could not be updated in time. Run `git reset -q -- PATH` for the declared paths before any plain commit.

## Boundaries

**Mixing with plain commit leaves silent omissions.** This tool cannot stop a session that runs `git commit` without paths, and that commit still writes the whole shared index. With path commits and plain commits mixed, every command exited 0 and a file was still missing from history in the measurements below. The `--own-lines` window between `update-ref` and the index update is another place a plain commit can revert lines. Use one route for a working tree. After a session, `git log --name-status` and `git status` show what actually landed.

**A hash is a check, not a lock.** A writer that changes a file after the last check reaches the commit, and verification then reports `content_differs` after the commit exists.

**Deny zones.** Files moved into a deny zone are outside this tool. It refuses zone paths, folder paths and everything inside a linked working tree, and it offers no path list on stdin or in a file, so it cannot be used to step around a deny-zone guard. Use the folder staging described in `commands/vault-session-end.md` for those moves, at a time no other session is committing, and read `git status` before committing.

**Hooks can pull shared notes in.** A stricter vault-local hook may require the index or handoff in the same commit as a new note. A path commit would then sweep those files, which this tool refuses. `--own-lines` carries them with only the caller's lines.

**Not an isolation tool.** Sessions that need separate working files should use separate worktrees. Each linked worktree has its own index and its own lock, and this command works inside one when `--vault` names that tree.

**No automatic fallback.** A failed commit is reported and left for the caller.

## Measured conditions

The numbers here measure git commands, not this tool, and they set its design and default limit. All are from synthetic repositories on Git for Windows 2.55.0.windows.3, Python 3.12.10 and 32 logical CPUs, in 187 runs with 8,112 commit attempts. Writers committed with 0 to 50 ms between commits, which is an extreme load, so the rates describe that load and not the frequency in real sessions. Items marked sequential do not depend on load.

| Finding | Result |
| --- | --- |
| `git add` then `git commit -- PATH` | 6,045 reported commits, all present in the final commit |
| Out-of-date temporary index, then plain commit | Final commit lost files: 68.0 % without a hook, 80.0 % with a 300 ms hook. All 888 stayed in history and the working tree |
| Out-of-date temporary index, then `git commit -- PATH` | No file lost; 57 of 480 commits had a subject that did not match their change |
| Lost file with a plain commit (sequential) | Reproduced in one run with exit code 0 |
| `git commit -- PATH` with a 300 ms hook, 5 attempts | 54 of 240 commits finished |
| Same, 60 attempts | 598 of 600 finished; both failures were vanished stage entries |
| Entry from a successful `git add` vanished | 11 of 3,624 in a small index without hooks; every case recovered by restarting from `git add` |
| Mixed path and plain commits | A file silently missing in 5 of 1,076 runs where every command exited 0 |
| Temporary index with plain commit | 93 of 1,200 commits had another commit's subject |
| Five writers on one shared log with path commits | 97 of 240 commits ended as nothing to commit; 170 of 220 committed lines landed in another writer's commit |
| Temporary index, `commit-tree`, `update-ref` compare | No file lost and no subject mismatch; six writers took 15.6 s without a hook and 83.7 s with a 300 ms hook, against 6.7 s and 27.2 s for path commits |
| Uncontended commit on an index of several thousand entries | 0.13 to 0.16 s without a hook; about 1.1 s with a strict hook on 15 KB notes |

With the strict hook, 2 of 144 commits failed at 60 attempts: one stage entry vanished, and one held the lock for all 60 attempts, about 21 seconds of waiting. The default limit is a measured starting point. A vault with a slow hook and many writers should raise `--max-attempts`.

The vanished-entry and subject-swap mechanisms were observed by behaviour. Other Git versions, Linux and macOS load, network drives, file-sync tools and security products were not measured. The tool's own tests add a small multi-process run on every platform in CI, which checks outcomes and never timing.

## Sources

- [git-commit](https://git-scm.com/docs/git-commit), `--only`, as shipped with Git for Windows 2.55.0.windows.3.
- [git-update-ref](https://git-scm.com/docs/git-update-ref), the old-value check, same release.
- [git-hook](https://git-scm.com/docs/git-hook), `run --ignore-missing`, same release.
- [gitglossary](https://git-scm.com/docs/gitglossary), pathspec magic `literal`, same release.
