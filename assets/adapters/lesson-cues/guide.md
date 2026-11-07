# Lesson cues (optional, Claude Code only)

`lesson-cues.py` in this folder is an optional Claude Code PreToolUse hook. When a tool call matches a cue stored in the
lesson ledger, it adds the lesson to Claude's context as a factual statement (`hint`), or stops that call once per
session (`guard`). The plugin does not turn this hook on: it is not in `hooks/hooks.json`, and it works only after the
steps in "Turning it on".

This is an advisory, not a control. It does not replace the rules in the vault's behaviour contract, the deny-zone guard
or a test. Whether it changes behaviour has not been measured.

## What a cue looks like

A cue lives in the optional `cues` key of a row's lesson marker, so a ledger row keeps its five cells:

```json
{"kind": "hint", "text": "A pipe hid the exit code of the step before it in a past run.",
 "tools": ["Bash"], "command_contains": [" | tail"]}
```

| Key | Meaning |
|---|---|
| `kind` | `hint` or `guard` |
| `text` | Up to 300 characters. Write what happened, not an order |
| `tools` | Optional. Tool names the cue applies to |
| `command_prefix` | Literal text at the start of a Bash, PowerShell or Monitor command, or after `&&`, `\|\|`, `;`, `\|`, `&` or a line break |
| `command_contains` | Literal text anywhere in the command, quoted text included |
| `path_glob` | `fnmatch` glob against the vault-relative path and the file name. `*` also crosses `/`, and a leading `**/` is optional |

A cue needs at least one of the last three keys, each with one to eight values of up to 200 characters. All three ignore
case. Regular expressions are refused: a cue is data that a hook runs on every tool call, and a literal match cannot
backtrack. A row holds at most eight cues. A value cannot hold `<!--` or `-->`, because the marker is an HTML comment on
one line. It also cannot hold a control character, an invisible format mark such as a bidirectional override, or a
Unicode line or paragraph separator: these end a line or hide text from a reader.

Add cues through `vault_lesson_delta.py`: `ADD` takes an optional `cues` list, and `CUES` replaces the list of an
existing lesson (an empty list removes it). Both go through the same prepare, review and approve steps as any other
lesson change. `INC`, `EDIT` and `RETIRE` keep the list. Rows without cues are written exactly as before.

## Compile the list

The hook runs alone under `python -I`, so it cannot import the ledger parser. It reads one compiled file:

```text
python skills/agentic-vault/scripts/vault_lesson_cues.py --vault PATH build
python skills/agentic-vault/scripts/vault_lesson_cues.py --vault PATH build --write
```

`build` prints the result and writes nothing. `--write` replaces `00-meta/lesson-cues.json`, and nothing else. A list
larger than the 256 KiB the hook reads is refused as `lesson_cues_too_large`, and the old file stays.

The list holds heading records that are not retired, and rows whose whole status cell is one of these:

- `관찰중`, `승격제안` or `active`
- `승격완료(→target)` or `승격완료(검증중→target, date, anchor)`

Retired, withdrawn, rejected, rolled-back and candidate rows are left out, and so is anything else. The match covers the
whole cell, so `관찰중단`, `승격제안 기각`, `승격제안철회`, `승격완료취소`, `activeX` and `관찰중 (note)` all stay out. A cell that
fits a form but holds `철회`, `기각`, `중단`, `취소`, `롤백`, `retired`, `withdrawn`, `rejected` or `rolled` also stays out, so
`승격완료(→롤백)` is out, and so is a real target whose name has one of those words. Keep notes in the evidence cell.

Two places are left out whatever the status says:

- **An HTML comment block:** from `<!--` at the start of a line to the line that holds `-->`, or to the end of the file
  when it never closes. Comment text in the middle of a line does not open a block. A row in a block stays in the
  ledger, so `INC`, `EDIT` and `RETIRE` still work on it.
- **The rejected-drafts section:** everything under `## 기각 대장`, up to the next `##` heading that is not a lesson
  record.

`skipped` in the result counts cue rows left out by `retired`, `status`, `comment` and `rejected`, and `ignored_rows` counts
rows that do not parse. Compile again after changing the ledger: the hook does not notice that the list is out of date.

## What the hook does

- **Hint:** adds `Vault lesson record ID, recorded N times: text` to `additionalContext`. No permission decision is made.
- **Guard:** answers `deny` with the same line as the reason, once per session and cue. The same call goes through when
  repeated.
