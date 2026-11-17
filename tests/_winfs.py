"""Windows filesystem helpers shared by the test modules.

``make_junction`` replaces ``cmd /c mklink /J`` subprocess calls. On Korean
Windows ``cmd`` prints CP949 text, and tests that decoded it as UTF-8 made the
subprocess reader thread print a ``UnicodeDecodeError`` traceback. The primary
path calls the Win32 API directly and produces no console output at all.

``extended``, ``plain`` and ``deep_path`` let a fixture create, read or remove
entries beyond the plain 260-character limit of Windows without long-path
support. They change nothing on other systems, and ``deep_path`` changes
nothing for a path that stays below the limit.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

try:
    import _winapi
except ImportError:  # pragma: no cover - non-Windows platforms
    _winapi = None

MKLINK_TIMEOUT_SECONDS = 10
PLAIN_PATH_LIMIT = 240  # the margin below Windows MAX_PATH (260) that vault_evidence.fs_path uses


def extended(path: str | os.PathLike[str]) -> Path:
    """The Windows extended-length form of an absolute path; any other path, and any other system, unchanged."""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return Path(path)
    if re.match(r"^[A-Za-z]:\\", text):
        return Path("\\\\?\\" + text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path(path)


def plain(path: str | os.PathLike[str]) -> Path:
    """The path without the extended-length prefix, for messages and comparisons."""
    text = str(path)
    if os.name == "nt" and text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[8:])
    if os.name == "nt" and text.startswith("\\\\?\\"):
        return Path(text[4:])
    return Path(path)


def deep_path(path: str | os.PathLike[str], reserve: int = 0) -> Path:
    """path, in extended-length form when it plus ``reserve`` more characters reaches the plain limit."""
    return extended(path) if len(str(path)) + reserve >= PLAIN_PATH_LIMIT else Path(path)


def make_junction(link: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Create a directory junction at ``link`` that points to ``target``.

    Uses ``_winapi.CreateJunction(target, link)`` (CPython 3.5+ on Windows) and
    lets its ``OSError`` propagate, for example when ``link`` already exists.
    Only when that function is unavailable does it fall back to
    ``cmd /c mklink /J``; the fallback captures output as bytes and never
    decodes it, and raises ``OSError`` with a content-free message on failure.
    """
    create = getattr(_winapi, "CreateJunction", None)
    if create is not None:
        create(str(target), str(link))
        return
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            timeout=MKLINK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("mklink /J timed out") from exc
    if result.returncode != 0:
        raise OSError(f"mklink /J failed with exit code {result.returncode}")
