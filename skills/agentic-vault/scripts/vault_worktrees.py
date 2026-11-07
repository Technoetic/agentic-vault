"""Linked working trees of the vault's repository: a copy of the vault, not more notes.

A linked working tree (git worktree add, or a host session that makes one inside
the vault) checks out the tracked notes a second time. Its deny zones then sit
under another folder, where a root-anchored zone no longer matches, and its notes
duplicate the real ones. vault_paths.zone_matches stays as it is: that rule is a
tested contract. This module adds the second view of a path instead.

A folder counts when its .git is a regular file whose gitdir value names
<common>/worktrees/<name> of the repository that holds this vault. A submodule
also has a .git file, but its value names modules/<name>, so its notes stay in
every walk. A nested repository and a linked tree of another repository stay
folders too. Without a repository the checks read nothing and answer no.

The two uses of that answer differ in what a wrong answer costs. A zone is closed
on the .git file alone, so a stale or hand-written file only closes more. A walk
or a backup leaves a folder out, which would hide real notes, so it also needs
git's admin folder to name this folder back (registered_worktree). A folder that
fails that test is walked as usual, with its zones still closed.

The rule between the SHARED WORKTREE RULE markers is copied verbatim into
assets/adapters/deny-guard/deny-guard.py and vault_healthcheck.py, which run
alone from a vault's 00-meta/scripts and cannot import this file;
tests/test_vault_worktrees.py fails if they drift.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from vault_paths import resolve_note_path, zone_matches


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


def _small_text(path: str) -> str | None:
    """The text of a small regular file, or None for anything else."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > GITFILE_LIMIT:
            return None
        with open(path, "rb") as handle:
            return handle.read(GITFILE_LIMIT + 1).decode("utf-8")
    except (OSError, ValueError):
        return None


def gitfile_target(directory: str) -> str | None:
    """The path that a regular .git file in directory names, or None for anything else."""
    text = _small_text(os.path.join(directory, ".git"))
    if text is None or not text.startswith("gitdir:"):
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


def registered_worktree(directory: str, common: str) -> bool:
    """True when directory is a linked tree and git's admin folder names it back.

    git keeps <common>/worktrees/<name>/gitdir, the path of the tree's .git file.
    A .git file written by hand has no such record, and neither has a copy whose
    admin folder is gone. Only a walk or a backup needs this answer, because it
    leaves the folder out: a zone stays closed on linked_worktree alone.
    """
    if not linked_worktree(directory, common):
        return False
    admin = gitfile_target(directory)
    text = None if admin is None else _small_text(os.path.join(admin, "gitdir"))
    value = "" if text is None else text.strip()
    if not value or any(ord(char) < 32 for char in value):
        return False
    named = os.path.normpath(os.path.join(admin, value))
    return not _other_share(named, directory) and _canonical_path(named) == _canonical_path(
        os.path.join(directory, ".git"))


def vault_prefix(vault: str, top: str) -> tuple:
    """The vault's folder inside a working tree whose top is top, case folded."""
    relative = os.path.relpath(vault, top)
    return () if relative == os.curdir else tuple(part.casefold() for part in relative.split(os.sep))


def tree_tails(root: str, parts, common: str, counts=linked_worktree) -> list:
    """What follows each tree on a path below root, outermost tree first."""
    found: list = []
    current = root
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        if counts(current, common):
            found.append(tuple(parts[index + 1:]))
    return found


def tree_views(root: str, parts, common: str, prefix: tuple) -> tuple:
    """The path as the vault's own relative path in each linked tree it lies in."""
    size = len(prefix)
    return tuple(rest[size:] for rest in tree_tails(root, parts, common)
                 if tuple(part.casefold() for part in rest[:size]) == prefix)
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
        self.prefix = vault_prefix(self.vault, top)

    def linked(self, directory) -> bool:
        """True when this folder's .git file says it is a linked tree of the vault's repository."""
        return self.common is not None and linked_worktree(os.fspath(directory), self.common)

    def registered(self, directory) -> bool:
        """True when git's admin folder confirms it too: the test for leaving a folder out."""
        return self.common is not None and registered_worktree(os.fspath(directory), self.common)

    def inside(self, parts: Sequence[str]) -> bool:
        """True when a vault-relative path is a linked tree or lies in one."""
        return self.common is not None and bool(tree_tails(self.vault, parts, self.common))

    def inside_registered(self, parts: Sequence[str]) -> bool:
        """True when a vault-relative path is a registered tree or lies in one."""
        return self.common is not None and bool(
            tree_tails(self.vault, parts, self.common, registered_worktree))

    def views(self, parts: Sequence[str]) -> tuple[tuple[str, ...], ...]:
        """The path as the vault's own relative path in each linked tree it lies in."""
        return () if self.common is None else tree_views(self.vault, parts, self.common, self.prefix)

    def denied(self, parts: Sequence[str], zones: Sequence[str]) -> bool:
        """True when a zone covers the path as seen from a linked tree."""
        return any(zone_matches(view, zone) for view in self.views(parts) for zone in zones)


def checked_note_path(vault: Path, relative: str, zones: Sequence[str]) -> Path:
    """resolve_note_path, then the same zones as seen from each linked tree on the path.

    Every tool that takes a named path goes through this. vault_paths.py stays as
    it is, so the second view sits here. The result is a resolved path, so a case
    variant or short name of a zone is compared in its real form.
    """
    path = resolve_note_path(vault, relative, zones)
    root = vault.resolve(strict=True)
    if WorktreeScope(root).denied(path.relative_to(root).parts, zones):
        raise ValueError("note path is in a denied zone")
    return path
