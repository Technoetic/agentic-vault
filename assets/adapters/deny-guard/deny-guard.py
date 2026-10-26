# agentic-vault:adapter deny-guard engine=0.32.0
"""deny-guard.py - opt-in deny-zone guard for Claude Code PreToolUse hooks.

ASCII only, Python 3.10+ standard library only. The plugin ships this file in
assets/adapters/deny-guard/ with ps-parse.ps1, its PowerShell parser. Nothing
installs them by default: a user who wants the guard copies both byte for
byte to 00-meta/scripts/ in the vault and merges settings-hooks.json into
.claude/settings.local.json (guide.md). The hook runs this file as
python -X utf8 -I <file>, so it needs no shebang line.

Contract:
  - Input: one PreToolUse hook input (JSON) on stdin. The vault root is
    CLAUDE_PROJECT_DIR. Deny zones are deny_zones in 00-meta/vault-config.json,
    or the template defaults when that file cannot be read or the list is
    invalid (one stderr line says so).
  - Output: nothing (no decision: the normal permission flow goes on), or one
    JSON object whose hookSpecificOutput.permissionDecision is "deny" or
    "ask". It never answers "allow", so it can only narrow what the
    permission system lets through. The exit code is always 0; an internal
    error becomes "ask".
  - Without CLAUDE_PROJECT_DIR it writes one stderr line and decides nothing.

What it checks:
  - Read, Edit, Write, NotebookEdit: every string field whose key ends in
    "path". No such field is an input shape the guard does not know: ask.
  - Glob: path and pattern. Grep: path and glob (its pattern is the text to
    find, not a path). A search without a path starts in the working
    directory and may walk through a zone; that is not denied.
  - Bash, and Monitor, whose command runs in the Bash tool's shell (a
    WebSocket watch, ws without a command, runs nothing: no decision): a
    raw-text pre-filter for zone names (the last segment of each zone), guard
    control names and, on Windows, 8.3 short names that can stand for them
    (90-ASS~1), then shlex words. A literal word that resolves into a zone
    returns deny. The text after a ':' is a path too (git REV:PATH, :PATH,
    :N:PATH, pathspec magic such as :/PATH, host:PATH), checked from the
    working directory, the vault root and the repository root. Words that
    name a zone without opening it are exempt: the destination of a move
    that keeps existing files (mv -n or --no-clobber, git mv without -f) or a
    new folder (mkdir), which the vault rules use to quarantine files, echo or
    printf text sent to a file, a git commit or tag message, and the value of
    an exclude option (--exclude, --exclude-dir, --ignore-dir, --ignore=VALUE,
    --ignore VALUE only for ls and ag, a '!' glob). A move into a zone that may
    replace a file there returns ask. Paths built at run time return ask, and
    so does a zone name inside $(...), backticks or after a here-document
    (<<), exempt word or not: the shell may run that text. The Claude Code
    commit form "$(cat <<'EOF' ... EOF)" is text.
  - PowerShell: the same pre-filter (a backtick, PowerShell's escape
    character, does not hide a zone name). Only on a hit, ps-parse.ps1 next
    to this file parses the command in a Windows PowerShell child process
    (PARSE_TIMEOUT seconds) without running it. A literal string, redirection
    target or pipeline input that resolves into a zone returns deny. A parse
    error, a parser failure or timeout, a construct evaluated at run time
    (Invoke-Expression, Start-Process, Invoke-Command, & or . on a script
    block, dot-sourcing, -EncodedCommand, a provider path outside the file
    system, variables, operators, method calls, expandable strings, another
    shell), a relative path in a command that also changes the location
    (Set-Location, cd), or a zone name that is no literal path returns ask.
    PowerShell has none of the Bash exceptions.
  - Edit, Write and NotebookEdit on the guard's own controls
    (00-meta/vault-config.json, the .claude folder and .claude/settings*.json,
    this script and ps-parse.ps1), and shell words that name them, return ask.

A guard, not a sandbox: it does not see files that a started program opens by
itself, paths assembled at run time that never spell a zone name, searches
that start above a zone, a junction swapped after the check, or tools that its
hook matcher leaves out.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Sequence


DENY = "deny"
ASK = "ask"
DEFAULT_DENY_ZONES = (
    "10-inbox/_processed", "20-knowledge/_archive", "50-projects/_completed",
    "90-assets", ".obsidian",
)
CONFIG_REL = "00-meta/vault-config.json"
GUARD_REL = "00-meta/scripts/deny-guard.py"
PARSER_REL = "00-meta/scripts/ps-parse.ps1"
PARSER_NAME = "ps-parse.ps1"  # looked up next to this file, wherever it is installed
PARSE_TIMEOUT = 5.0  # seconds for the PowerShell parser child; the hook allows 10
CONTROL_NAMES = ("settings.json", "settings.local.json", "vault-config.json", "deny-guard.py", "ps-parse.ps1")
PATH_TOOLS = ("Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep")
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
# Monitor runs its command in the Bash tool's shell, under Bash permission rules (Claude Code tools reference).
SHELL_TOOLS = ("Bash", "PowerShell", "Monitor")
MAX_LABEL = 120

_WINDOWS = os.name == "nt"
_CASELESS_PATHS = sys.platform == "darwin"  # macOS volumes ignore case unless formatted otherwise
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?=/|$)")
_DEVICE_PREFIX_RE = re.compile(r"^[\\/]{2}[?.][\\/](?=[A-Za-z]:)")
_ROOTED_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/~])")
_WILDCARD_RE = re.compile(r"[*?\[{]")
_RUNTIME_RE = re.compile(r"[$`]")
_RUNTIME_TEXT_RE = re.compile(r"`|\$\S")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Guard control names in text. .claude counts as the folder itself, with a wildcard name right
# inside it (.claude/*.json) or with a path that ends in '.' or '..' (.claude/rules/..), not as
# part of a deeper path such as ~/.claude/plugins/cache/...; Windows drops trailing dots and
# spaces from a name.
_CONTROL_TEXT_RE = re.compile(
    r"vault-config\.json|deny-guard\.py|ps-parse\.ps1|settings[^\s/\\'\"]*\.json"
    r"|\.claude[. ]*(?:[\\/]+[^\s\\/'\"]*[*?\[{][^\s\\/'\"]*|[\\/]+(?:[^\s\\/'\"]+[\\/]+)*\.\.?)?[\\/]*"
    r"(?=$|[\s'\"`:;&|()<>,{}*?\[])")
_SHELL_FLAG_RE = re.compile(r"-[a-z]*c")
# A Windows 8.3 short name in text (90-ASS~1, SETTIN~1.JSO): one to six characters, '~' and digits.
_SHORT_NAME_RE = re.compile(
    r"(?:^|(?<=[\s\\/'\"`=:;&|()<>,{}]))([^\s\\/'\"`=:;&|()<>,{}.~*?\[\]]{1,6})~[0-9]{1,6}"
    r"(?=$|[\s\\/.'\"`:;&|()<>,}*?\[])")
_SHORT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~")
_HEX_DIGITS = frozenset("0123456789ABCDEF")
_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")

# Shell operators. shlex merges a run of these characters into one word; a
# newline is an operator here, so the next line starts a new command.
_OPERATOR_CHARS = "();<>|&\n"
_REDIRECTS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "<&", ">|", "<>", "&>", "&>>"})
_STDOUT_REDIRECTS = frozenset({">", ">>", ">|", ">&", "&>", "&>>"})
# Redirect targets that are streams, not files (/dev/null only discards): a
# pipe or $(...) can read what goes there.
_STREAM_TARGET_RE = re.compile(r"^/(?:dev/(?!null$)|proc/)")
# Options whose value (--name=VALUE or the next word) is a pattern of files to
# leave out. Only these names: a flag such as --exclude-vcs, --ignore-backups or
# --delete-excluded takes no value, so the word after it stays an operand, and
# an option that reads a file (--exclude-from, --ignore-file, --ignore-path)
# opens what it names.
_EXCLUDE_OPTIONS = frozenset({"--exclude", "--exclude-dir", "--ignore", "--ignore-dir"})
# ripgrep's --ignore takes no value (it turns --no-ignore off), so the word after
# a bare --ignore is its value only for the commands where --ignore names a
# pattern; --ignore=VALUE stays exempt everywhere.
_IGNORE_VALUE_COMMANDS = frozenset({"ls", "ag"})
# Appended to a descriptor number that touches its redirect (the 2 of 2>&1), so
# that shlex keeps it apart from an operand that is a number (mv a b 2 > f).
_IO_MARK = "\x01"
_DIGITS = "0123456789"
_WORD_BREAKS = " \t\r\n;&|()<>"
# The commit form Claude Code writes: git commit -m "$(cat <<'EOF' ... EOF)".
_HEREDOC_TEXT_RE = re.compile(r"\$\(cat <<[ \t]*'([A-Za-z_][A-Za-z0-9_]*)'[ \t]*\n")
_CLOSE_PAREN_RE = re.compile(r"[ \t]*\)")
_CASE_RE = re.compile(r"\bcase\b")
_KEYWORDS = frozenset({"!", "{", "}", "do", "then", "else", "elif", "if", "while", "until", "time"})
_CD_COMMANDS = frozenset({"cd", "pushd", "popd"})
_SOURCE_COMMANDS = frozenset({"eval", "source", "."})
_TEXT_COMMANDS = frozenset({"echo", "printf"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "busybox"})
_INTERPRETERS = _SHELLS | frozenset({
    "python", "py", "pypy", "node", "nodejs", "deno", "bun", "perl", "ruby", "php",
    "lua", "pwsh", "powershell", "cmd", "osascript", "rscript",
})
_INLINE_FLAGS = frozenset({
    "-c", "-e", "--eval", "-p", "--print", "-r", "-command", "-encodedcommand",
    "-enc", "-ec", "/c", "/k",
})

# PowerShell. Cmdlets resolve relative paths against the location, which these
# commands (and their aliases) change.
_PS_LOCATION_COMMANDS = frozenset({
    "set-location", "sl", "cd", "chdir", "push-location", "pushd", "pop-location", "popd",
})
# After a location change only these stay fixed on Windows: \x and ~ follow the
# current drive and provider, which Set-Location also changes.
_PS_FIXED_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")
# The same test as Test-ProviderPath in ps-parse.ps1: FileSystem::path (with or
# without a module name) is a file path; any other Provider::path, or a drive
# name longer than one letter (HKLM:, Env:, a PSDrive) not followed by a space
# ("note: x" is text), is not. A $ starts a variable ("$env:X\a"), not a drive.
_PS_FILESYSTEM_RE = re.compile(r"^\s*(?:[^\s:\\/$]+\\)?filesystem::", re.IGNORECASE)
_PS_PROVIDER_RE = re.compile(r"^\s*(?:[^\s:\\/$]+\\)?[^\s:\\/$]+::|^\s*[^\s:\\/$]{2,}:(?!\s)")
_PS_FLAG_LABELS = {
    "invoke_expression": "Invoke-Expression",
    "start_process": "Start-Process",
    "invoke_command": "Invoke-Command",
    "script_block_call": "& or . on a script block or a value",
    "dot_source": "dot-sourcing",
    "encoded_command": "-EncodedCommand",
    "provider_path": "a provider path outside the file system",
    "expression": "variables, operators, method calls or script blocks",
}
_PS_COUNT_KEYS = ("errors", "expandable")
_PS_TEXT_KEYS = ("strings", "expandable_strings", "flags")


# This file is copied into vaults and run alone by the hook, so it cannot
# import the plugin's vault_paths.py. The block below is a verbatim copy of
# vault_paths.zone_matches; tests/test_rule_sources.py fails if they drift.
# --- BEGIN SHARED ZONE RULE (verbatim copy of vault_paths.zone_matches) ---
def zone_matches(parts: Sequence[str], zone: str) -> bool:
    """Return True when a deny/exclude zone covers a path, ignoring case.

    A one-segment zone ("90-assets") matches that component at any depth; a
    longer zone ("10-inbox/_processed") matches only as a prefix from the root.
    """
    zone_parts = tuple(
        part.casefold() for part in zone.replace("\\", "/").split("/") if part
    )
    if not zone_parts:
        return False
    folded = tuple(part.casefold() for part in parts)
    if len(zone_parts) == 1:
        return zone_parts[0] in folded
    return folded[: len(zone_parts)] == zone_parts
# --- END SHARED ZONE RULE ---


def normalize_zone(value: object) -> str | None:
    """Return a deny zone as a relative '/' path, or None when it is unusable.

    Looser than the healthcheck's config check (no device-name or character
    rules) because a zone here is only compared, never opened: every zone the
    healthcheck accepts is accepted here with the same normalized text.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith(("//", "\\\\")) or re.match(r"^[A-Za-z]:", text):
        return None
    text = text.replace("\\", "/")
    if text.startswith("/"):
        return None
    for part in text.split("/"):
        if part in ("", ".", "..") or any(ord(char) < 32 for char in part):
            return None
    return text


