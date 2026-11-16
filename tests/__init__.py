"""Tests for the agentic-vault repository."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
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
                shutil.rmtree(ext, ignore_errors=ignore_errors)
            elif not ignore_errors:
                raise

    tempfile.TemporaryDirectory._rmtree = _longpath_rmtree
