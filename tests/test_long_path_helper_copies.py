"""The long-path helper block of the standalone repository tools.

``scripts/verify_codex_plugin.py``, ``scripts/verify_antigravity_plugin.py`` and
``scripts/build_antigravity_package.py`` are standalone files (standard library
only, loaded by path), so each carries its own copy of the same small block.
The copies must stay byte-identical, and the block must leave every short path
and every other system exactly as it was.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("scripts/verify_codex_plugin.py", "scripts/verify_antigravity_plugin.py",
         "scripts/build_antigravity_package.py")
START = "# --- long-path helpers (keep this block identical in the standalone tools that carry it) ---\n"
END = "# --- end of long-path helpers ---\n"
MESSAGE_START = "# The extended-length prefix of a drive or UNC path inside a message:"
MESSAGE_END = '    return EXTENDED_PREFIX.sub(lambda found: found.group(1) * 2 if found.group(2) else "", text)\n'
WINDOWS = os.name == "nt"
PREFIX = "\\\\?\\"


def block_of(relative: str) -> str:
    text = (ROOT / relative).read_bytes().decode("utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise AssertionError(f"{relative}: the long-path helper block must occur exactly once")
    return text[text.index(START):text.index(END) + len(END)]


class LongPathHelperCopiesTests(unittest.TestCase):
    def test_the_three_copies_are_byte_identical(self) -> None:
        blocks = {relative: block_of(relative) for relative in TOOLS}
        first = blocks[TOOLS[0]]
        for relative, block in blocks.items():
            with self.subTest(tool=relative):
                self.assertEqual(block, first)
        for name in ("PLAIN_PATH_LIMIT = 240", "def _extended(path) -> Path:", "def _plain(path) -> Path:",
                     "def _workspace(root, reserve: int) -> Path:"):
            self.assertIn(name, first)

    def test_the_message_helper_is_byte_identical_in_the_three_tools(self) -> None:
        copies = {}
        for relative in TOOLS:
            text = (ROOT / relative).read_bytes().decode("utf-8")
            self.assertEqual((text.count(MESSAGE_START), text.count(MESSAGE_END)), (1, 1), relative)
            copies[relative] = text[text.index(MESSAGE_START):text.index(MESSAGE_END) + len(MESSAGE_END)]
        for relative, copy in copies.items():
            with self.subTest(tool=relative):
                self.assertEqual(copy, copies[TOOLS[0]])
        namespace = {"os": os, "re": re}
        exec(compile(copies[TOOLS[0]], "message-helper", "exec"), namespace)
        plain_text = namespace["_plain_text"]
        self.assertEqual(plain_text("no path here"), "no path here")
        if WINDOWS:
            self.assertEqual(plain_text("copy " + PREFIX + "C:\\a failed"), "copy C:\\a failed")
            self.assertEqual(plain_text(repr(PREFIX + "C:\\a")), repr("C:\\a"))
            self.assertEqual(plain_text(PREFIX + "UNC\\server\\share"), "\\\\server\\share")
            # a path with no plain form keeps its spelling
            volume = PREFIX + "Volume{0b1c}\\x"
            self.assertEqual(plain_text("copy " + volume), "copy " + volume)
            self.assertEqual(plain_text(repr(volume)), repr(volume))

    def test_the_block_changes_nothing_on_short_paths_and_other_systems(self) -> None:
        namespace = {"os": os, "re": re, "Path": Path}
        exec(compile(block_of(TOOLS[0]), "long-path-helpers", "exec"), namespace)
        extended, plain, workspace = namespace["_extended"], namespace["_plain"], namespace["_workspace"]
        limit = namespace["PLAIN_PATH_LIMIT"]
        self.assertEqual(limit, 240)
        relative = Path("notes") / "a.md"
        self.assertEqual(str(extended(relative)), str(relative))
        self.assertEqual(str(plain(relative)), str(relative))
        root = Path("C:\\t") if WINDOWS else Path("/t")
        short = root / "work"
        room = limit - len(str(short))
        self.assertEqual(str(workspace(short, room - 1)), str(short))
        if WINDOWS:
            self.assertEqual(str(workspace(short, room)), PREFIX + str(short))
            self.assertEqual(str(extended("C:\\a\\b")), PREFIX + "C:\\a\\b")
            self.assertEqual(str(extended("\\\\server\\share\\b")), PREFIX + "UNC\\server\\share\\b")
            self.assertEqual(str(extended(PREFIX + "C:\\a\\b")), PREFIX + "C:\\a\\b")
            self.assertEqual(str(plain(PREFIX + "C:\\a\\b")), "C:\\a\\b")
            self.assertEqual(str(plain(PREFIX + "UNC\\server\\share\\b")), "\\\\server\\share\\b")
            for other in (PREFIX + "Volume{0b1c}\\x", PREFIX + "ContainerMappedDirectories\\x"):
                self.assertEqual(str(plain(other)), other)
        else:
            self.assertEqual(workspace(short, room), short)
            self.assertEqual(workspace(short, 10_000), short)
            self.assertEqual(extended(short), short)
            self.assertEqual(plain(short), short)


if __name__ == "__main__":
    unittest.main()