def load_zones(vault: str) -> tuple[tuple[str, ...], str | None]:
    """Return the deny zones and, when the template defaults replaced them, why."""
    try:
        with open(os.path.join(vault, *CONFIG_REL.split("/")), "rb") as handle:
            data = json.loads(handle.read().decode("utf-8-sig"))
    except FileNotFoundError:
        return DEFAULT_DENY_ZONES, f"{CONFIG_REL} was not found"
    except (OSError, UnicodeDecodeError, ValueError):
        return DEFAULT_DENY_ZONES, f"{CONFIG_REL} could not be read as JSON"
    if not isinstance(data, dict):
        return DEFAULT_DENY_ZONES, f"{CONFIG_REL} is not a JSON object"
    if "deny_zones" not in data:
        return DEFAULT_DENY_ZONES, None  # the healthcheck uses the same default
    zones = data["deny_zones"]
    if not isinstance(zones, list):
        return DEFAULT_DENY_ZONES, "deny_zones is not a list"
    normalized = []
    for zone in zones:
        clean = normalize_zone(zone)
        if clean is None:
            return DEFAULT_DENY_ZONES, "deny_zones holds an entry that is not a relative path"
        normalized.append(clean)
    return tuple(normalized), None


# -- reasons: fixed wording around the zone name ---------------------------------

def _label(text: str) -> str:
    return text if len(text) <= MAX_LABEL else text[:MAX_LABEL] + "..."


def _deny_target(tool: str, zone: str) -> str:
    return (f"the {tool} target is inside the deny zone '{_label(zone)}' (deny_zones in "
            f"{CONFIG_REL}). The vault rules keep deny zones unread and unchanged; leave this "
            "path alone or ask the user.")


def _deny_pattern(what: str, zone: str) -> str:
    return (f"{what} reaches into the deny zone '{_label(zone)}' (deny_zones in {CONFIG_REL}). "
            "The vault rules keep deny zones unread; search outside them.")


def _deny_shell(zone: str) -> str:
    return (f"this command names a path inside the deny zone '{_label(zone)}' (deny_zones in "
            f"{CONFIG_REL}). Shell commands do not read or change deny zones; mv -n or git mv "
            "into a zone, mkdir, echo or printf text, commit messages and exclude options are "
            "the exceptions. To search notes for the zone name, use the Grep tool.")


