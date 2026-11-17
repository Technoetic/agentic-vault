"""Tests for ``scripts/long_temp_suite.py``.

The tool runs the suite under a temporary folder of an exact path length and
refuses to run when ``tempfile`` would use another folder. The cases here use
short lengths relative to the current temporary folder, so they do not depend
on how long that folder is.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import extended
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import extended

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "long_temp_suite.py"
SPEC = importlib.util.spec_from_file_location("long_temp_suite_under_test", SCRIPT)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

INNER_TEST = '''\
import os
import tempfile
import unittest


class Inner(unittest.TestCase):
    def test_environment(self):
        folder = os.environ["TEMP"]
        self.assertEqual(os.environ["TMP"], folder)
        self.assertEqual(os.environ["TMPDIR"], folder)
        self.assertEqual(os.environ["AGENTIC_VAULT_REQUIRE_LONG_TEMP"], str(len(folder)))
        self.assertEqual(os.path.normcase(os.path.abspath(tempfile.gettempdir())),
                         os.path.normcase(os.path.abspath(folder)))
        with open(os.path.join(os.environ["INNER_MARK"], "ran"), "w", encoding="utf-8") as handle:
            handle.write(folder)
'''


class FolderPathTests(unittest.TestCase):
    def test_the_folder_path_has_exactly_the_requested_length(self) -> None:
        parent = Path(tempfile.gettempdir()).resolve()
        for extra in (30, 41, 97):
            length = len(str(parent)) + extra
            with self.subTest(extra=extra):
                folder = tool.folder_path(parent, length, "0a1b2c3d")
                self.assertEqual(len(str(folder)), length)
                self.assertEqual(folder.parent, parent)
                self.assertTrue(folder.name.startswith("av-long-temp-0a1b2c3d-"))

    def test_the_length_is_exact_below_a_root_folder_as_well(self) -> None:
        # A drive or file-system root already ends with its separator; nothing is created here.
        root = Path(os.path.abspath(os.sep))
        for length in (60, 150):
            with self.subTest(length=length):
                folder = tool.folder_path(root, length, "0a1b2c3d")
                self.assertEqual(len(str(folder)), length)
                self.assertEqual(folder.parent, root)

    def test_a_length_the_parent_cannot_give_is_a_usage_error(self) -> None:
        parent = Path(tempfile.gettempdir()).resolve()
        with self.assertRaises(tool.UsageError):
            tool.folder_path(parent, len(str(parent)) + 5, "0a1b2c3d")
        with self.assertRaises(tool.UsageError):
            tool.folder_path(parent, len(str(parent)) + 1 + 256, "0a1b2c3d")


class ProbeTests(unittest.TestCase):
    def test_a_usable_folder_is_reported_and_an_unusable_one_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            usable = Path(temporary).resolve() / "usable"
            usable.mkdir()
            self.assertTrue(tool.tempfile_uses(usable, tool.child_environment(usable, len(str(usable)))))
            missing = Path(temporary).resolve() / "missing"
            self.assertFalse(tool.tempfile_uses(missing, tool.child_environment(missing, len(str(missing)))))

    def test_the_child_environment_sets_the_three_variables_and_the_guard(self) -> None:
        folder = Path(tempfile.gettempdir()).resolve() / "anything"
        environment = tool.child_environment(folder, 123)
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(environment[name], str(folder))
        self.assertEqual(environment[tool.REQUIRE], "123")
        self.assertEqual(tool.REQUIRE, "AGENTIC_VAULT_REQUIRE_LONG_TEMP")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name).resolve()
        self.parent = self.base / "p"
        self.parent.mkdir()
        self.length = len(str(self.parent)) + 40
        self.inner = self.base / "inner"
        self.inner.mkdir()
        (self.inner / "inner_case.py").write_text(INNER_TEST, encoding="utf-8")
        self.mark = self.base / "mark"
        self.mark.mkdir()
        self.summary = self.base / "out" / "summary.json"

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = dict(os.environ, INNER_MARK=str(self.mark))
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--parent", str(self.parent),
             "--length", str(self.length), *arguments,
             "--", "discover", "-s", str(self.inner), "-p", "inner_case.py"],
            capture_output=True, text=True, encoding="utf-8", env=environment, check=False)

    def test_the_suite_runs_under_the_folder_and_the_folder_is_removed(self) -> None:
        result = self.run_tool("--summary", str(self.summary))
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = (self.mark / "ran").read_text(encoding="utf-8")
        self.assertEqual(len(folder), self.length)
        self.assertEqual(Path(folder).parent, self.parent)
        self.assertFalse(os.path.lexists(extended(folder)))
        summary = json.loads(result.stderr.strip().splitlines()[-1])
        self.assertEqual(summary, json.loads(self.summary.read_text(encoding="utf-8")))
        self.assertEqual(sorted(summary), ["long_paths_enabled", "platform", "python", "returncode",
                                           "temp_length", "tempfile_uses_temp"])
        self.assertEqual((summary["temp_length"], summary["tempfile_uses_temp"], summary["returncode"]),
                         (self.length, True, 0))
        self.assertIn(summary["long_paths_enabled"], (0, 1, None))
        if os.name != "nt":
            self.assertIsNone(summary["long_paths_enabled"])

    def test_keep_leaves_the_folder_and_names_it(self) -> None:
        result = self.run_tool("--keep")
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = (self.mark / "ran").read_text(encoding="utf-8")
        self.assertTrue(os.path.isdir(folder))
        self.assertIn(f"long_temp_suite: kept {folder}", result.stderr)
        self.assertEqual(json.loads(result.stderr.strip().splitlines()[-1])["returncode"], 0)

    def test_a_failing_suite_gives_its_exit_code(self) -> None:
        (self.inner / "inner_case.py").write_text(
            "import unittest\n\n\nclass Inner(unittest.TestCase):\n    def test_fails(self):\n        self.fail('x')\n",
            encoding="utf-8")
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr.strip().splitlines()[-1])["returncode"], 1)

    def test_nothing_runs_when_tempfile_would_use_another_folder(self) -> None:
        calls = []
        errors = io.StringIO()
        with mock.patch.object(tool, "tempfile_uses", return_value=False), \
             mock.patch.object(tool, "run_suite", side_effect=lambda *a, **k: calls.append(a) or 0), \
             contextlib.redirect_stderr(errors):
            code = tool.main(["--parent", str(self.parent), "--length", str(self.length)])
        self.assertEqual(code, 3)
        self.assertEqual(calls, [])
        self.assertIn("nothing was run", errors.getvalue())
        summary = json.loads(errors.getvalue().strip().splitlines()[-1])
        self.assertEqual((summary["tempfile_uses_temp"], summary["returncode"]), (False, 3))
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_an_impossible_length_and_a_missing_parent_are_usage_errors(self) -> None:
        for arguments in (["--parent", str(self.parent), "--length", str(len(str(self.parent)) + 3)],
                          ["--parent", str(self.base / "absent"), "--length", str(self.length)]):
            errors = io.StringIO()
            with self.subTest(arguments=arguments[-1]), mock.patch.object(tool, "run_suite") as run_suite, \
                 contextlib.redirect_stderr(errors):
                self.assertEqual(tool.main(arguments), 2)
                run_suite.assert_not_called()
                self.assertTrue(errors.getvalue().startswith("long_temp_suite: "))
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_an_interrupted_suite_still_reports_its_status(self) -> None:
        status = 0xC000013A  # what Windows reports for a console program ended by Ctrl+C

        class Interrupted:
            waits = 0

            def __init__(self, *args, **kwargs):
                pass

            def wait(self, timeout=None):
                Interrupted.waits += 1
                if Interrupted.waits == 1:
                    raise KeyboardInterrupt
                return status

            def kill(self):
                raise AssertionError("the suite ended by itself and must not be killed")

        errors = io.StringIO()
        with mock.patch.object(tool, "tempfile_uses", return_value=True), \
             mock.patch.object(tool.subprocess, "Popen", Interrupted), contextlib.redirect_stderr(errors):
            code = tool.main(["--parent", str(self.parent), "--length", str(self.length),
                              "--summary", str(self.summary)])
        summary = json.loads(errors.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary["returncode"], status)
        self.assertEqual(json.loads(self.summary.read_text(encoding="utf-8")), summary)
        self.assertEqual(code, status - 2 ** 32 if os.name == "nt" else status)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_a_windows_status_is_passed_on_as_a_status_the_process_can_end_with(self) -> None:
        self.assertEqual(tool.exit_status(0), 0)
        self.assertEqual(tool.exit_status(1), 1)
        self.assertEqual(tool.exit_status(-2), -2)
        big = 0xC0000005
        self.assertEqual(tool.exit_status(big), big - 2 ** 32 if os.name == "nt" else big)

    def test_a_usage_error_leaves_an_existing_summary_file_as_it_was(self) -> None:
        self.summary.parent.mkdir(parents=True)
        self.summary.write_text("previous\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            code = tool.main(["--parent", str(self.parent), "--length", str(len(str(self.parent)) + 3),
                              "--summary", str(self.summary)])
        self.assertEqual(code, 2)
        self.assertEqual(self.summary.read_text(encoding="utf-8"), "previous\n")

    def test_a_summary_that_cannot_be_written_after_a_passing_suite_is_exit_code_2(self) -> None:
        errors = io.StringIO()
        with mock.patch.object(tool, "tempfile_uses", return_value=True), \
             mock.patch.object(tool, "run_suite", return_value=0), \
             mock.patch.object(Path, "write_bytes", side_effect=PermissionError(13, "denied")), \
             contextlib.redirect_stderr(errors):
            code = tool.main(["--parent", str(self.parent), "--length", str(self.length),
                              "--summary", str(self.summary)])
        self.assertEqual(code, 2)
        self.assertIn("cannot write the summary", errors.getvalue())
        self.assertEqual(json.loads(errors.getvalue().strip().splitlines()[-1])["returncode"], 0)

    def test_the_default_arguments_are_the_whole_suite(self) -> None:
        self.assertEqual(tool.DEFAULT_ARGUMENTS, ("discover", "-s", "tests"))
        self.assertEqual(tool.DEFAULT_LENGTH, 150)
        seen = []
        with mock.patch.object(tool, "run_suite", side_effect=lambda arguments, environment: seen.append(
                (list(arguments), environment["TEMP"])) or 0), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["--parent", str(self.parent), "--length", str(self.length)]), 0)
        self.assertEqual(seen[0][0], ["discover", "-s", "tests"])
        self.assertEqual(len(seen[0][1]), self.length)


if __name__ == "__main__":
    unittest.main()
