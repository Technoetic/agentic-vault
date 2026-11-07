# agentic-vault:adapter lesson-cues engine=0.43.0
"""lesson-cues.py - opt-in lesson cues for Claude Code PreToolUse hooks.

ASCII only, Python 3.10+ standard library only. The plugin ships this file in
assets/adapters/lesson-cues/. Nothing installs it by default: a user who wants
it copies it byte for byte to 00-meta/scripts/ in the vault and merges
settings-hooks.json into .claude/settings.local.json (guide.md). The hook runs
this file as python -X utf8 -I <file>, so it stands alone and imports no
vault_* module.

Contract:
  - Input: one PreToolUse hook input (JSON) on stdin. The vault root is
    CLAUDE_PROJECT_DIR. Cues come from 00-meta/lesson-cues.json, which
    vault_lesson_cues.py build --write compiles from the lesson ledger.
  - Output: nothing, or one JSON object whose hookSpecificOutput carries
    additionalContext (hint cues, as factual statements) and, for guard cues,
    permissionDecision "deny". It never answers "allow" or "ask". The exit code
    is always 0 and every failure means no output: this hook does not use
    onFailure, so a crash or a timeout lets the tool call run.
  - A matching cue fires once per session and cue. Guards fire first. One call
    carries at most MAX_PER_CALL cues and MAX_CHARS characters; a cue that does
    not fit stays unfired for a later call.
  - Without a usable session record a guard cannot promise to stop only once,
    so it is delivered as a hint.

Matching is literal: command_prefix (at the start of the command or after
&&, ||, ;, |, & or a line break), command_contains (anywhere, quoted text
included) and path_glob (fnmatch against the vault-relative path and the file
name). All three ignore case. The shell text is not parsed, so a separator
inside quotes also starts a segment. That can only widen a match.

An advisory, not a control: additionalContext sits next to the tool result and
cannot change a call that was already made, and a guard can be repeated.
"""
from __future__ import annotations

import fnmatch
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import tempfile


CUES_REL = "00-meta/lesson-cues.json"
MAX_FILE_BYTES = 256 * 1024
MAX_PER_CALL = 3
MAX_CHARS = 1500
COMMAND_TOOLS = ("Bash", "PowerShell", "Monitor")
GUARD = "guard"
HINT = "hint"
PREFIX = "lesson-cues: "
SUFFIX = "\nThis cue stops a matching call once per session."

_SEGMENT_RE = re.compile(r"&&|\|\||[;|&\n]")
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?=/|$)")


def _warn(message: str) -> None:
    try:
        sys.stderr.write("lesson-cues: " + message + "\n")
    except (OSError, ValueError):
        pass


def _native(path: str) -> str:
    if os.name == "nt":
        path = _MSYS_DRIVE_RE.sub(lambda match: match.group(1) + ":", path)
    return path


