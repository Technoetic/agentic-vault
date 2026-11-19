"""Tests for the parallel stdlib unittest runner (scripts/run_unittest_parallel.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_unittest_parallel.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_unittest_parallel", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


class ParallelUnittestRunnerTests(unittest.TestCase):
    def test_discovery_matches_all_test_files_in_tests_dir(self) -> None:
        discovered = runner.discover_modules()
        expected = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py") if p.is_file())
        self.assertEqual(discovered, expected)
        self.assertIn("test_000_environment", discovered)
        self.assertIn("test_run_unittest_parallel", discovered)

    def test_list_option_prints_modules_without_running(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--list"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines, runner.discover_modules())

    def test_subset_execution_passes_and_reports_totals(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(RUNNER),
                "--workers",
                "2",
                "--modules",
                "test_000_environment",
                "test_first_handoff",
            ],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[OK] tests.test_000_environment:", proc.stdout)
        self.assertIn("[OK] tests.test_first_handoff:", proc.stdout)
        self.assertIn("across 2 modules", proc.stdout)
        self.assertIn("\nOK", proc.stdout)

    def test_unknown_module_fails_with_usage_code(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--modules", "test_does_not_exist_xyz"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown test module", proc.stderr)

    def test_failing_module_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "tests"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "test_boom.py").write_text(
                "import unittest\n"
                "class Boom(unittest.TestCase):\n"
                "    def test_fail(self):\n"
                "        self.fail('deliberate failure')\n",
                encoding="utf-8",
            )
            res = runner.run_module("test_boom", cwd=Path(tmp), timeout=30)
            self.assertFalse(res["ok"])
            self.assertEqual(res["returncode"], 1)
            self.assertEqual(res["ran"], 1)
            self.assertIn("deliberate failure", str(res["stderr"]))


if __name__ == "__main__":
    unittest.main()
