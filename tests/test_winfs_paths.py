"""Tests for the extended-length path helpers of ``tests/_winfs.py``.

Fixtures use these helpers to create, read and remove entries beyond the plain
260-character limit of Windows without long-path support. The helpers must
leave every other system, and every short path, exactly as it was.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    from _winfs import PLAIN_PATH_LIMIT, deep_path, extended, plain
except ImportError:  # pragma: no cover - package-style test runs
    from tests._winfs import PLAIN_PATH_LIMIT, deep_path, extended, plain

WINDOWS = os.name == "nt"
PREFIX = "\\\\?\\"


class ExtendedPathHelperTests(unittest.TestCase):
    def test_relative_paths_are_unchanged_everywhere(self) -> None:
        relative = Path("notes") / "a.md"
        self.assertEqual(str(extended(relative)), str(relative))
        self.assertEqual(str(plain(relative)), str(relative))
        self.assertEqual(str(deep_path(Path("x" * 400))), "x" * 400)

    def test_absolute_paths_gain_the_prefix_only_on_windows(self) -> None:
        if WINDOWS:
            self.assertEqual(str(extended("C:\\a\\b.md")), PREFIX + "C:\\a\\b.md")
            self.assertEqual(str(extended("\\\\server\\share\\b.md")), PREFIX + "UNC\\server\\share\\b.md")
            self.assertEqual(str(extended(PREFIX + "C:\\a\\b.md")), PREFIX + "C:\\a\\b.md")
            self.assertEqual(str(plain(PREFIX + "C:\\a\\b.md")), "C:\\a\\b.md")
            self.assertEqual(str(plain(PREFIX + "UNC\\server\\share\\b.md")), "\\\\server\\share\\b.md")
            self.assertEqual(str(plain("C:\\a\\b.md")), "C:\\a\\b.md")
        else:
            absolute = Path(tempfile.gettempdir()) / "a" / "b.md"
            self.assertEqual(extended(absolute), absolute)
            self.assertEqual(plain(absolute), absolute)

    def test_deep_path_changes_nothing_below_the_limit(self) -> None:
        root = Path(tempfile.gettempdir()).resolve()
        short = root / "note.md"
        self.assertLess(len(str(short)), PLAIN_PATH_LIMIT)
        self.assertEqual(str(deep_path(short)), str(short))
        just_below = root / ("n" * (PLAIN_PATH_LIMIT - 1 - len(str(root)) - 1))
        self.assertEqual(len(str(just_below)), PLAIN_PATH_LIMIT - 1)
        self.assertEqual(str(deep_path(just_below)), str(just_below))

    def test_deep_path_switches_at_the_limit_and_counts_the_reserve(self) -> None:
        root = Path(tempfile.gettempdir()).resolve()
        at_limit = root / ("n" * (PLAIN_PATH_LIMIT - len(str(root)) - 1))
        self.assertEqual(len(str(at_limit)), PLAIN_PATH_LIMIT)
        short = root / "work"
        reserve = PLAIN_PATH_LIMIT - len(str(short))
        if WINDOWS:
            self.assertEqual(str(deep_path(at_limit)), PREFIX + str(at_limit))
            self.assertEqual(str(deep_path(short, reserve)), PREFIX + str(short))
            self.assertEqual(str(deep_path(short, reserve - 1)), str(short))
        else:
            self.assertEqual(deep_path(at_limit), at_limit)
            self.assertEqual(deep_path(short, reserve), short)

    def test_a_file_beyond_260_characters_can_be_written_read_and_removed(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="av-winfs-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(extended(base), ignore_errors=True))
        nested = base
        while len(str(nested)) < 270:
            nested = nested / ("segment_" + "y" * 24)
        target = deep_path(nested / "note.md")
        target.parent.mkdir(parents=True)
        target.write_text("deep\n", encoding="utf-8")
        self.assertEqual(target.read_text(encoding="utf-8"), "deep\n")
        self.assertEqual(str(plain(target)), str(nested / "note.md"))
        self.assertGreater(len(str(plain(target))), 260)
        shutil.rmtree(extended(base))
        self.assertFalse(extended(base).exists())


if __name__ == "__main__":
    unittest.main()