- **Never** answers `allow` or `ask`, so it cannot widen what the permission system lets through.
- **Checks the file again:** the compiled file has no review step of its own, so the hook drops any cue the compiler
  could not have written and warns once on standard error with the number dropped. That covers the id format, the count,
  the kind, the text and value limits, the characters above and unknown keys. The other cues still work.
- **Budget:** at most 3 cues and 1,500 characters per call, guards first. A cue that does not fit stays unfired and can
  fire on a later call. A cue that fired, hint or guard, does not fire again in that session.
- **Session record:** one empty file per cue and session in a per-user folder under the system temporary directory. No
  file goes into the vault. If that record cannot be written, or the input has no session ID, a guard is delivered as a
  hint and may repeat: a guard that cannot count would stop the call on every attempt.
- **Failure:** every failure means no cue. The exit code is always 0.

## Limits

- **A hint cannot stop the call it matched.** The hook documentation describes `additionalContext` for PreToolUse as
  "String added to Claude's context alongside the tool result". Claude has already made the call by then, and the page
  says Claude reads such a reminder "on the next model request". So a hint cannot change the call that triggered it,
  and it reaches the next decision, whatever that next call is. A lesson about a call that does damage on its first run
  cannot be delivered as a hint.
- **A guard is advice, not enforcement.** The handler does not use `onFailure`, so a crash, a timeout or a missing
  `python` lets the call run. A repeat also runs. Use the deny-zone guard or a permission rule for anything that must
  not happen.
- **Write facts.** The same page asks for "factual statements rather than imperative system instructions" and warns that
  imperative text can trip prompt injection defences, so it may not be used as context. The hook adds a factual lead-in;
  the sentence after it is yours.
- **Matching is text, not parsing.** A separator inside quotes also starts a segment, and a prefix such as `git` cannot
  tell one subcommand from another. Quoted text is deliberately not removed: a command passed as
  `powershell -Command "a; b"` must stay visible. A command built at run time is not seen.
- **Effect not measured.** Nobody has compared sessions with and without cues. A comparison with a control group needs
  real model runs, which this release does not include. The limits above are not tuned values: three cues and 1,500
  characters are operator choices.
- **Cues are vault text.** They reach the model as context with the same trust as the ledger. Review them like any
  other lesson change.
- **The compiled file can still be edited.** The check above keeps a cue to one line of 300 characters in the compiled
  shape. It does not prove that the cue came from the ledger: the hook does not compare `source_sha256`, so an edited
  file with well-formed cues is delivered. The file is deliberately not a deny-guard control. Those controls protect
  the guard itself, a guard control prompt would fire on every command that names the file, and the ledger it is
  compiled from is not a control either.

## Turning it on

Use `/vault-init` step 4-3, or by hand:

1. Check that `python` on `PATH` is 3.10 or later. The hook starts it without a shell.
2. Copy `lesson-cues.py` byte for byte to `00-meta/scripts/lesson-cues.py`. Keep the `agentic-vault:adapter lesson-cues
   engine=` stamp on the first line.
3. Merge the group in `settings-hooks.json` into `hooks.PreToolUse` of `.claude/settings.local.json`, keeping every
   existing key and hook. Do not add `"onFailure": "block"`: an advisory hook must not be able to stop every tool call.
4. Compile the list with `build --write`, then restart the session.
5. `python skills/agentic-vault/scripts/vault_adapters.py --vault . --format json` reports `adapters.lesson_cues` with
   `state: installed`.

To turn it off, remove the group that calls `lesson-cues.py` from `.claude/settings.local.json`.

## Sources

Ideas only. No code was copied.

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks), read on 2026-11-07. The page shows no version.
  Used for the place of `additionalContext`, the next model request and the advice on factual statements.
- [joeblackwaslike/lessons-learned](https://github.com/joeblackwaslike/lessons-learned) at `main` 3b39f38152fe, MIT.
  Used for per-lesson trigger fields, a per-call budget and the choice between hint and guard. Its budget is 3 lessons
  and 4 KB, and its patterns are regular expressions; both differ here.
- [logpai/Drain3](https://github.com/logpai/Drain3) at v0.9.11, commit 78d3cae4bcac329c4e4f275d416562e10c5c446e, MIT
  (`LICENSE.txt`).
  Considered for `vault_lesson_clusters.py`. Its fixed-depth tree does not suit free text and its masking would remove
  the entry numbers that carry the best signal, so neither was taken.
