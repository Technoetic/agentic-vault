"""Safe path handling shared by agentic-vault note readers.

This module is the single source of three rules: which relative path segments
are allowed, how a deny/exclude zone matches a path, and that no symlink or
Windows reparse point (junction) is traversed. Plugin scripts import it.

vault_healthcheck.py is the one exception. It is installed into each vault as a
standalone file (00-meta/scripts/vault_healthcheck.py, run by the pre-commit
hook without the plugin on sys.path), so it cannot import this module. It keeps
a verbatim copy of the block between the SHARED PATH RULE markers below, and
tests/test_rule_sources.py fails when the two copies differ.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Sequence


_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


# --- BEGIN SHARED PATH RULE (verbatim copy in vault_healthcheck.py) ---
# Windows reserves device names in any letter case and with any extension
# ("con", "Con.md"). The case-folded set and the IGNORECASE pattern are both
# applied, so the merged rule is never looser than either earlier copy.
RESERVED_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
    *(f"com{i}" for i in (*range(1, 10), "¹", "²", "³")),
    *(f"lpt{i}" for i in (*range(1, 10), "¹", "²", "³")),
})
_RESERVED_DEVICE_PATTERN = re.compile(
    r"(?i)(?:con|prn|aux|nul|clock\$|conin\$|conout\$"
    r"|com[1-9¹²³]|lpt[1-9¹²³])"
)
UNSAFE_SEGMENT_CHARACTERS = '<>:"|?*'


def unsafe_path_segment(part: str) -> bool:
    """Return True when one '/'-separated segment is not a portable vault name.

    Rejected: empty, '.', '..', a trailing dot or space (Windows drops them),
    control characters, any of <>:"|?* (':' also blocks NTFS alternate data
    streams) and reserved device names with or without an extension.
    """
    stem = part.split(".", 1)[0]
    return (
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or any(ord(char) < 32 or char in UNSAFE_SEGMENT_CHARACTERS for char in part)
        or stem.casefold() in RESERVED_DEVICE_NAMES
        or _RESERVED_DEVICE_PATTERN.fullmatch(stem) is not None
    )
# --- END SHARED PATH RULE ---


def relative_parts(value: object, label: str) -> tuple[str, ...]:
    """Split a vault-relative path after applying the shared segment rule.

    Backslashes are separators. The value is used exactly as given: it is not
    trimmed, so "note.md " is a different (and rejected) Windows name.
    """
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty relative path")
    if value.startswith(("/", "\\")) or _DRIVE_PATH.match(value):
        raise ValueError(f"{label} must be relative")

    parts = tuple(value.replace("\\", "/").split("/"))
    if any(unsafe_path_segment(part) for part in parts):
        raise ValueError(f"{label} contains an unsafe path segment")
    return parts


# Earlier private name, kept for callers that imported it.
_relative_parts = relative_parts


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


def _is_link_or_reparse(path: Path) -> bool:
    metadata = os.lstat(path)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG
    )


def _is_denied(parts: tuple[str, ...], deny_zones: Sequence[str]) -> bool:
    if isinstance(deny_zones, (str, bytes)):
        raise ValueError("deny_zones must be a sequence of paths")
    for index, zone in enumerate(deny_zones):
        relative_parts(zone, f"deny_zones[{index}]")
        if zone_matches(parts, zone):
            return True
    return False


def _contained_relative_path(candidate: Path, root: Path) -> Path:
    r"""Compare a transient DOS extended prefix without changing the I/O path.

    Windows realpath can retain \\?\ after a file disappears between its two
    handle lookups. Only ordinary absolute drive paths are equivalent here:
    device/UNC namespaces stay distinct, and the existing root must also have
    the same file identity through the candidate's original ancestor path.
    """
    try:
        return candidate.relative_to(root)
    except ValueError:
        if os.name != 'nt':
            raise
    def drive_path(path):
        value = str(path)
        if value.startswith('\\\\?\\'):
            value = value[4:]
        if not re.match(r'^[A-Za-z]:\\', value):
            raise ValueError('incomparable_windows_namespace')
        compared = Path(value)
        if any(unsafe_path_segment(part) for part in compared.parts[1:]):
            raise ValueError('unsafe_windows_comparison')
        return compared
    relative = drive_path(candidate).relative_to(drive_path(root))
    ancestor = candidate
    for _ in relative.parts:
        ancestor = ancestor.parent
    if not os.path.samefile(ancestor, root):
        raise ValueError('root_identity_changed')
    return relative


def resolve_note_path(
    vault: Path,
    rel_path: str,
    deny_zones: Sequence[str] = (),
) -> Path:
    """Return a contained note path after rejecting unsafe path components.

    Both symlinks and Windows reparse points (including junctions) are rejected
    before resolution. Missing final components are allowed so callers can use
    the same helper before checking whether an optional note exists.
    """
    if isinstance(vault, (str, bytes)) or not isinstance(vault, Path):
        raise ValueError("vault must be a Path")
    parts = relative_parts(rel_path, "note path")
    if _is_denied(parts, deny_zones):
        raise ValueError("note path is in a denied zone")

    try:
        resolved_vault = vault.resolve(strict=True)
    except RuntimeError as exc:
        raise OSError("vault path could not be resolved") from exc
    if not resolved_vault.is_dir():
        raise OSError("vault is not a directory")
    if _is_link_or_reparse(vault):
        raise ValueError("vault must not be a symlink or reparse point")

    candidate = resolved_vault.joinpath(*parts)
    current = resolved_vault
    for part in parts:
        current = current / part
        try:
            if _is_link_or_reparse(current):
                raise ValueError("note path contains a symlink or reparse point")
        except FileNotFoundError:
            continue

    try:
        resolved_candidate = candidate.resolve(strict=False)
    except RuntimeError as exc:
        raise OSError("note path could not be resolved") from exc
    try:
        resolved_relative = _contained_relative_path(resolved_candidate, resolved_vault)
    except ValueError as exc:
        raise ValueError("note path escapes the vault") from exc
    if _is_denied(resolved_relative.parts, deny_zones):
        raise ValueError("resolved note path is in a denied zone")
    return resolved_candidate
