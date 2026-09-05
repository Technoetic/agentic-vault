"""Safe path handling shared by agentic-vault note readers."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Sequence


_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_RESERVED_NAME = re.compile(
    r"(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?"
)
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _relative_parts(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty relative path")
    if value.startswith(("/", "\\")) or _DRIVE_PATH.match(value):
        raise ValueError(f"{label} must be relative")

    normalized = value.replace("\\", "/")
    parts = tuple(normalized.split("/"))
    for part in parts:
        if (
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or _RESERVED_NAME.fullmatch(part)
        ):
            raise ValueError(f"{label} contains an unsafe path segment")
    return parts


def _is_link_or_reparse(path: Path) -> bool:
    metadata = os.lstat(path)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG
    )


def _is_denied(parts: tuple[str, ...], deny_zones: Sequence[str]) -> bool:
    if isinstance(deny_zones, (str, bytes)):
        raise ValueError("deny_zones must be a sequence of paths")
    folded_parts = tuple(part.casefold() for part in parts)
    for index, zone in enumerate(deny_zones):
        zone_parts = tuple(
            part.casefold()
            for part in _relative_parts(zone, f"deny_zones[{index}]")
        )
        if len(zone_parts) == 1:
            if zone_parts[0] in folded_parts:
                return True
        elif folded_parts[: len(zone_parts)] == zone_parts:
            return True
    return False


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
    parts = _relative_parts(rel_path, "note path")
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
        resolved_relative = resolved_candidate.relative_to(resolved_vault)
    except ValueError as exc:
        raise ValueError("note path escapes the vault") from exc
    if _is_denied(resolved_relative.parts, deny_zones):
        raise ValueError("resolved note path is in a denied zone")
    return resolved_candidate
