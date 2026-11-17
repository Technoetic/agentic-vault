"""Tests for the agentic-vault repository.

On Windows the cleanup of ``tempfile.TemporaryDirectory`` is wrapped: when the
plain cleanup fails (an entry beyond the plain 260-character limit cannot be
removed through its plain path), the same cleanup runs again on the
extended-length form of the folder, so read-only entries are still handled the
way ``tempfile`` handles them. ``python -m unittest discover -s tests`` does not
import this package on its own; ``tests/test_000_environment.py`` imports it
before any other test module runs.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


if os.name == "nt":
    _orig_rmtree = tempfile.TemporaryDirectory._rmtree

    @classmethod
    def _longpath_rmtree(cls, name, ignore_errors=False, **kwargs):
        try:
            _orig_rmtree(name, ignore_errors=ignore_errors, **kwargs)
        except OSError:
            raw = os.fspath(name)
            if isinstance(raw, str) and not raw.startswith("\\\\?\\"):
                ext = "\\\\?\\" + str(Path(raw).resolve())
                _orig_rmtree(ext, ignore_errors=ignore_errors, **kwargs)
            elif not ignore_errors:
                raise

    tempfile.TemporaryDirectory._rmtree = _longpath_rmtree