def _ask_replace(zone: str) -> str:
    return (f"this move may replace a file inside the deny zone '{_label(zone)}' (deny_zones in "
            f"{CONFIG_REL}): mv without -n (or --no-clobber) and git mv -f overwrite an existing "
            "file. Quarantine moves use mv -n or git mv; confirm only if you asked to replace a "
            "file there.")


def _deny_git_add(zone: str) -> str:
    return (f"this git add names a path inside the deny zone '{_label(zone)}' (deny_zones in "
            f"{CONFIG_REL}). To stage files moved into a zone, move them with git mv, which stages "
            "the move, or stage a folder outside the zone that holds them.")


def _deny_powershell(zone: str) -> str:
    return (f"this PowerShell command names a path inside the deny zone '{_label(zone)}' (deny_zones in "
            f"{CONFIG_REL}). PowerShell commands do not read or change deny zones and have none of the "
            "Bash exceptions: to move a file into a zone, use mv -n or git mv in the Bash tool; to search "
            "notes for the zone name, use the Grep tool.")


def _ask_wildcard(what: str, zone: str) -> str:
    return (f"{what} has a wildcard next to the deny zone name '{_label(zone)}', so the guard "
            "cannot tell whether it reaches the zone.")


def _ask_control(subject: str, control: str) -> str:
    return (f"{subject} {control}, which controls the deny-zone guard. Confirm only if you "
            "asked for this change.")


def _ask_unchecked(zone: str | None, why: str) -> str:
    what = f"the deny zone '{_label(zone)}'" if zone else "a deny-zone guard control"
    return f"this command names {what} but {why}, so the guard cannot check it."


# -- paths ------------------------------------------------------------------------

def _native(text: str) -> str:
    """Map an MSYS drive path (/d/x) and a \\\\?\\ prefix to a Windows path."""
    if not _WINDOWS:
        return text
    text = _DEVICE_PREFIX_RE.sub("", text)
    match = _MSYS_DRIVE_RE.match(text)
    if match:
        text = match.group(1).upper() + ":/" + text[2:].lstrip("/")
    return text


def _fold_part(part: str) -> str:
    # Windows ignores an alternate data stream suffix and trailing dots or
    # spaces when it opens a path component, so "90-assets." is "90-assets".
    if _WINDOWS:
        part = part.split(":", 1)[0].rstrip(" .")
    return part


def _fold_path(path: str) -> str:
    """The form paths are compared in: normcase (case and separators on Windows), and casefold on macOS."""
    path = os.path.normcase(path)
    return path.casefold() if _CASELESS_PATHS else path


def _relative(path: str, root: str) -> tuple[str, ...] | None:
    if path == root:
        return ()
    prefix = root if root.endswith(os.sep) else root + os.sep
    if not path.startswith(prefix):
        return None
    return tuple(_fold_part(part) for part in path[len(prefix):].split(os.sep) if part)


def _is_unc(path: str) -> bool:
    return path.startswith(("\\\\", "//"))


def _literal_prefix(text: str, stop: int) -> str:
    """The folders of text before index stop, ending with a separator, or ''."""
    head = text[:stop]
    cut = max(head.rfind("/"), head.rfind("\\"))
    return head[:cut + 1] if cut >= 0 else ""


def _may_match_control(pattern: str) -> bool:
    last = re.split(r"[\\/]", pattern)[-1].casefold()
    return "{" in last or any(fnmatch.fnmatchcase(name, last) for name in CONTROL_NAMES)


def _short_stem(name: str) -> str | None:
    """The upper-case characters that a Windows 8.3 short name of name starts with.

    Windows drops leading dots, the extension and the other dots and spaces, and
    writes + , ; = [ ] as '_'. None for any other character (non-ASCII text goes
    through the code page), so that any short name may stand for such a name.
    """
    text = name.lstrip(".").upper()
    if "." in text:
        text = text[:text.rindex(".")]
    stem: list[str] = []
    for char in text.replace(".", "").replace(" ", ""):
        if char in _SHORT_CHARS:
            stem.append(char)
        elif char in "+,;=[]":
            stem.append("_")
        else:
            return None
    return "".join(stem) or None


def _short_alias(base: str, stem: str | None) -> bool:
    """True when a short name whose part before '~' is base can stand for a name with this stem.

    Windows writes the first characters of the stem and ~N (90-ASS~1, 90-AS~10)
    or, once four such names exist, the first two characters (one for a
    one-letter stem), four hex digits of a hash and ~N (90A1B2~1).
    """
    base = base.upper()
    if stem is None:
        return True
    prefix = stem[:2]
    return stem.startswith(base) or (base.startswith(prefix) and len(base) == len(prefix) + 4
                                     and set(base[len(prefix):]) <= _HEX_DIGITS)


_CONTROL_STEMS = tuple(_short_stem(name) for name in (*CONTROL_NAMES, ".claude"))


def _git_toplevel(start: str) -> str | None:
    """The nearest folder at or above start that holds .git (a repository or worktree root), or None."""
    current = start
    for _ in range(256):
        try:
            if os.path.exists(os.path.join(current, ".git")):
                return current
        except (OSError, ValueError):
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


class _Location:
    __slots__ = ("paths", "views")

    def __init__(self, paths: list[str], views: list[tuple[str, ...]]) -> None:
        self.paths = paths  # the lexical absolute path, then the resolved one
        self.views = views  # vault-relative parts of those that lie inside the vault


# -- shell words ----------------------------------------------------------------

def _command_name(word: str) -> str:
    name = re.split(r"[\\/]", word)[-1].casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    return re.sub(r"[0-9.]+$", "", name) or name


