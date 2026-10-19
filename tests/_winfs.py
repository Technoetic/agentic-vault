"""Windows filesystem helpers shared by the test modules.

``make_junction`` replaces ``cmd /c mklink /J`` subprocess calls. On Korean
Windows ``cmd`` prints CP949 text, and tests that decoded it as UTF-8 made the
subprocess reader thread print a ``UnicodeDecodeError`` traceback. The primary
path calls the Win32 API directly and produces no console output at all.
"""
from __future__ import annotations

import os
import subprocess

try:
    import _winapi
except ImportError:  # pragma: no cover - non-Windows platforms
    _winapi = None

MKLINK_TIMEOUT_SECONDS = 10


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
