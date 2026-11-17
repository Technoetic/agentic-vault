"""Environment checks that run before every other test module.

The file name sorts first on purpose. ``python -m unittest discover -s tests``
imports the test modules as top-level modules, so ``tests/__init__.py`` — which
makes the cleanup of temporary folders work for entries beyond the plain
260-character limit of Windows — is not imported on its own. Importing the
package here installs that cleanup before any other module creates a temporary
folder.

The module also holds the guard of a declared long TEMP run
(``scripts/long_temp_suite.py``): Python's ``tempfile`` skips a TEMP folder it
cannot use and silently falls back to another one, so a run that sets
``AGENTIC_VAULT_REQUIRE_LONG_TEMP`` fails here unless ``tempfile`` really uses
the declared folder.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import tests as tests_package  # noqa: E402 - installs the long-path cleanup on Windows

try:
    from _winfs import extended
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import extended

REQUIRE = "AGENTIC_VAULT_REQUIRE_LONG_TEMP"
WINDOWS = os.name == "nt"


def declared_temporary_folder() -> str | None:
    """The folder the environment names, in the order ``tempfile`` reads the variables."""
    for name in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            return value
    return None


class LongPathCleanupTests(unittest.TestCase):
    def test_the_package_cleanup_is_installed_on_windows(self) -> None:
        current = tempfile.TemporaryDirectory._rmtree
        if WINDOWS:
            self.assertIs(current.__func__, tests_package._longpath_rmtree.__func__)
        else:
            self.assertFalse(hasattr(tests_package, "_longpath_rmtree"))

    def test_a_temporary_folder_with_a_deep_read_only_file_is_removed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve()
        nested = root
        while len(str(nested)) < 270:
            nested = nested / ("segment_" + "c" * 24)
        deep = extended(nested)
        deep.mkdir(parents=True)
        locked = deep / "read-only.bin"
        locked.write_bytes(b"x")
        os.chmod(locked, stat.S_IREAD)
        temporary.cleanup()
        self.assertFalse(os.path.lexists(extended(root)))


class DeclaredLongTempTests(unittest.TestCase):
    def test_tempfile_uses_the_declared_long_temp(self) -> None:
        used = tempfile.gettempdir()
        with tempfile.TemporaryFile(dir=used) as handle:
            handle.write(b"x")
        declared = os.environ.get(REQUIRE)
        if declared is None:
            return  # not a declared long TEMP run: the folder only has to be usable
        named = declared_temporary_folder()
        self.assertIsNotNone(named, "a declared long TEMP run names no temporary folder")
        self.assertEqual(os.path.normcase(os.path.abspath(used)), os.path.normcase(os.path.abspath(named)),
                         "tempfile uses another folder than the declared one")
        self.assertGreaterEqual(len(used), int(declared), "the temporary folder is shorter than declared")


if __name__ == "__main__":
    unittest.main()
