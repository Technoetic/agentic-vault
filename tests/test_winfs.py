"""Tests for the shared Windows junction helper in tests/_winfs.py."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    import _winfs
except ImportError:  # e.g. ``python -m unittest tests.test_winfs`` from the repo root
    from tests import _winfs


@unittest.skipUnless(os.name == "nt", "Windows junction helper")
class MakeJunctionWindowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "target"
        self.target.mkdir()
        (self.target / "a.md").write_text("alpha\n", encoding="utf-8")
        (self.target / "nested").mkdir()

    def remove_junction(self, link: Path) -> None:
        self.addCleanup(lambda: os.rmdir(link) if os.path.lexists(link) else None)

    def test_junction_resolves_to_target_and_lists_its_entries(self) -> None:
        link = self.root / "link"
        self.assertIsNone(_winfs.make_junction(link, self.target))
        self.remove_junction(link)
        self.assertEqual(sorted(os.listdir(link)), ["a.md", "nested"])
        self.assertEqual((link / "a.md").read_text(encoding="utf-8"), "alpha\n")
        self.assertTrue(os.path.samefile(link, self.target))
        self.assertEqual(Path(os.path.realpath(link)), Path(os.path.realpath(self.target)))

    def test_junction_is_a_reparse_point_not_a_symlink(self) -> None:
        link = self.root / "link"
        _winfs.make_junction(str(link), str(self.target))
        self.remove_junction(link)
        attributes = os.lstat(link).st_file_attributes
        self.assertTrue(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        self.assertFalse(link.is_symlink())
        if hasattr(os.path, "isjunction"):  # Python 3.12+
            self.assertTrue(os.path.isjunction(link))
        # Removing the junction leaves the target and its contents intact.
        os.rmdir(link)
        self.assertTrue((self.target / "a.md").is_file())

    def test_existing_directory_at_link_raises_oserror(self) -> None:
        link = self.root / "occupied"
        link.mkdir()
        with self.assertRaises(OSError):
            _winfs.make_junction(link, self.target)
        self.assertEqual(os.listdir(link), [])
        self.assertFalse(os.lstat(link).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    def test_existing_file_at_link_raises_oserror(self) -> None:
        link = self.root / "occupied.txt"
        link.write_bytes(b"keep")
        with self.assertRaises(OSError):
            _winfs.make_junction(link, self.target)
        self.assertEqual(link.read_bytes(), b"keep")

    def test_existing_junction_at_link_raises_oserror(self) -> None:
        link = self.root / "link"
        _winfs.make_junction(link, self.target)
        self.remove_junction(link)
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(OSError):
            _winfs.make_junction(link, other)
        self.assertTrue(os.path.samefile(link, self.target))


class MakeJunctionDispatchTests(unittest.TestCase):
    """Platform-independent checks with the Win32 call and subprocess mocked."""

    LINK = os.path.join("vault", "link")
    TARGET = os.path.join("outside", "target")

    def test_primary_path_passes_target_then_link_and_skips_subprocess(self) -> None:
        fake = mock.Mock()
        with mock.patch.object(_winfs, "_winapi", fake), \
                mock.patch.object(_winfs.subprocess, "run") as run:
            _winfs.make_junction(Path(self.LINK), Path(self.TARGET))
        fake.CreateJunction.assert_called_once_with(str(Path(self.TARGET)), str(Path(self.LINK)))
        run.assert_not_called()

    def test_primary_path_lets_oserror_propagate(self) -> None:
        fake = mock.Mock()
        fake.CreateJunction.side_effect = FileExistsError(17, "exists")
        with mock.patch.object(_winfs, "_winapi", fake), \
                mock.patch.object(_winfs.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                _winfs.make_junction(self.LINK, self.TARGET)
        run.assert_not_called()

    def fallback_run(self, returncode: int) -> mock.Mock:
        # CP949 bytes for a Korean cmd message; they must never be decoded.
        completed = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=b"\xc1\xa4\xbb\xf3", stderr=b"\xbf\xc0\xb7\xf9")
        return mock.Mock(return_value=completed)

    def assert_bytes_mklink_call(self, run: mock.Mock) -> None:
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["cmd", "/c", "mklink", "/J", self.LINK, self.TARGET])
        self.assertTrue(kwargs.get("capture_output"))
        for decoding in ("text", "encoding", "errors", "universal_newlines"):
            self.assertNotIn(decoding, kwargs)

    def test_fallback_without_create_junction_uses_bytes_mklink(self) -> None:
        run = self.fallback_run(0)
        with mock.patch.object(_winfs, "_winapi", object()), \
                mock.patch.object(_winfs.subprocess, "run", run):
            self.assertIsNone(_winfs.make_junction(self.LINK, self.TARGET))
        self.assert_bytes_mklink_call(run)

    def test_fallback_without_winapi_module_uses_bytes_mklink(self) -> None:
        run = self.fallback_run(0)
        with mock.patch.object(_winfs, "_winapi", None), \
                mock.patch.object(_winfs.subprocess, "run", run):
            _winfs.make_junction(self.LINK, self.TARGET)
        self.assert_bytes_mklink_call(run)

    def test_fallback_nonzero_exit_raises_content_free_oserror(self) -> None:
        run = self.fallback_run(1)
        with mock.patch.object(_winfs, "_winapi", object()), \
                mock.patch.object(_winfs.subprocess, "run", run):
            with self.assertRaises(OSError) as caught:
                _winfs.make_junction(self.LINK, self.TARGET)
        self.assert_bytes_mklink_call(run)
        message = str(caught.exception)
        self.assertIn("exit code 1", message)
        self.assertNotIn(self.LINK, message)
        self.assertNotIn(self.TARGET, message)

    def test_fallback_timeout_raises_oserror(self) -> None:
        run = mock.Mock(side_effect=subprocess.TimeoutExpired(["cmd"], 10))
        with mock.patch.object(_winfs, "_winapi", object()), \
                mock.patch.object(_winfs.subprocess, "run", run):
            with self.assertRaises(OSError):
                _winfs.make_junction(self.LINK, self.TARGET)


class JunctionCallSiteTests(unittest.TestCase):
    def test_test_modules_create_junctions_only_through_the_helper(self) -> None:
        tests_dir = Path(__file__).resolve().parent
        helper_files = {"_winfs.py", Path(__file__).name}
        offenders = sorted(
            path.name for path in tests_dir.glob("*.py")
            if path.name not in helper_files
            and "mklink" in path.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
