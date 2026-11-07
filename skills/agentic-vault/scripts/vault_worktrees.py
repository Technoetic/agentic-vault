"""Linked working trees of the vault's repository: a copy of the vault, not more notes.

A linked working tree (git worktree add, or a host session that makes one inside
the vault) checks out the tracked notes a second time. Its deny zones then sit
under another folder, where a root-anchored zone no longer matches, and its notes
duplicate the real ones. vault_paths.zone_matches stays as it is: that rule is a
tested contract. This module adds the second view of a path instead.

A folder counts only when its .git is a regular file whose gitdir value names
<common>/worktrees/<name> of the repository that holds this vault. A submodule
also has a .git file, but its value names modules/<name>, so its notes stay in
every walk. A nested repository and a linked tree of another repository stay
folders too. Without a repository the checks read nothing and answer no.

The rule between the SHARED WORKTREE RULE markers is copied verbatim into
assets/adapters/deny-guard/deny-guard.py and vault_healthcheck.py, which run
alone from a vault's 00-meta/scripts and cannot import this file;
tests/test_vault_worktrees.py fails if they drift.
"""
from __future__ import annotations

import os
import stat
import sys
from typing import Sequence

from vault_paths import zone_matches


# --- BEGIN SHARED WORKTREE RULE (verbatim copy of vault_worktrees.py) ---
GITFILE_LIMIT = 4096  # a .git file holds one path


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


def _other_share(path: str, anchor: str) -> bool:
    """True for a UNC path on another share than anchor, which is never looked up."""
    drive = os.path.splitdrive(path)[0]
    return drive.startswith("\\\\") and os.path.normcase(drive) != os.path.normcase(os.path.splitdrive(anchor)[0])


def _canonical_path(path: str) -> str:
    """The form two repository paths are compared in: links resolved, case folded as the file system does."""
    try:
        text = os.path.realpath(path)
    except (OSError, ValueError):
        text = path
    if text.startswith("\\\\?\\"):
        text = text[4:]
    text = os.path.normcase(text)
    return text.casefold() if sys.platform == "darwin" else text


def gitfile_target(directory: str) -> str | None:
    """The path that a regular .git file in directory names, or None for anything else."""
    marker = os.path.join(directory, ".git")
    try:
        info = os.lstat(marker)
        if not stat.S_ISREG(info.st_mode) or info.st_size > GITFILE_LIMIT:
            return None
        with open(marker, "rb") as handle:
            text = handle.read(GITFILE_LIMIT + 1).decode("utf-8")
    except (OSError, ValueError):
        return None
    if not text.startswith("gitdir:"):
        return None
    value = text[len("gitdir:"):].strip()
    if not value or any(ord(char) < 32 for char in value):
        return None
    return os.path.normpath(os.path.join(directory, value))


def repository_common(directory: str) -> str | None:
    """The common git directory of the working tree at directory, in _canonical_path form, or None."""
    marker = os.path.join(directory, ".git")
    try:
        if stat.S_ISDIR(os.lstat(marker).st_mode):
            return _canonical_path(marker)
    except (OSError, ValueError):
        return None
    target = gitfile_target(directory)
    if target is None or _other_share(target, directory):
        return None
    if os.path.basename(os.path.dirname(target)) == "worktrees" and os.path.isfile(os.path.join(target, "commondir")):
        target = os.path.dirname(os.path.dirname(target))
    return _canonical_path(target)


def linked_worktree(directory: str, common: str) -> bool:
    """True when directory is a linked working tree of the repository whose common git directory is common.

    The gitdir value must be <common>/worktrees/<name>: not a submodule, whose
    value is .../modules/<name>, and not a tree of another repository. The
    admin folder itself may be gone: the copy of the vault is still there.
    """
    target = gitfile_target(directory)
    if target is None or os.path.basename(os.path.dirname(target)) != "worktrees":
        return False
    owner = os.path.dirname(os.path.dirname(target))
    return not _other_share(owner, common) and _canonical_path(owner) == common
# --- END SHARED WORKTREE RULE ---


class WorktreeScope:
    """The linked working trees of the repository that holds one vault."""

    def __init__(self, vault) -> None:
        self.vault = os.path.abspath(os.fspath(vault))
        self.common = None
        self.prefix: tuple[str, ...] = ()  # the vault's folder inside a working tree
        top = _git_toplevel(self.vault)
        if top is None:
            return
        self.common = repository_common(top)
        relative = os.path.relpath(self.vault, top)
        if relative != os.curdir:
            self.prefix = tuple(part.casefold() for part in relative.split(os.sep))

    def linked(self, directory) -> bool:
        """True when this folder is a linked working tree of the vault's repository."""
        return self.common is not None and linked_worktree(os.fspath(directory), self.common)

    def _trees(self, parts: Sequence[str]) -> list[tuple[str, ...]]:
        """What follows each linked tree on a vault-relative path, outermost tree first."""
        found: list[tuple[str, ...]] = []
        if self.common is None:
            return found
        current = self.vault
        for index, part in enumerate(parts):
            current = os.path.join(current, part)
            if linked_worktree(current, self.common):
                found.append(tuple(parts[index + 1:]))
        return found

    def inside(self, parts: Sequence[str]) -> bool:
        """True when a vault-relative path is a linked tree or lies in one."""
        return bool(self._trees(parts))

    def views(self, parts: Sequence[str]) -> tuple[tuple[str, ...], ...]:
        """The path as the vault's own relative path in each linked tree it lies in."""
        size = len(self.prefix)
        return tuple(rest[size:] for rest in self._trees(parts)
                     if tuple(part.casefold() for part in rest[:size]) == self.prefix)

    def denied(self, parts: Sequence[str], zones: Sequence[str]) -> bool:
        """True when a zone covers the path as seen from a linked tree."""
        return any(zone_matches(view, zone) for view in self.views(parts) for zone in zones)