def _strings(cue: dict, name: str) -> list:
    value = cue.get(name, [])
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def load_cues(vault: str) -> list:
    """Compiled cues, or an empty list when the file is absent or unusable."""
    path = os.path.join(vault, *CUES_REL.split("/"))
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            _warn("the compiled cue file is not a regular file of a usable size")
            return []
        with open(path, "rb") as stream:
            data = json.loads(stream.read(MAX_FILE_BYTES + 1).decode("utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        _warn("the compiled cue file cannot be read")
        return []
    cues = data.get("cues") if isinstance(data, dict) and data.get("version") == 1 else None
    if not isinstance(cues, list):
        _warn("the compiled cue file has an unknown format")
        return []
    return [cue for cue in cues if isinstance(cue, dict) and cue.get("kind") in (GUARD, HINT)
            and isinstance(cue.get("text"), str) and isinstance(cue.get("id"), str)]


def _command_matches(cue: dict, command: str) -> bool:
    text = command.casefold()
    if any(item.casefold() in text for item in _strings(cue, "command_contains")):
        return True
    prefixes = [item.casefold() for item in _strings(cue, "command_prefix")]
    return any(segment.strip().startswith(prefix) for segment in _SEGMENT_RE.split(text) for prefix in prefixes)


def _relative(vault: str, path: str) -> str:
    text = path.replace("\\", "/").casefold()
    root = vault.replace("\\", "/").rstrip("/").casefold() + "/"
    return text[len(root):] if text.startswith(root) else text


def _path_matches(cue: dict, vault: str, path: str) -> bool:
    relative = _relative(vault, path)
    name = relative.rsplit("/", 1)[-1]
    for pattern in _strings(cue, "path_glob"):
        pattern = pattern.replace("\\", "/").casefold()
        options = [pattern, pattern[3:]] if pattern.startswith("**/") else [pattern]
        if any(fnmatch.fnmatchcase(value, option) for value in (relative, name) for option in options):
            return True
    return False


def cue_matches(cue: dict, tool: str, tool_input: dict, vault: str) -> bool:
    tools = _strings(cue, "tools")
    if tools and tool not in tools:
        return False
    command = tool_input.get("command")
    if tool in COMMAND_TOOLS and isinstance(command, str) and _command_matches(cue, command):
        return True
    return any(key.endswith("path") and isinstance(value, str) and _path_matches(cue, vault, value)
               for key, value in tool_input.items())


def _cue_key(cue: dict) -> str:
    raw = json.dumps(cue, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _state_dir() -> str | None:
    """A per-user folder for session marks; None when it cannot be trusted."""
    try:
        user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser())[:32]
    except Exception:  # getuser raises several types when no name can be found
        user = "user"
    path = os.path.join(tempfile.gettempdir(), "agentic-vault-lesson-cues-" + user)
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            return None
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return None
    except OSError:
        return None
    return path


def claim(state_dir: str | None, vault: str, session: object, cue: dict) -> bool | None:
    """True for the first claim in a session, False for a repeat, None when unknown."""
    if state_dir is None or not isinstance(session, str) or not session:
        return None
    raw = "\0".join((os.path.normcase(vault), session, _cue_key(cue)))
    name = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    try:
        os.close(os.open(os.path.join(state_dir, name), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return False
    except OSError:
        return None
    return True


def _render(cue: dict) -> str:
    count = cue.get("count")
    seen = f", recorded {count} times" if type(count) is int and count > 1 else ""
    return f"Vault lesson record {cue['id']}{seen}: {cue['text']}"


def select(cues: list, tool: str, tool_input: dict, vault: str, session: object, state_dir: str | None) -> tuple:
    """(guard lines, hint lines) for this call, inside the per-call budget."""
    matched = [cue for cue in cues if cue_matches(cue, tool, tool_input, vault)]
    matched.sort(key=lambda cue: cue["kind"] != GUARD)
    guards, hints, used = [], [], 0
    for cue in matched:
        if len(guards) + len(hints) >= MAX_PER_CALL:
            break
        line = _render(cue)
        wrapper = len(PREFIX) + len(SUFFIX) if cue["kind"] == GUARD and not guards else 0
        if used + wrapper + len(line) + 1 > MAX_CHARS:
            continue
        state = claim(state_dir, vault, session, cue)
        if state is False:
            continue
        (guards if cue["kind"] == GUARD and state else hints).append(line)
        used += wrapper + len(line) + 1
    return guards, hints


def hook_output(guards: list, hints: list) -> dict | None:
    if not guards and not hints:
        return None
    specific = {"hookEventName": "PreToolUse"}
    if hints:
        specific["additionalContext"] = "\n".join(hints)
    if guards:
        specific["permissionDecision"] = "deny"
        specific["permissionDecisionReason"] = PREFIX + "\n".join(guards) + SUFFIX
    return {"hookSpecificOutput": specific}


def evaluate(payload: object, vault: str) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cues = load_cues(vault)
    if not cues:
        return None
    return hook_output(*select(cues, payload["tool_name"], tool_input, vault, payload.get("session_id"), _state_dir()))


def main() -> int:
    try:
        vault = _native(os.environ.get("CLAUDE_PROJECT_DIR", ""))
        if not vault.strip() or not os.path.isdir(vault):
            _warn("CLAUDE_PROJECT_DIR does not name the vault folder")
            return 0
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = evaluate(payload, vault)
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    except Exception as error:  # an advisory hook must never stop the tool call
        _warn(f"no cue was delivered ({type(error).__name__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
