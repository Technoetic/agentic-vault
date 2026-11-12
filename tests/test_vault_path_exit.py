"""An explicit `--vault` path that is not a folder ends with exit 2.

A mistyped or vanished `--vault` used to end the health check with exit 0 and
"볼트 아님" (or exit 1 in staged mode), and Doctor with exit 0 and `not_vault`,
so a script could not tell a wrong path from a folder that is simply not a
vault. Now a path given on the command line that is missing or a file ends
both with exit 2: the health check names the path on stderr, Doctor reports
`invalid_vault` / `vault_path_not_found`. An existing folder without
`00-meta/vault-config.json`, and a run without `--vault` (the SessionStart
hook), keep exit 0; the new `--require-vault` turns that case into exit 2.

Contract tests (they pass before and after the change): the existing non-vault
folder without the flag, the hook's implicit path, recall's codes and the
abbreviations `--s` and `--st` of `--staged`. Doctor's exact default bytes for
a non-vault folder stay pinned in `tests/test_vault_doctor_host_version.py`.

Most cases call `main()` in this process to keep the module fast; one case
runs the health check as a program. Temporary roots are resolved, so a path
such as `/var/...` on macOS is the same `/private/var/...` the scripts print,
and Git runs with the user's settings cut off (`isolate_git`).
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
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
DOCTOR = SCRIPTS / "vault_doctor.py"
CONFIG = "00-meta/vault-config.json"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
    import vault_recall
finally:
    sys.path.remove(str(SCRIPTS))

CLEAN_CONFIG = {"deny_zones": ["90-assets"], "hot_note": "00-meta/hot.md", "handoff_note": ""}
HOT_NOTE = ("---\ntitle: hot\ntype: reference\nstatus: active\nai_priority: low\n"
            "tags: [hot]\ncreated: 2026-01-02\nupdated: 2026-01-02\n---\nShip it.\n")


def path_message(path: Path) -> str:
    return f"[vault-healthcheck] 오류: --vault 경로가 없거나 폴더가 아님 — {path}\n"


def require_message(path: Path) -> str:
    return (f"[vault-healthcheck] 오류: 볼트 아님 — {path} 에 {CONFIG} 이 없음 "
            "(--require-vault)\n")


def not_vault_message(path: Path) -> str:
    return (f"[vault-healthcheck] 볼트 아님 — {path} 에 {CONFIG} 이 없어 "
            "검사를 건너뜁니다.\n")


def run_healthcheck(*arguments: str) -> tuple[int, str, str]:
    """Call the health check's main() with these arguments; returns (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", [str(HEALTHCHECK), *arguments]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = vault_healthcheck.main()
        except SystemExit as stop:  # argparse usage errors
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def load_doctor():
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("doctor_vault_path_under_test", DOCTOR)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load module: {DOCTOR}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path


class _PathCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        isolate_git(self, ceiling=self.root)
        self.missing = self.root / "missing"
        self.file = self.root / "file.txt"
        self.file.write_text("not a folder\n", encoding="utf-8")
        self.plain = self.root / "plain"
        self.plain.mkdir()

    def make_vault(self, name: str) -> Path:
        vault = self.root / name
        (vault / "00-meta").mkdir(parents=True)
        (vault / CONFIG).write_text(json.dumps(CLEAN_CONFIG), encoding="utf-8")
        (vault / "00-meta" / "hot.md").write_text(HOT_NOTE, encoding="utf-8")
        return vault


class HealthcheckPathTests(_PathCase):
    def test_explicit_path_that_is_not_a_folder_exits_2(self) -> None:
        cases = (
            ("missing", (), self.missing),
            ("file", (), self.file),
            ("staged missing", ("--staged",), self.missing),
            ("staged file", ("--staged",), self.file),
        )
        for label, options, path in cases:
            with self.subTest(label):
                code, stdout, stderr = run_healthcheck("--vault", str(path), *options)
                self.assertEqual(code, 2, stderr)
                self.assertEqual(stderr, path_message(path.resolve()))
                self.assertEqual(stdout, "")

    def test_relative_explicit_path_is_named_resolved(self) -> None:
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            code, stdout, stderr = run_healthcheck("--vault", "missing")
        finally:
            os.chdir(previous)
        self.assertEqual(code, 2, stderr)
        self.assertEqual(stderr, path_message(self.missing))
        self.assertEqual(stdout, "")

    def test_program_exit_status_for_a_missing_path(self) -> None:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(HEALTHCHECK), "--vault", str(self.missing)],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=60,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.replace("\r\n", "\n"), path_message(self.missing))

    def test_require_vault_turns_a_folder_without_configuration_into_exit_2(self) -> None:
        code, stdout, stderr = run_healthcheck("--vault", str(self.plain), "--require-vault")
        self.assertEqual(code, 2, stderr)
        self.assertEqual(stderr, require_message(self.plain))
        self.assertEqual(stdout, "")

    def test_require_vault_has_no_effect_in_staged_mode(self) -> None:
        def refuse(_vault: Path) -> dict:
            raise vault_healthcheck.HealthcheckError("staged probe")

        with mock.patch.object(vault_healthcheck, "load_staged_config", refuse):
            code, stdout, stderr = run_healthcheck(
                "--vault", str(self.plain), "--staged", "--require-vault")
        self.assertEqual(code, 1, stderr)
        self.assertEqual(stderr, "[vault-healthcheck] staged 오류: staged probe\n")
        self.assertEqual(stdout, "")

    def test_require_vault_keeps_the_result_of_a_valid_vault(self) -> None:
        results = []
        for name, options in (("plain-run", ()), ("required-run", ("--require-vault",))):
            vault = self.make_vault(name)
            results.append(subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(HEALTHCHECK), "--vault", str(vault),
                 *options],
                cwd=vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", check=False, timeout=60,
            ))
        plain, required = results
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        self.assertEqual(required.returncode, plain.returncode, required.stdout + required.stderr)
        self.assertEqual(required.stdout.replace("required-run", "plain-run"), plain.stdout)
        self.assertEqual(required.stderr, plain.stderr)


