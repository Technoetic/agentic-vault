"""Helpers for running the session hooks under Antigravity CLI.

Antigravity CLI runs a plugin's hook commands with the plugin folder as the
working directory, writes one camelCase JSON event to stdin and reads one
JSON object from stdout. The hooks use these helpers only when the generated
hooks.json passes ``--host antigravity``: read the event, find the vault
among the folders the event names, let one of the handlers registered for an
event act, and write the answer.

Standard library only, so a hook can import this module before anything else.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unicodedata


# The event carries lastUserInput, which can be far larger than a Claude Code
# hook event; past this bound the event is not used.
MAX_EVENT_BYTES = 16 * 1024 * 1024
CONFIG_REL = "00-meta/vault-config.json"
# Claim markers live in this folder of the temporary folder. On POSIX, where
# the temporary folder can be shared (/tmp), the name ends with "-<user id>".
CLAIM_FOLDER = "agentic-vault-hook-claims"
CLAIM_NAME_CHARS = 40
# A marker holds its key this long: a second handler of the same event, or the
# same key again, is not processed within it. An older marker is stale and the
# next caller processes the key again, so a reopened conversation or an
# executionNum that comes again is handled after two minutes.
CLAIM_VALID_SECONDS = 120
# The sweep removes marker files older than this.
CLAIM_MAX_AGE_SECONDS = 2 * 24 * 60 * 60
CLAIM_SWEEP_ENTRIES = 200
_HEX = frozenset("0123456789abcdef")
# Control characters, and lone surrogates that no UTF-8 encoder accepts.
_REJECTED_CATEGORIES = frozenset({"Cc", "Cs"})


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _no_constant(value: str) -> object:
    raise ValueError("invalid_constant")


def read_event(stream=None) -> dict | None:
    """Return the hook event object, or None when there is none to use.

    None stands for a terminal or absent stdin, more than MAX_EVENT_BYTES,
    bytes that are not UTF-8 JSON (a repeated key or NaN included) and a JSON
    value that is not an object. Never raises: the hook still has to answer
    with a JSON object.
    """
    try:
        stream = sys.stdin if stream is None else stream
        if stream is None or stream.isatty():
            return None
        binary = getattr(stream, "buffer", stream)
        raw = binary.read(MAX_EVENT_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > MAX_EVENT_BYTES:
            return None
        event = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys,
                           parse_constant=_no_constant)
    except Exception:  # any input, any failure: no event rather than no answer
        return None
    return event if isinstance(event, dict) else None


def _has_config(folder: Path) -> bool:
    try:
        return (folder / CONFIG_REL).is_file()
    except (OSError, ValueError):
        return False


def _vaults(values: list[str]) -> list[Path]:
    """The absolute folders among values that hold a vault configuration, each once."""
    found: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if not value or not Path(value).is_absolute():
            continue
        try:
            key = os.path.normcase(os.path.realpath(value))
        except (OSError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        folder = Path(value)
        if _has_config(folder):
            found.append(folder)
    return found


def vault_candidates(event: object) -> list[Path]:
    """Return the vault folders the event names, the event's cwd first.

    Only ``cwd`` (a string) and ``workspacePaths`` (a list of strings) are
    read. A relative path is skipped: it would be read against the hook's
    working directory, which is the plugin folder. Two spellings of one
    folder count once.
    """
    if not isinstance(event, dict):
        return []
    values: list[str] = []
    cwd = event.get("cwd")
    if isinstance(cwd, str):
        values.append(cwd)
    workspaces = event.get("workspacePaths")
    if isinstance(workspaces, list) and all(isinstance(item, str) for item in workspaces):
        values.extend(workspaces)
    return _vaults(values)


def resolve_vault(explicit, event: object) -> tuple[Path | None, str]:
    """Return (vault, "one_vault"), (None, "no_vault") or (None, "several_vaults").

    An explicit vault is the only candidate; otherwise the candidates come
    from the event. CLAUDE_PROJECT_DIR and the process working directory are
    never used.
    """
    candidates = _vaults([str(explicit)]) if explicit is not None else vault_candidates(event)
    if len(candidates) == 1:
        return candidates[0], "one_vault"
    return None, ("several_vaults" if candidates else "no_vault")


def _sweep(folder: Path) -> None:
    """Remove marker and takeover files older than two days; best effort, bounded."""
    cutoff = time.time() - CLAIM_MAX_AGE_SECONDS
    try:
        with os.scandir(folder) as entries:
            for scanned, entry in enumerate(entries):
                if scanned >= CLAIM_SWEEP_ENTRIES:
                    break
                if len(entry.name) != CLAIM_NAME_CHARS or not set(entry.name) <= _HEX:
                    continue
                try:
                    if (entry.is_file(follow_symlinks=False)
                            and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                        os.unlink(entry.path)
                except OSError:
                    continue
    except OSError:
        pass


def _claim_folder() -> Path | None:
    """Return the folder for claim markers, or None when it must not be used.

    The Windows temporary folder belongs to the user, so the folder there
    keeps its plain name. A POSIX temporary folder such as /tmp can be
    shared: the name ends with the user id, the folder is made with mode
    0700, and it is used only when lstat shows a directory (a symbolic link
    is not one) owned by this user. Otherwise no marker is made there and
    nothing in it is swept; the caller acts (fail open).
    """
    base = Path(tempfile.gettempdir())
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        folder = base / CLAIM_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    uid = getuid()
    folder = base / f"{CLAIM_FOLDER}-{uid}"
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(folder)
    if stat.S_ISDIR(info.st_mode) and info.st_uid == uid:
        return folder
    return None


def _make_marker(path: Path) -> bool | None:
    """Create path exclusively: True when made, False when it exists, None on any other failure."""
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return None
    try:
        os.close(descriptor)
    except OSError:
        pass
    return True


def _fresh(info: os.stat_result) -> bool:
    """Whether a marker still holds its key: its time is within CLAIM_VALID_SECONDS of now.

    A time further ahead of the clock (a clock set back) counts as stale too,
    so no marker holds a key much longer than the validity.
    """
    return abs(time.time() - info.st_mtime) < CLAIM_VALID_SECONDS


def _renew_stale(marker: Path) -> bool:
    """For a marker that exists: False while it is fresh, True for the one caller that renews it.

    Handlers of one event can find the same stale marker at once. Each tries
    to create, exclusively, a takeover file named after the marker and its
    modification time; the one that creates it sets the marker's time to now
    and acts, and the others return False. A renewal never removes the
    marker, so no caller finds its name free in between. The sweep removes
    takeover files like markers, after two days.
    """
    try:
        info = os.lstat(marker)
    except FileNotFoundError:  # swept meanwhile: a new claim
        return _make_marker(marker) is not False
    except OSError:
        return True
    if not stat.S_ISREG(info.st_mode):
        return True
    if _fresh(info):
        return False
    takeover = hashlib.sha256(f"{marker.name}\0{info.st_mtime_ns}".encode("ascii")).hexdigest()
    if _make_marker(marker.with_name(takeover[:CLAIM_NAME_CHARS])) is False:
        return False
    try:
        os.utime(marker)
    except FileNotFoundError:  # swept meanwhile: claim it anew
        return _make_marker(marker) is not False
    except OSError:
        pass
    return True


def claim_once(*parts: str) -> bool:
    """Return True when this caller is to act for the key these parts make, else False.

    Antigravity CLI runs every handler registered for an event, and the
    package registers several so that one of them finds a Python. The first
    one to create the marker file acts; the others find the marker fresh. A
    marker holds its key for CLAIM_VALID_SECONDS; after that one caller
    renews it and acts again. Any failure other than a fresh or a lost claim
    returns True: acting twice is better than losing the event.
    """
    digest = hashlib.sha256("\0".join(parts).encode("utf-8", "surrogatepass")).hexdigest()
    try:
        folder = _claim_folder()
    except (OSError, ValueError):
        return True
    if folder is None:
        return True
    _sweep(folder)
    marker = folder / digest[:CLAIM_NAME_CHARS]
    made = _make_marker(marker)
    if made is not False:  # made it, or could not make it: act
        return True
    return _renew_stale(marker)


def _discard_output(stream) -> None:
    """Point the file descriptor behind stream at the null device; best effort."""
    try:
        descriptor = stream.fileno()
        null = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):
        return
    if null == descriptor:  # the descriptor had been closed and now names the null device
        return
    try:
        os.dup2(null, descriptor)
    except OSError:
        pass
    finally:
        try:
            os.close(null)
        except OSError:
            pass


def emit(obj: object) -> None:
    """Write obj as one line of compact JSON: UTF-8 bytes and an LF, flushed.

    A host that has stopped reading (a closed pipe) makes the write fail. The
    hook must still exit 0 without a traceback, which would name the plugin
    path, so the failure is dropped. The bytes that did not go out stay in
    the stream's buffer and the interpreter flushes them again at exit; the
    descriptor behind the stream is pointed at the null device first, so that
    flush cannot fail again.
    """
    data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8", "replace")
    stream = sys.stdout
    if stream is None:
        return
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            stream.write(data.decode("utf-8"))
            stream.flush()
            return
        stream.flush()
        buffer.write(data)
        buffer.flush()
    except (OSError, ValueError):
        _discard_output(stream)


def valid_id(value: object, limit: int) -> str | None:
    """Return value when it is a non-empty string of at most limit characters without controls."""
    if (isinstance(value, str) and 0 < len(value) <= limit
            and not any(unicodedata.category(character) in _REJECTED_CATEGORIES for character in value)):
        return value
    return None