def _strip_comments(command: str) -> str:
    """Drop shell comments: '#' at the start of a word, up to the line end."""
    out: list[str] = []
    quote = ""
    index = 0
    size = len(command)
    while index < size:
        char = command[index]
        if quote:
            out.append(char)
            if char == "\\" and quote == '"' and index + 1 < size:
                out.append(command[index + 1])
                index += 1
            elif char == quote:
                quote = ""
        elif char == "\\" and index + 1 < size:
            out.append(char + command[index + 1])
            index += 1
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "#" and (index == 0 or command[index - 1] in " \t\r\n;&|()<>"):
            end = command.find("\n", index)
            if end < 0:
                break
            index = end
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _words(command: str) -> list[str]:
    """Split a shell command into words and operators (ValueError if unbalanced)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    return list(lexer)


def _mark_io_numbers(command: str) -> str:
    """Mark each descriptor number written against its redirect (2>&1, 2>/dev/null, 1>f).

    shlex splits '2>&1' and '2 >&1' alike into '2', '>&' and '1'. The shell
    reads only an unquoted run of digits that starts a word and touches < or >
    as a descriptor; in 'mv a b 2 > f' the 2 is an operand. _IO_MARK after
    such a number lets _simple_commands drop it from the words.
    """
    out: list[str] = []
    quote = ""
    starts_word = True
    index = 0
    size = len(command)
    while index < size:
        char = command[index]
        if quote:
            if char == "\\" and quote == '"' and index + 1 < size:
                out.append(command[index:index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char == "\\" and index + 1 < size:
            out.append(command[index:index + 2])
            index += 2
            starts_word = False
            continue
        elif char in "'\"":
            quote = char
        elif char in _DIGITS and starts_word:
            end = index
            while end < size and command[end] in _DIGITS:
                end += 1
            out.append(command[index:end])
            if end < size and command[end] in "<>":
                out.append(_IO_MARK)
            index = end
            starts_word = False
            continue
        out.append(char)
        starts_word = not quote and char in _WORD_BREAKS
        index += 1
    return "".join(out)


def _is_operator(word: str) -> bool:
    return bool(word) and all(char in _OPERATOR_CHARS for char in word)


def _simple_commands(words: list[str]) -> list[list[tuple[str, str]]]:
    """Group words into simple commands; each word is (text, redirect or '').

    A redirect is its operator with the descriptor number in front when the
    command writes one against it ('2>&', '1>>'; _mark_io_numbers). The number
    is no word of the command.
    """
    commands: list[list[tuple[str, str]]] = [[]]
    redirect = ""
    number = ""
    for word in words:
        if _is_operator(word):
            if word in _REDIRECTS:
                redirect = number + word
            else:
                commands.append([])
                redirect = ""
            number = ""
            continue
        if word.endswith(_IO_MARK) and word[:-1].isdigit():
            number = word[:-1]
            continue
        commands[-1].append((word, redirect))
        redirect = ""
        number = ""
    return [command for command in commands if command]


def _writes_stdout_to_file(simple: list[tuple[str, str]]) -> bool:
    """True when the command's standard output ends in a file (> f, 1>> f, &> f).

    A redirect with a descriptor number other than 1 (2> f) moves another
    stream and does not count. The last redirect of standard output wins, as
    in the shell. A stream (/dev/stdout, /dev/fd/1, /proc/self/fd/1, >&2) or a
    target built at run time is no file: a pipe or $(...) can read what goes
    there.
    """
    to_file = False
    for target, redirect in simple:
        operator = redirect.lstrip(_DIGITS)
        number = redirect[:len(redirect) - len(operator)]
        if operator not in _STDOUT_REDIRECTS or number not in ("", "1"):
            continue  # 2> f moves standard error
        to_file = not (_STREAM_TARGET_RE.match(target) or _RUNTIME_RE.search(target)
                       or (operator == ">&" and (target.isdigit() or target == "-")))
    return to_file


def _head_index(plain: list[str]) -> int:
    """Index of the command word: after VAR=value prefixes and keywords such as do or then."""
    index = 0
    while index < len(plain) and (_ASSIGNMENT_RE.match(plain[index]) or plain[index] in _KEYWORDS):
        index += 1
    return index


def _move_destinations(args: list[str]) -> set[int]:
    """Indexes of the destination words in an mv argument list.

    Only plain forms count: SOURCE... DEST, -t DIR and --target-directory[=]DIR.
    A -t combined with other short options gives no destination, so every word
    stays checked.
    """
    operands: list[int] = []
    targets: set[int] = set()
    options = True
    take_next = False
    for index, word in enumerate(args):
        if take_next:
            targets.add(index)
            take_next = False
        elif options and word == "--":
            options = False
        elif options and word.startswith("--"):
            if word == "--target-directory":
                take_next = True
            elif word.startswith("--target-directory="):
                targets.add(index)
        elif options and word.startswith("-") and len(word) > 1:
            if word == "-t":
                take_next = True
            elif "t" in word[1:]:
                return set()
        else:
            operands.append(index)
    if targets:
        return targets
    return {operands[-1]} if len(operands) >= 2 else set()


def _move_keeps_files(args: list[str], git: bool) -> bool:
    """True when this mv or git mv (args after the command) refuses to replace an existing file.

    git mv refuses unless -f or --force is given. mv replaces files unless the
    last of -f, -i and -n is -n (GNU and BSD mv both let the last one win), or
    --no-clobber; --update=none and --update=none-fail skip existing files too.
    """
    mode = ""
    for word in args:
        if word == "--":
            break
        short = word.startswith("-") and not word.startswith("--") and len(word) > 1
        if git:
            if word == "--force" or (short and "f" in word[1:]):
                return False
        elif word in ("--no-clobber", "--update=none", "--update=none-fail"):
            mode = "n"
        elif word == "--force":
            mode = "f"
        elif word == "--interactive":
            mode = "i"
        elif word == "--exchange":
            return False  # swaps the two files
        elif short:
            for char in word[1:]:
                if char in "St":
                    break  # the rest is the option's value (-S SUFFIX, -t DIR)
                if char in "fin":
                    mode = char
    return git or mode == "n"


def _git_subcommand(plain: list[str], head_index: int) -> int | None:
    """Index of the git subcommand (the first word after git that is no option), or None."""
    if head_index >= len(plain) or _command_name(plain[head_index]) != "git":
        return None
    return next((index for index in range(head_index + 1, len(plain)) if not plain[index].startswith("-")), None)


def _exempt_words(plain: list[str], head_index: int, writes_file: bool) -> tuple[set[int], set[int]]:
    """Indexes of words whose zone names do not open the zone, and of move destinations that may replace a file.

    A move destination (mv, git mv) or a new folder (mkdir) puts files into a
    zone, which the vault rules use to quarantine processed notes; a move
    destination is exempt only when the move keeps existing files (mv -n,
    git mv without -f), and is returned second otherwise. Text that echo or
    printf writes to a file (echo 'zone/' >> .gitignore) and a git commit or
    tag message are text; echo output that goes to a pipe, a stream or into
    $(...) can become a path, so it stays checked. The value of an option in
    _EXCLUDE_OPTIONS (--exclude-dir=zone, --exclude-dir zone; a bare --ignore
    only for ls and ag) or a word starting with '!' (rg -g '!zone') leaves
    the zone out. Code inside an exempt word ($(...), backticks) is checked on
    its own (_code_spans).
    """
    exempt: set[int] = set()
    replacing: set[int] = set()
    head = _command_name(plain[head_index]) if head_index < len(plain) else ""
    value_next = False
    options = True
    for index, word in enumerate(plain):
        if value_next:
            exempt.add(index)  # the value of --exclude-dir zone
            value_next = False
            continue
        if word == "--":
            options = False  # what follows is an operand, even --exclude
        name, has_value = word.split("=", 1)[0], "=" in word
        if word.startswith("!") or (options and has_value and name in _EXCLUDE_OPTIONS):
            exempt.add(index)
        value_next = (options and not has_value and name in _EXCLUDE_OPTIONS
                      and (name != "--ignore" or head in _IGNORE_VALUE_COMMANDS))
    if not head:
        return exempt, replacing
    start = head_index + 1
    if head == "mv":
        moved = {start + index for index in _move_destinations(plain[start:])}
        (exempt if _move_keeps_files(plain[start:], git=False) else replacing).update(moved)
    elif head == "mkdir":
        exempt.update(start + index for index, word in enumerate(plain[start:]) if not word.startswith("-"))
    elif head in _TEXT_COMMANDS and writes_file:
        exempt.update(range(start, len(plain)))
    elif head == "git":
        sub = _git_subcommand(plain, head_index)
        if sub is not None and plain[sub] == "mv":
            moved = {sub + 1 + index for index in _move_destinations(plain[sub + 1:])}
            (exempt if _move_keeps_files(plain[sub + 1:], git=True) else replacing).update(moved)
        elif sub is not None and plain[sub] in ("commit", "tag"):
            for index in range(sub + 1, len(plain)):
                word = plain[index]
                if word in ("-m", "--message") and index + 1 < len(plain):
                    exempt.add(index + 1)
                elif word.startswith("--message=") or (word.startswith("-m") and len(word) > 2):
                    exempt.add(index)
    return exempt, replacing


def _builds_at_run_time(plain: list[str], head_index: int) -> bool:
    """eval, source or '.', or an interpreter given inline code (sh -c, python -c)."""
    if head_index < len(plain) and _command_name(plain[head_index]) in _SOURCE_COMMANDS:
        return True
    for position, word in enumerate(plain):
        name = _command_name(word)
        if name not in _INTERPRETERS:
            continue
        for flag in plain[position + 1:]:
            lowered = flag.casefold()
            if lowered in _INLINE_FLAGS or (name in _SHELLS and _SHELL_FLAG_RE.fullmatch(lowered)):
                return True
    return False


def _candidates(word: str) -> list[str]:
    candidates = [word]
    if "=" in word:
        candidates.append(word.split("=", 1)[1])  # VAR=path, --option=path
    if len(word) > 2 and word[0] == "-" and word[1].isalpha():
        candidates.append(word[2:])  # -Ipath
    return [candidate for candidate in candidates if candidate]


def _colon_tails(word: str, scoped_variables: bool = False) -> list[tuple[str, bool]]:
    """The text after each ':' in a word that is no drive (C:\\x), and whether it starts at the top (:/x).

    git reads REV:PATH, :PATH and :N:PATH (a stage in the index) as a path in a
    tree, and the pathspec magic :/PATH, :(top)PATH or :(icase)PATH as a path
    from the top of the repository; host:PATH names a remote copy. An exclusion
    (:!PATH, :^PATH, :(exclude)PATH) gives no tail. With scoped_variables
    (PowerShell), a ':' after a '$' may belong to a variable ($env:X, $script:x)
    and gives no tail.
    """
    tails: list[tuple[str, bool]] = []
    index = word.find(":", 2 if _DRIVE_ROOT_RE.match(word) else 0)
    while index >= 0:
        if scoped_variables and "$" in word[:index]:
            break
        tail = word[index + 1:]
        top = index == 0 and tail[:1] in ("/", "\\")
        if tail.startswith("("):
            close = tail.find(")")
            tail = "" if close < 0 or "exclude" in tail[:close].casefold() else tail[close + 1:]
        elif tail.startswith(("!", "^")):
            tail = ""
        if tail:
            tails.append((tail, top))
        index = word.find(":", index + 1)
    return tails


# -- code inside words ------------------------------------------------------------

def _skip_escaped(text: str, index: int, close: str) -> int:
    """Index after close, where a backslash escapes the next character (`...` and $'...')."""
    size = len(text)
    while index < size:
        if text[index] == "\\":
            index += 2
        elif text[index] == close:
            return index + 1
        else:
            index += 1
    return size


def _heredoc_text_end(text: str, start: int) -> int | None:
    """Index after the Claude Code commit form at start, or None.

    The form is $(cat <<'EOF', its lines, a line EOF, then ')'. The quoted
    delimiter makes the lines text that the shell does not expand. Nothing
    may follow the delimiter on the first line or come between the closing
    line and ')', and no line may hold $( or a backtick, so a shell that reads
    the form another way still runs nothing.
    """
    match = _HEREDOC_TEXT_RE.match(text, start)
    if match is None:
        return None
    position = match.end()
    while True:
        newline = text.find("\n", position)
        if newline < 0:
            return None
        line = text[position:newline]
        position = newline + 1
        if line == match.group(1):
            close = _CLOSE_PAREN_RE.match(text, position)
            return close.end() if close else None
        if "$(" in line or "`" in line:
            return None


def _walk(text: str, index: int, spans: list[str] | None, inside: bool) -> int:
    """Walk shell text from index; record code spans in spans and return where the walk ends.

    Single quotes and $'...' are literal; double quotes keep $( and backticks
    live. With inside, index starts the body of a $( ... ) and the walk ends
    after the ')' that closes it. Quotes start over in that body, as in the
    shell. A body that may hold a ')' of its own (a case pattern, ${...}, a
    comment, a here-document) runs to the end of the text, so an early ')'
    never hides code.
    """
    start = index
    size = len(text)
    depth = 0
    quoted = False  # inside double quotes
    while index < size:
        char = text[index]
        if char == "\\":
            index += 2
        elif char == "`":
            end = _skip_escaped(text, index + 1, "`")
            if spans is not None:
                spans.append(text[index:end])
            index = end
        elif text.startswith("$(", index):
            end = _heredoc_text_end(text, index)
            if end is None:
                end = _walk(text, index + 2, None, True)
                if spans is not None:
                    spans.append(text[index:end])
            index = end
        elif char == '"':
            quoted = not quoted
            index += 1
        elif quoted:
            index += 1
        elif char == "'":
            end = text.find("'", index + 1)
            index = size if end < 0 else end + 1
        elif text.startswith("$'", index):
            index = _skip_escaped(text, index + 2, "'")
        elif text.startswith("<<", index) and not text.startswith("<<<", index):
            if spans is not None:
                spans.append(text[index:])  # where the here-document ends is not parsed
            return size
        elif inside and (char == "#" or text.startswith("${", index)):
            return size
        elif inside and char == "(":
            depth += 1
            index += 1
        elif inside and char == ")":
            if depth:
                depth -= 1
                index += 1
            elif _CASE_RE.search(text, start, index):
                return size
            else:
                return index + 1
        else:
            index += 1
    return size


def _code_spans(command: str) -> list[str]:
    """Text in a command that the shell may run as code: each $(...) and `...`
    (quoted or not), and everything after a here-document operator (<<).

    shlex keeps "$(cat zone)" in one word, splits `cat zone` across words and
    reads quotes in a here-document as quotes, so word exemptions cannot
    cover this text; Guard.bash checks it on its own. The Claude Code commit
    form "$(cat <<'EOF' ... EOF)" is text (_heredoc_text_end).
    """
    spans: list[str] = []
    _walk(command, 0, spans, False)
    return spans


# -- PowerShell parser ---------------------------------------------------------

def _powershell_executable() -> str | None:
    """Windows PowerShell 5.1 in the Windows folder; never looked up in PATH or the working folder."""
    if not _WINDOWS:
        return None
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if not root:
        return None
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else None


def _valid_facts(facts: object) -> bool:
    if not isinstance(facts, dict):
        return False
    for key in _PS_COUNT_KEYS:
        value = facts.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    for key in _PS_TEXT_KEYS:
        value = facts.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
    commands = facts.get("commands")
    return isinstance(commands, list) and all(item is None or isinstance(item, str) for item in commands)


def _read_facts(raw: bytes) -> dict | None:
    """The parser's JSON line (the last line that starts with '{'), or None."""
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    for line in reversed(lines):
        if line.strip().startswith("{"):
            try:
                facts = json.loads(line)
            except ValueError:
                return None
            return facts if _valid_facts(facts) else None
    return None


def parse_powershell(command: str) -> tuple[dict | None, str]:
    """Parse one command with ps-parse.ps1 next to this file, without running it.

    Returns (facts, "") or (None, why there are none). The child is Windows
    PowerShell 5.1 (-NoProfile -NonInteractive -ExecutionPolicy Bypass -File)
    with the command on stdin as UTF-8 and PARSE_TIMEOUT seconds to answer.
    """
    parser = os.path.join(os.path.dirname(os.path.abspath(__file__)), PARSER_NAME)
    if not os.path.isfile(parser):
        return None, f"was not found ({PARSER_NAME} next to the guard)"
    executable = _powershell_executable()
    if executable is None:
        return None, "needs Windows PowerShell (powershell.exe), which was not found"
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", parser],
            input=command.encode("utf-8"), capture_output=True, timeout=PARSE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None, f"did not answer within {PARSE_TIMEOUT:g} seconds"
    except (OSError, ValueError) as error:  # ValueError: text that UTF-8 cannot encode
        return None, f"could not be started ({type(error).__name__})"
    if result.returncode != 0:
        return None, f"failed (exit code {result.returncode})"
    facts = _read_facts(result.stdout)
    if facts is None:
        return None, "gave no answer the guard can read"
    return facts, ""


def _ps_fixed(path: str) -> bool:
    """True for a path that Set-Location cannot move: X:\\ or UNC on Windows, /x elsewhere."""
    return bool(_PS_FIXED_PATH_RE.match(path)) if _WINDOWS else path.startswith("/")


def _ps_file_path(text: str) -> str | None:
    """The file-system path a PowerShell string names, or None for another provider's path."""
    stripped = _PS_FILESYSTEM_RE.sub("", text, count=1)
    if stripped != text:
        return stripped  # FileSystem::D:\x is D:\x
    return None if _PS_PROVIDER_RE.match(text) else text


def _ps_run_time(facts: dict, names: list[str]) -> str:
    """Plain words for the run-time constructs in the parser facts, or ''."""
    labels = [_PS_FLAG_LABELS.get(flag, _label(flag)) for flag in facts["flags"]]
    if facts["expandable"]:
        labels.append("an expandable string")
    if any(_command_name(text) in _INTERPRETERS for text in (*names, *facts["strings"])):
        labels.append("another shell or interpreter")
    return ", ".join(dict.fromkeys(labels))


class Guard:
    """Decisions for one hook call: vault roots, zones and working directory."""

    def __init__(self, vault: str, zones: Sequence[str], cwd: object = None) -> None:
        self.zones = tuple(zones)
        self.zone_stems = [(zone, _short_stem(zone.split("/")[-1])) for zone in self.zones]
        given = os.path.normpath(os.path.abspath(_native(vault)))
        self.vault_root = given
        self._tree_roots: list[str] | None = None
        roots = [given]
        try:
            real = os.path.realpath(given)
        except (OSError, ValueError):
            real = given
        if _WINDOWS:
            real = _DEVICE_PREFIX_RE.sub("", real)
        if real not in roots:
            roots.append(real)
        self.roots = [_fold_path(root) for root in roots]
        self.vault_on_unc = any(_is_unc(root) for root in roots)
        self.cwd = given
        if isinstance(cwd, str) and cwd.strip():
            self.cwd = self.absolute(cwd) or given

    # -- paths -------------------------------------------------------------------

    def absolute(self, text: str, base: str | None = None) -> str | None:
        """The absolute lexical path of text, or None when it cannot be known."""
        text = _native(text)
        if text.startswith("~"):
            if text != "~" and text[1] not in "/\\":
                return None  # ~user: another account's home
            text = os.path.expanduser(text)
            if text.startswith("~"):
                return None
        try:
            return os.path.normpath(os.path.abspath(os.path.join(base or self.cwd, text)))
        except (OSError, ValueError):
            return None

    def locate(self, absolute: str) -> _Location:
        paths = [absolute]
        if not _is_unc(absolute) or self.vault_on_unc:  # no network lookup for other hosts
            try:
                real = os.path.realpath(absolute)
            except (OSError, ValueError):
                real = absolute
            if _WINDOWS:
                real = _DEVICE_PREFIX_RE.sub("", real)
            if real != absolute:
                paths.append(real)
        views: list[tuple[str, ...]] = []
        for path in paths:
            folded = _fold_path(path)
            for root in self.roots:
                parts = _relative(folded, root)
                if parts is not None and parts not in views:
                    views.append(parts)
        return _Location(paths, views)

    def zone_of(self, location: _Location) -> str | None:
        for parts in location.views:
            for zone in self.zones:
                if zone_matches(parts, zone):
                    return zone
        return None

    def control_of(self, location: _Location) -> str | None:
        """Name the guard control that the path is, or None."""
        for parts in location.views:
            folded = "/".join(part.casefold() for part in parts)
            if folded in (CONFIG_REL, GUARD_REL, PARSER_REL):
                return folded
        for path in location.paths:  # .claude or .claude/settings*.json, in or outside the vault
            parts = [_fold_part(part).casefold() for part in re.split(r"[\\/]+", path) if part]
            if parts and parts[-1] == ".claude":
                return ".claude"
            if len(parts) >= 2 and parts[-2] == ".claude" and fnmatch.fnmatchcase(parts[-1], "settings*.json"):
                return ".claude/" + parts[-1]
        return None

    def above_vault(self, location: _Location) -> bool:
        for path in location.paths:
            folded = _fold_path(path)
            prefix = folded if folded.endswith(os.sep) else folded + os.sep
            if any(root.startswith(prefix) for root in self.roots):
                return True
        return False

    def short_names(self, text: str) -> tuple[list[str], bool]:
        """Zones, and whether a guard control, that a Windows 8.3 short name in text can stand for.

        Only Windows resolves short names. 90-ASS~1 or 90A1B2~1 can stand for
        90-assets (_short_alias); whether it does is left to realpath, which
        expands a short name that exists.
        """
        if not _WINDOWS or "~" not in text:
            return [], False
        bases = [match.group(1) for match in _SHORT_NAME_RE.finditer(text)]
        if not bases:
            return [], False
        zones = [zone for zone, stem in self.zone_stems if any(_short_alias(base, stem) for base in bases)]
        return zones, any(_short_alias(base, stem) for base in bases for stem in _CONTROL_STEMS)

    def zones_in(self, text: str) -> list[str]:
        """Zones whose last segment, or a short name that can stand for it, appears in text, ignoring case."""
        folded = text.casefold()
        short = self.short_names(text)[0]
        return [zone for zone in self.zones if zone.split("/")[-1].casefold() in folded or zone in short]

    def control_named(self, text: str) -> bool:
        """True when text names a guard control, or holds a short name that can stand for one."""
        return bool(_CONTROL_TEXT_RE.search(text.casefold())) or self.short_names(text)[1]

    def zone_named_in(self, text: str) -> str | None:
        """The first zone named in text (zones_in), or None."""
        zones = self.zones_in(text)
        return zones[0] if zones else None

    def mentions(self, text: str) -> tuple[list[str], bool]:
        """Zones named in a raw command (zones_in), and whether a control name is.

        A second look drops quotes, backslashes, braces and commas, so 90-ass"ets"
        and 90-ass\\ets still show the zone name.
        """
        variants = (text.casefold(), re.sub(r"[\"'\\{},]", "", text.casefold()))
        named = [set(self.zones_in(variant)) for variant in variants]
        zones = [zone for zone in self.zones if any(zone in found for found in named)]
        return zones, any(self.control_named(variant) for variant in variants)

    def tree_roots(self) -> list[str]:
        """Where a path in a git tree starts: the vault root, and the repository root above the working directory."""
        if self._tree_roots is None:
            roots = [self.vault_root]
            if not _is_unc(self.cwd) or self.vault_on_unc:  # no network lookup for other hosts
                top = _git_toplevel(self.cwd)
                if top is not None and top not in roots:
                    roots.append(top)
            self._tree_roots = roots
        return self._tree_roots

    def word_candidates(self, word: str, powershell: bool = False) -> list[str]:
        """The literal pieces of one word to check as paths.

        The word, the value after '=' (VAR=path, --option=path) and after -X
        (-Ipath), and the text after each ':' (_colon_tails). git resolves
        REV:PATH from the top of the repository, so a relative tail is also
        checked from each tree root (tree_roots); ./ and ../ follow the working
        directory there.
        """
        candidates = _candidates(word)
        for candidate in list(candidates):
            for tail, top in _colon_tails(candidate, scoped_variables=powershell):
                candidates.append(tail)
                anchor = tail.lstrip("/\\") if top else tail
                if anchor and not _ROOTED_RE.match(anchor) and not anchor.startswith(("./", "../", ".\\", "..\\")):
                    candidates.extend(os.path.join(root, anchor) for root in self.tree_roots())
        return list(dict.fromkeys(candidates))

    def path_hit(self, text: str, base: str | None = None) -> tuple[str | None, str | None, bool]:
        """(zone, control, resolved) for one path; resolved is False for ~user or an invalid name."""
        absolute = self.absolute(text, base)
        if absolute is None:
            return None, None, False
        location = self.locate(absolute)
        return self.zone_of(location), self.control_of(location), True

    def glob_hit(self, base: str, pattern: str) -> tuple[str, str] | None:
        """(DENY, zone) when a wildcard path starts in or names a zone,
        (ASK, zone) when a wildcard sits next to a zone name, else None."""
        text = _native(pattern.strip())
        match = _WILDCARD_RE.search(text)
        if match is None:
            zone = self.path_hit(text, base)[0]
            return (DENY, zone) if zone else None
        literal = _literal_prefix(text, match.start())
        rest = text[len(literal):]
        absolute = self.absolute(literal or ".", base)
        if absolute is None:
            named = self.zone_named_in(text)
            return (ASK, named) if named else None
        location = self.locate(absolute)
        zone = self.zone_of(location)
        if zone is not None:
            return DENY, zone
        inside = bool(location.views)
        if not inside and not self.above_vault(location):
            return None  # the wildcard part cannot reach the vault
        segments = [segment for segment in re.split(r"[\\/]+", rest) if segment]
        if ".." in segments:
            named = self.zone_named_in(rest)
            return (ASK, named) if named else None
        asked = None
        for segment in segments:
            folded = _fold_part(segment).casefold()
            wild = _WILDCARD_RE.search(segment) is not None
            for zone in self.zones:
                last = zone.split("/")[-1].casefold()
                if wild and last in folded:
                    asked = asked or zone  # a wildcard next to the zone name
                elif not wild and "/" not in zone and folded == last:
                    if inside:
                        return DENY, zone  # a one-segment zone matches at any depth
                    asked = asked or zone
                elif not wild and folded == last:
                    asked = asked or zone  # the last segment of a longer zone after a wildcard
        return (ASK, asked) if asked else None

    # -- path tools ---------------------------------------------------------------

    def path_tool(self, tool: str, tool_input: dict) -> list[tuple[str, str]]:
        verdicts: list[tuple[str, str]] = []
        unknown = (ASK, f"the {tool} input has no path field the guard recognizes, so it cannot "
                        "check deny zones.")
        found = False
        for key, value in tool_input.items():
            name = key.casefold() if isinstance(key, str) else ""
            if not name.endswith(("path", "paths")) or value is None:
                continue
            items = value if isinstance(value, list) and name.endswith("paths") else [value]
            for item in items:
                if not isinstance(item, str):
                    verdicts.append(unknown)
                    continue
                if not item:
                    continue
                found = True
                zone, control, resolved = self.path_hit(item)
                if not resolved:
                    verdicts.append((ASK, f"the {tool} path cannot be resolved (a ~user home or an "
                                          "invalid name), so the guard cannot check deny zones."))
                elif zone is not None:
                    verdicts.append((DENY, _deny_target(tool, zone)))
                elif control is not None and tool in WRITE_TOOLS:
                    verdicts.append((ASK, _ask_control(f"{tool} changes", control)))
        base = self.cwd
        where = tool_input.get("path")
        if tool in ("Glob", "Grep") and isinstance(where, str) and where:
            base = self.absolute(where) or self.cwd
        if tool == "Glob":
            pattern = tool_input.get("pattern")
            if not isinstance(pattern, str):
                verdicts.append(unknown)
            elif pattern.strip():
                verdicts.extend(self._pattern_verdicts("the Glob pattern", base, pattern))
        elif tool == "Grep":
            glob = tool_input.get("glob")
            if glob is not None and not isinstance(glob, str):
                verdicts.append(unknown)
            elif glob and glob.strip() and not glob.strip().startswith("!"):  # '!x' excludes
                verdicts.extend(self._pattern_verdicts("the Grep glob", base, glob))
        elif not found:
            verdicts.append(unknown)
        return verdicts

    def _pattern_verdicts(self, what: str, base: str, pattern: str) -> list[tuple[str, str]]:
        hit = self.glob_hit(base, pattern)
        if hit is None:
            return []
        if hit[0] == DENY:
            return [(DENY, _deny_pattern(what, hit[1]))]
        return [(ASK, _ask_wildcard(what, hit[1]))]

    # -- shells -----------------------------------------------------------------

    def cwd_verdicts(self) -> list[tuple[str, str]]:
        zone = self.zone_of(self.locate(self.cwd))
        if zone is None:
            return []
        return [(ASK, f"the shell's working directory is inside the deny zone '{_label(zone)}'.")]

    def shell_candidate(self, text: str, *, exempt: bool, cwd_known: bool,
                        deny=_deny_shell, decision: str = DENY) -> list[tuple[str, str]]:
        """Verdicts for one literal piece of a shell word.

        A path in a zone gives (decision, deny(zone)): deny, or ask for the
        destination of a move that may replace a file there.
        """
        named = None if exempt else self.zone_named_in(text)
        control = self.control_named(text)
        if named is None and not control:
            return []
        native = _native(text)
        if not cwd_known and not _ROOTED_RE.match(native):
            return [(ASK, _ask_unchecked(named, "changes directory before this relative path"))]
        runtime = _RUNTIME_RE.search(native)
        wildcard = _WILDCARD_RE.search(native)
        verdicts: list[tuple[str, str]] = []
        if runtime is None and wildcard is None:
            zone, control_path, resolved = self.path_hit(native)
            if not resolved:
                return [(ASK, _ask_unchecked(named, "uses a path it cannot resolve (a ~user home or "
                                                    "an invalid name)"))]
            if zone is not None and not exempt:
                verdicts.append((decision, deny(zone)))
            if control_path is not None:
                verdicts.append((ASK, _ask_control("this command names", control_path)))
            return verdicts
        if runtime is not None:
            stop = min(found.start() for found in (runtime, wildcard) if found is not None)
            literal = _literal_prefix(native, stop)
            if literal and not exempt:
                zone = self.path_hit(literal)[0]
                if zone is not None:
                    verdicts.append((decision, deny(zone)))
            verdicts.append((ASK, _ask_unchecked(named, "builds this path at run time")))
            return verdicts
        if not exempt:
            hit = self.glob_hit(self.cwd, native)
            if hit is not None and hit[0] == DENY:
                verdicts.append((decision, deny(hit[1])))
            elif hit is not None:
                verdicts.append((ASK, _ask_wildcard("this command", hit[1])))
        if control and _may_match_control(native):
            verdicts.append((ASK, _ask_unchecked(None, "matches it with a wildcard")))
        return verdicts

    def code_verdicts(self, command: str) -> list[tuple[str, str]]:
        """Ask when a zone or control name sits in text the shell may run as code (_code_spans)."""
        coded: list[str] = []
        control = False
        for span in _code_spans(command):
            named, has_control = self.mentions(span)
            coded.extend(zone for zone in named if zone not in coded)
            control = control or has_control
        if not coded and not control:
            return []
        return [(ASK, _ask_unchecked(coded[0] if coded else None,
                                     "inside $(...), backticks or after a here-document (<<), where the "
                                     "shell may run it as code"))]

    def bash(self, command: str) -> list[tuple[str, str]]:
        verdicts = self.cwd_verdicts()
        zones, controls = self.mentions(command)
        if not zones and not controls:
            return verdicts
        stripped = _strip_comments(command)
        verdicts.extend(self.code_verdicts(stripped))
        if _IO_MARK in stripped:  # the character that marks descriptor numbers (_mark_io_numbers)
            return verdicts + [(ASK, _ask_unchecked(zones[0] if zones else None,
                                                    "holds the control character U+0001"))]
        try:
            words = _words(_mark_io_numbers(stripped))
        except ValueError:
            return verdicts + [(ASK, _ask_unchecked(zones[0] if zones else None,
                                                    "cannot be split into words (unbalanced quotes)"))]
        seen: set[str] = set()   # zones named by some word
        live: set[str] = set()   # zones named by a word that is not exempt
        control_seen = False     # a control name in some word
        control_live = False     # a control name in a word that is not exempt
        dynamic = bool(_RUNTIME_TEXT_RE.search(command))
        cwd_known = True
        for simple in _simple_commands(words):
            plain = [word for word, redirect in simple if not redirect]
            head_index = _head_index(plain)
            exempt, replacing = _exempt_words(plain, head_index, _writes_stdout_to_file(simple))
            dynamic = dynamic or _builds_at_run_time(plain, head_index)
            sub = _git_subcommand(plain, head_index)
            staging = sub is not None and plain[sub] == "add"
            plain_index = 0
            for word, redirect in simple:
                word_exempt = False
                replaces = False
                deny = _deny_shell
                if not redirect:
                    word_exempt = plain_index in exempt
                    replaces = plain_index in replacing
                    plain_index += 1
                    deny = _ask_replace if replaces else _deny_git_add if staging else _deny_shell
                named = set(self.zones_in(word)) & set(zones)
                has_control = self.control_named(word)
                if not named and not has_control:
                    continue
                seen.update(named)
                control_seen = control_seen or has_control
                if not word_exempt:
                    live.update(named)
                    control_live = control_live or has_control
                for candidate in self.word_candidates(word):
                    verdicts.extend(self.shell_candidate(candidate, exempt=word_exempt, cwd_known=cwd_known,
                                                         deny=deny, decision=ASK if replaces else DENY))
            if head_index < len(plain) and _command_name(plain[head_index]) in _CD_COMMANDS:
                cwd_known = False
        missing = [zone for zone in zones if zone not in seen]
        control_missing = controls and not control_seen
        if dynamic and (live or missing or control_live or control_missing):
            first = next((zone for zone in zones if zone in live or zone in missing), None)
            verdicts.append((ASK, _ask_unchecked(first, "builds commands or paths at run time "
                                                        "($VAR, $(...), backticks, eval, source, "
                                                        "sh -c or inline code)")))
        if missing:
            verdicts.append((ASK, _ask_unchecked(missing[0], "not as a path it can resolve (a "
                                                             "comment, brace expansion or joined "
                                                             "text)")))
        elif control_missing:
            verdicts.append((ASK, _ask_unchecked(None, "not as a path it can resolve")))
        return verdicts

    def powershell(self, command: str) -> list[tuple[str, str]]:
        """The Bash pre-filter, then ps-parse.ps1: literal zone paths deny, anything unclear asks."""
        verdicts = self.cwd_verdicts()
        zones, controls = self.mentions(command)
        if "`" in command:  # PowerShell's escape character: 90-ass`ets is 90-assets
            unescaped, more = self.mentions(command.replace("`", ""))
            zones = [zone for zone in self.zones if zone in zones or zone in unescaped]
            controls = controls or more
        if not zones and not controls:
            return verdicts  # no parser process: such a call costs only the Python start
        first = zones[0] if zones else None
        facts, problem = parse_powershell(command)
        if facts is None:
            return verdicts + [(ASK, _ask_unchecked(first, f"the PowerShell parser {problem}"))]
        names = [name for name in facts["commands"] if name]
        moved = any(_command_name(name) in _PS_LOCATION_COMMANDS for name in names)
        texts = facts["strings"] + facts["expandable_strings"]
        for text in texts:
            path = _ps_file_path(text)
            if path is None:  # Registry::, Env:, HKLM:, another PSDrive
                named = self.zone_named_in(text)
                if named or self.control_named(text):
                    verdicts.append((ASK, _ask_unchecked(named, "only in a provider path outside the file "
                                                                "system (Registry::, Env:, a PSDrive)")))
                continue
            for candidate in self.word_candidates(path, powershell=True):
                if moved and not _ps_fixed(_native(candidate)):
                    # Set-Location may run before or after this word (loops, blocks).
                    named = self.zone_named_in(candidate)
                    if named or self.control_named(candidate):
                        verdicts.append((ASK, _ask_unchecked(named, "changes the location (Set-Location, cd) "
                                                                    "in the same command")))
                    continue
                verdicts.extend(self.shell_candidate(candidate, exempt=False, cwd_known=True,
                                                     deny=_deny_powershell))
        if facts["errors"]:
            verdicts.append((ASK, _ask_unchecked(first, "does not parse as Windows PowerShell 5.1")))
        run_time = _ps_run_time(facts, names)
        if run_time:
            verdicts.append((ASK, _ask_unchecked(first, f"has parts that are only known when it runs "
                                                        f"({run_time})")))
        named_by = [set(self.zones_in(text)) for text in texts]
        missing = [zone for zone in zones if not any(zone in found for found in named_by)]
        if missing:
            verdicts.append((ASK, _ask_unchecked(missing[0], "not as a literal path it can resolve (a comment, "
                                                             "a variable or function name, or joined text)")))
        elif controls and not any(self.control_named(text) for text in texts):
            verdicts.append((ASK, _ask_unchecked(None, "not as a literal path it can resolve")))
        return verdicts


def _strongest(verdicts: list[tuple[str, str]]) -> tuple[str, str] | None:
    for decision in (DENY, ASK):
        for verdict in verdicts:
            if verdict[0] == decision:
                return verdict
    return None


def evaluate(payload: object, vault: str, warn=None) -> tuple[str, str] | None:
    """Return (decision, reason) for one hook input, or None for no decision."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        return ASK, ("the hook input is not a JSON object with tool_name and tool_input, so "
                     "the guard cannot check deny zones.")
    tool = payload["tool_name"]
    if tool not in PATH_TOOLS and tool not in SHELL_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ASK, f"the {tool} input is not a JSON object, so the guard cannot check deny zones."
    if tool == "Monitor" and tool_input.get("command") is None and "ws" in tool_input:
        return None  # a WebSocket watch runs no shell command
    vault = _native(vault)  # an MSYS root (/c/vault) reads the same config as C:/vault
    zones, problem = load_zones(vault)
    if problem is not None and warn is not None:
        warn(f"deny-guard: {problem}; using the template deny zones")
    guard = Guard(vault, zones, payload.get("cwd"))
    if tool in PATH_TOOLS:
        return _strongest(guard.path_tool(tool, tool_input))
    command = tool_input.get("command")
    if not isinstance(command, str):
        return ASK, f"the {tool} input has no command string, so the guard cannot check deny zones."
    if tool == "PowerShell":
        return _strongest(guard.powershell(command))
    return _strongest(guard.bash(command))  # Bash, and Monitor, whose command runs in the Bash tool's shell


def hook_output(decision: str, reason: str) -> dict:
    if decision not in (DENY, ASK):  # never widen: anything else becomes a question
        decision = ASK
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": decision,
                                   "permissionDecisionReason": "deny-guard: " + reason}}


def _warn(message: str) -> None:
    try:
        sys.stderr.write(message + "\n")
    except (OSError, ValueError):
        pass


def main() -> int:
    vault = _native(os.environ.get("CLAUDE_PROJECT_DIR", ""))
    if not vault.strip() or not os.path.isdir(vault):
        _warn("deny-guard: CLAUDE_PROJECT_DIR does not name the vault folder; no decision was made")
        return 0
    try:
        raw = sys.stdin.buffer.read()
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        result = evaluate(payload, vault, _warn)
    except Exception as error:  # a crash must not let the call through unchecked
        result = (ASK, f"internal error ({type(error).__name__}); confirm this tool call by hand.")
    if result is not None:
        sys.stdout.write(json.dumps(hook_output(*result), ensure_ascii=True) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