class HealthcheckContractTests(_PathCase):
    """These pass before and after the change."""

    def test_explicit_folder_without_configuration_still_passes_quietly(self) -> None:
        code, stdout, stderr = run_healthcheck("--vault", str(self.plain))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, not_vault_message(self.plain))
        self.assertEqual(stdout, "")

    def test_hook_path_from_the_environment_still_passes_quietly(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.missing)}):
            code, stdout, stderr = run_healthcheck()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, not_vault_message(self.missing))
        self.assertEqual(stdout, "")


class DoctorPathTests(_PathCase):
    def setUp(self) -> None:
        super().setUp()
        self.doctor = load_doctor()

    def run_doctor(self, path: Path, *arguments: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(self.doctor, "_utf8_streams"), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                code = self.doctor.main(["--vault", str(path), *arguments])
            except SystemExit as stop:  # argparse usage errors
                code = stop.code
        return code, out.getvalue()

    def test_path_that_is_not_a_folder_is_an_invalid_vault(self) -> None:
        for label, path in (("missing", self.missing), ("file", self.file)):
            with self.subTest(label):
                code, stdout = self.run_doctor(path, "--format", "json")
                self.assertEqual(code, 2)
                report = json.loads(stdout)
                self.assertEqual(report["status"], "invalid_vault")
                self.assertEqual(report["reason_code"], "vault_path_not_found")
                self.assertEqual(report["next_action"], "pass_an_existing_vault_directory")
                self.assertEqual(report["sections"], {})
                self.assertFalse(report["hook"]["would_emit_context"])

                code, text = self.run_doctor(path)
                self.assertEqual(code, 2)
                self.assertEqual(text.splitlines()[:2], [
                    "agentic-vault doctor: invalid_vault (vault_path_not_found)",
                    "next_action: pass_an_existing_vault_directory",
                ])

    def test_require_vault_changes_only_the_exit_code(self) -> None:
        for arguments in (("--format", "json"), ()):
            with self.subTest(arguments=arguments):
                default_code, default_out = self.run_doctor(self.plain, *arguments)
                code, stdout = self.run_doctor(self.plain, *arguments, "--require-vault")
                self.assertEqual(default_code, 0)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, default_out)
                if arguments:
                    self.assertEqual(json.loads(stdout)["status"], "not_vault")

    def test_require_vault_keeps_the_code_of_a_vault(self) -> None:
        vault = self.make_vault("vault")
        default_code, default_out = self.run_doctor(vault, "--format", "json")
        code, stdout = self.run_doctor(vault, "--format", "json", "--require-vault")
        self.assertEqual(default_code, 0)
        self.assertEqual(code, default_code)
        self.assertEqual(stdout, default_out)


class RecallContractTests(_PathCase):
    """Recall already returned exit 2 for both cases; this pins it."""

    def run_recall(self, path: Path) -> tuple[int, dict]:
        saved_path = list(sys.path)
        sys.path.insert(0, str(SCRIPTS))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = vault_recall.main(
                    ["--vault", str(path), "--query", "anything", "--format", "json"])
        finally:
            sys.path[:] = saved_path
        return code, json.loads(out.getvalue())

    def test_recall_codes(self) -> None:
        for path, status in ((self.missing, "invalid_vault"), (self.file, "invalid_vault"),
                             (self.plain, "not_vault")):
            with self.subTest(path=path.name):
                code, result = self.run_recall(path)
                self.assertEqual(code, 2)
                self.assertEqual(result["diagnostics"]["status"], status)


class ArgparseAbbreviationTests(_PathCase):
    def test_argparse_abbreviations(self) -> None:
        def refuse(_vault: Path) -> dict:
            raise vault_healthcheck.HealthcheckError("staged probe")

        # Contract: `--s` and `--st` still select `--staged` (no new option starts with `--s`).
        for prefix in ("--s", "--st"):
            with self.subTest(prefix=prefix), \
                    mock.patch.object(vault_healthcheck, "load_staged_config", refuse):
                code, _stdout, stderr = run_healthcheck("--vault", str(self.plain), prefix)
                self.assertEqual(code, 1, stderr)
                self.assertEqual(stderr, "[vault-healthcheck] staged 오류: staged probe\n")

        # Prefixes that were usage errors now select `--require-vault`.
        doctor = load_doctor()
        for prefix in ("--r", "--re"):
            with self.subTest(tool="healthcheck", prefix=prefix):
                code, _stdout, stderr = run_healthcheck("--vault", str(self.plain), prefix)
                self.assertEqual(code, 2, stderr)
                self.assertEqual(stderr, require_message(self.plain))
            with self.subTest(tool="doctor", prefix=prefix):
                out = io.StringIO()
                with mock.patch.object(doctor, "_utf8_streams"), contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    try:
                        code = doctor.main(["--vault", str(self.plain), "--format", "json", prefix])
                    except SystemExit as stop:
                        code = stop.code
                self.assertEqual(code, 2)
                self.assertEqual(out.getvalue()[:1], "{", "usage error instead of a report")
                self.assertEqual(json.loads(out.getvalue())["status"], "not_vault")


if __name__ == "__main__":
    unittest.main()
