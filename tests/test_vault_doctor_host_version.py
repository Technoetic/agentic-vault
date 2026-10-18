"""Opt-in Claude Code host version check of the doctor (--host-version).

The default doctor output is pinned to the bytes it printed before the option
existed, and the probe itself runs only with mocked subprocess calls here,
except one real run of this Python interpreter that exercises the plumbing.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCTOR = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_doctor.py"
BRIDGE = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "jarvis_bridge.py"
FAKE_CLAUDE = os.path.abspath(
    os.path.join(os.sep, "agentic-vault-fake-bin", "claude.exe" if os.name == "nt" else "claude")
)

# Captured from the doctor before --host-version existed (v0.24.0, d2b9315).
NOT_VAULT_TEXT = (
    "agentic-vault doctor: not_vault (config_not_found)\n"
    "next_action: run_with_a_vault_containing_00_meta_vault_config_json\n"
    "hook_would_emit_context: false\n"
    "configured_token_budget: None\n"
    "effective_token_budget: None\n"
    "estimated_emitted_tokens: 0\n"
    "estimated_emitted_chars: 0\n"
    "host_char_cap: 9500\n"
    "trust_boundary: diagnosis_only_host_hook_execution_unverified\n"
)
READY_TEXT = (
    "agentic-vault doctor: ready (context_ready)\n"
    "next_action: none\n"
    "hook_would_emit_context: true\n"
    "configured_token_budget: 4100\n"
    "effective_token_budget: 100\n"
    "estimated_emitted_tokens: 13\n"
    "estimated_emitted_chars: 46\n"
    "host_char_cap: 9500\n"
    "trust_boundary: diagnosis_only_host_hook_execution_unverified\n"
    "handoff: disabled (path_disabled); would_emit=false; configured_budget=4000; effective_budget=0\n"
    "hot: ready (section_ready); would_emit=true; configured_budget=100; effective_budget=100\n"
)
NOT_VAULT_REPORT = {
    "hook": {
        "configured_token_budget": None,
        "effective_token_budget": None,
        "estimated_emitted_chars": 0,
        "estimated_emitted_tokens": 0,
        "host_char_cap": 9500,
        "trust_boundary": "diagnosis_only_host_hook_execution_unverified",
        "would_emit_context": False,
    },
    "next_action": "run_with_a_vault_containing_00_meta_vault_config_json",
    "reason_code": "config_not_found",
    "schema_version": 1,
    "sections": {},
    "status": "not_vault",
}
READY_REPORT = {
    "hook": {
        "configured_token_budget": 4100,
        "effective_token_budget": 100,
        "estimated_emitted_chars": 46,
        "estimated_emitted_tokens": 13,
        "host_char_cap": 9500,
        "trust_boundary": "diagnosis_only_host_hook_execution_unverified",
        "would_emit_context": True,
    },
    "next_action": "none",
    "reason_code": "context_ready",
    "schema_version": 1,
    "sections": {
        "handoff": {
            "configured_token_budget": 4000,
            "effective_token_budget": 0,
            "estimated_emitted_chars": 0,
            "estimated_emitted_tokens": 0,
            "independently_estimated_tokens": 0,
            "independently_would_emit": False,
            "next_action": "configure_a_note_if_context_is_expected",
            "reason_code": "path_disabled",
            "status": "disabled",
            "would_emit": False,
        },
        "hot": {
            "configured_token_budget": 100,
            "effective_token_budget": 100,
            "estimated_emitted_chars": 45,
            "estimated_emitted_tokens": 13,
            "independently_estimated_tokens": 13,
            "independently_would_emit": True,
            "next_action": "none",
            "reason_code": "section_ready",
            "status": "ready",
            "would_emit": True,
        },
    },
    "status": "ready",
}


def load_module(name: str, path: Path):
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path


def load_doctor():
    return load_module("doctor_host_version_under_test", DOCTOR)


def platform_bytes(text: str) -> bytes:
    """What print() writes for text: LF becomes CRLF on Windows."""
    return text.replace("\n", os.linesep).encode("utf-8")


class FakeProcess:
    """Stands in for subprocess.Popen; records communicate timeouts and kill."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0, *,
                 hang: bool = False, hang_after_kill: bool = False) -> None:
        self._stdout = stdout
        self._final_returncode = returncode
        self.returncode: int | None = None
        self.hang = hang
        self.hang_after_kill = hang_after_kill
        self.killed = False
        self.timeouts: list[float | None] = []

    def communicate(self, input=None, timeout=None):  # noqa: A002 - Popen signature
        self.timeouts.append(timeout)
        if self.hang and (not self.killed or self.hang_after_kill):
            raise subprocess.TimeoutExpired(["claude", "--version"], timeout)
        self.returncode = -9 if self.killed else self._final_returncode
        return (b"" if self.killed else self._stdout), None

    def kill(self) -> None:
        self.killed = True


class HostVersionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doctor = load_doctor()

    def probe(self, process: FakeProcess | None = None, *, popen_error: BaseException | None = None,
              found: str | None = FAKE_CLAUDE) -> tuple[dict, mock.MagicMock]:
        """Run the probe with shutil.which and subprocess.Popen mocked."""
        doctor = self.doctor
        popen = mock.MagicMock(name="Popen")
        if popen_error is not None:
            popen.side_effect = popen_error
        else:
            popen.return_value = process if process is not None else FakeProcess()
        with mock.patch.object(doctor.shutil, "which", return_value=found), \
                mock.patch.object(doctor.subprocess, "Popen", popen):
            report = doctor.diagnose_host_version()
        return report, popen

    def run_main(self, vault: Path, *arguments: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with mock.patch.object(self.doctor, "_utf8_streams"), contextlib.redirect_stdout(buffer):
            code = self.doctor.main(["--vault", str(vault), *arguments])
        return code, buffer.getvalue()


class HostVersionProbeTests(HostVersionTestCase):
    def assert_report(self, report: dict, status: str, reason_code: str, version: str | None) -> None:
        self.assertEqual(report["host"], "claude-code")
        self.assertEqual(report["status"], status)
        self.assertEqual(report["reason_code"], reason_code)
        self.assertEqual(report["version"], version)
        self.assertEqual(report["recommended_min"], "2.1.295")
        self.assertEqual(
            report["trust_boundary"], "path_executable_version_only_running_host_unverified"
        )
        self.assertEqual(
            set(report),
            {"host", "status", "reason_code", "version", "recommended_min",
             "next_action", "trust_boundary"},
        )

    def test_versions_at_or_above_the_minimum_are_ok(self) -> None:
        cases = (
            (b"2.1.295 (Claude Code)\n", "2.1.295"),  # equal
            (b"2.1.296 (Claude Code)\n", "2.1.296"),  # above
            (b"2.1.1000 (Claude Code)\n", "2.1.1000"),  # numeric, not string, order
            (b"2.2.0\r\n", "2.2.0"),
            (b"3.0.0 (Claude Code)", "3.0.0"),
        )
        for stdout, version in cases:
            with self.subTest(stdout=stdout):
                report, _ = self.probe(FakeProcess(stdout))
                self.assert_report(report, "ok", "version_at_or_above_recommended_min", version)
                self.assertEqual(report["next_action"], "none")

    def test_versions_below_the_minimum_are_reported(self) -> None:
        cases = (
            (b"2.1.294 (Claude Code)\n", "2.1.294"),
            (b"2.1.252 (Claude Code)\n", "2.1.252"),
            (b"2.0.1000\n", "2.0.1000"),
            (b"1.99.999\n", "1.99.999"),
        )
        for stdout, version in cases:
            with self.subTest(stdout=stdout):
                report, _ = self.probe(FakeProcess(stdout))
                self.assert_report(
                    report, "below_recommended", "version_below_recommended_min", version
                )
                self.assertEqual(report["next_action"], "update_claude_code")

    def test_missing_executable_is_unavailable_without_launching(self) -> None:
        report, popen = self.probe(found=None)

        self.assert_report(report, "unavailable", "executable_not_found", None)
        self.assertEqual(report["next_action"], "check_claude_version_manually")
        popen.assert_not_called()

    def test_launch_errors_are_unavailable(self) -> None:
        for error in (FileNotFoundError("claude"), PermissionError("denied"), OSError("bad exe")):
            with self.subTest(error=type(error).__name__):
                report, _ = self.probe(popen_error=error)
                self.assert_report(report, "unavailable", "launch_failed", None)

    def test_garbled_output_is_unparsed(self) -> None:
        cases = (
            b"",
            b"\n\n",
            b"\xff\xfe\x00\x81garbage",
            b"Claude Code\n",
            b"version 2.1.300\n",
            b"2.1.300-beta.1 (Claude Code)\n",
            b"Update available\n2.1.300 (Claude Code)\n",
            b"2.1\n",
            b"\x1b[31m2.1.300\x1b[0m\n",
            b"\xd9\xa2.\xd9\xa1.\xd9\xa3\n",  # Arabic-Indic digits are not a version
            b"9" * 100_000,
        )
        for stdout in cases:
            with self.subTest(stdout=stdout[:40]):
                report, _ = self.probe(FakeProcess(stdout))
                self.assert_report(report, "unparsed", "version_unparsed", None)
                self.assertEqual(report["next_action"], "check_claude_version_manually")

    def test_nonzero_exit_is_unavailable_even_with_a_version(self) -> None:
        report, _ = self.probe(FakeProcess(b"2.1.300 (Claude Code)\n", returncode=1))

        self.assert_report(report, "unavailable", "nonzero_exit", None)

    def test_timeout_kills_and_reaps_within_five_seconds(self) -> None:
        for hang_after_kill in (False, True):
            with self.subTest(hang_after_kill=hang_after_kill):
                process = FakeProcess(b"2.1.300\n", hang=True, hang_after_kill=hang_after_kill)
                report, _ = self.probe(process)

                self.assert_report(report, "unavailable", "timeout", None)
                self.assertTrue(process.killed)
                # One bounded wait for the command, one bounded reap after the kill.
                self.assertEqual(len(process.timeouts), 2)
                self.assertNotIn(None, process.timeouts)
                self.assertLessEqual(sum(process.timeouts), 5.0)

    def test_command_is_a_fixed_argv_without_a_shell(self) -> None:
        process = FakeProcess(b"2.1.296 (Claude Code)\n")
        _, popen = self.probe(process)

        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args, ([FAKE_CLAUDE, "--version"],))
        self.assertFalse(kwargs.get("shell", False))
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(len(process.timeouts), 1)
        self.assertGreater(process.timeouts[0], 0)
        self.assertLessEqual(process.timeouts[0], 5.0)

    def test_unexpected_errors_are_contained(self) -> None:
        report, _ = self.probe(popen_error=RuntimeError("unexpected"))

        self.assert_report(report, "unavailable", "probe_failed", None)

    def test_windows_runs_only_a_native_exe(self) -> None:
        base = os.path.dirname(FAKE_CLAUDE)
        cases = (
            ("claude.cmd", False),
            ("claude.bat", False),
            ("claude.ps1", False),
            ("claude", False),
            ("claude.EXE", True),  # Windows PATHEXT spelling returned by shutil.which
            ("claude.exe", True),
        )
        for name, runs in cases:
            with self.subTest(name=name):
                with mock.patch.object(self.doctor, "_is_windows", return_value=True):
                    report, popen = self.probe(
                        FakeProcess(b"2.1.296 (Claude Code)\n"), found=os.path.join(base, name)
                    )
                if runs:
                    self.assertEqual(report["status"], "ok")
                    popen.assert_called_once()
                else:
                    self.assert_report(report, "unavailable", "windows_non_exe_launcher_not_run", None)
                    popen.assert_not_called()

    def test_windows_probe_never_opens_a_console_window(self) -> None:
        # Patched constant: the flag reaches Popen on every CI platform, not only Windows.
        create_no_window = 0x08000000
        native = os.path.join(os.path.dirname(FAKE_CLAUDE), "claude.exe")
        for windows, expected in ((True, create_no_window), (False, 0)):
            with self.subTest(windows=windows):
                with mock.patch.object(self.doctor, "_is_windows", return_value=windows), \
                        mock.patch.object(self.doctor.subprocess, "CREATE_NO_WINDOW",
                                          create_no_window, create=True):
                    report, popen = self.probe(FakeProcess(b"2.1.296 (Claude Code)\n"), found=native)

                self.assertEqual(report["status"], "ok")
                popen.assert_called_once()
                args, kwargs = popen.call_args
                self.assertEqual(args, ([native, "--version"],))
                self.assertEqual(kwargs["creationflags"], expected)
                self.assertFalse(kwargs.get("shell", False))

    def test_current_directory_executable_runs_only_from_an_explicit_path_entry(self) -> None:
        current = os.getcwd()
        name = "claude.exe" if os.name == "nt" else "claude"
        in_cwd = os.path.join(current, name)
        elsewhere = os.path.dirname(FAKE_CLAUDE)
        with mock.patch.dict(os.environ, {"PATH": elsewhere}):
            report, popen = self.probe(FakeProcess(b"2.1.296\n"), found=in_cwd)
        self.assert_report(report, "unavailable", "current_directory_executable_refused", None)
        popen.assert_not_called()

        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([elsewhere, current])}):
            report, popen = self.probe(FakeProcess(b"2.1.296\n"), found=in_cwd)
        self.assertEqual(report["status"], "ok")
        popen.assert_called_once()

    def test_real_process_plumbing_with_this_interpreter(self) -> None:
        # `python --version` prints "Python X.Y.Z": a real run that must be unparsed.
        with mock.patch.object(self.doctor.shutil, "which", return_value=sys.executable):
            report = self.doctor.diagnose_host_version()

        self.assertEqual((report["status"], report["reason_code"]), ("unparsed", "version_unparsed"))
        self.assertIsNone(report["version"])


class CurrentDirectoryRuleEquivalenceTests(unittest.TestCase):
    """The doctor copies the Jarvis bridge's current-directory rule; both must agree."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doctor = load_doctor()
        # Import only: the bridge defines functions and constants and starts nothing.
        cls.bridge = load_module("jarvis_bridge_current_directory_rule", BRIDGE)

    def test_doctor_and_bridge_agree_on_the_same_inputs(self) -> None:
        current = os.getcwd()
        name = "claude.exe" if os.name == "nt" else "claude"
        elsewhere = os.path.dirname(FAKE_CLAUDE)
        executables = (
            os.path.join(current, name),
            os.path.join(current, "sub", name),
            os.path.join(elsewhere, name),
            os.path.join(current.upper(), name),
            os.path.join(current.lower(), name),
            os.path.join(os.curdir, name),
            name,
        )
        path_values = (
            elsewhere,
            "",
            os.pathsep.join([elsewhere, current]),
            os.pathsep.join([elsewhere, current + os.sep]),
            os.pathsep.join([elsewhere, ""]),
            os.pathsep.join([elsewhere, os.curdir]),
            os.pathsep.join([current.upper(), elsewhere]),
            os.pathsep.join([os.path.join(current, "sub"), elsewhere]),
        )
        outcomes = set()
        for executable in executables:
            for path_value in path_values:
                with self.subTest(executable=executable, path=path_value), \
                        mock.patch.dict(os.environ, {"PATH": path_value}):
                    doctor_result = self.doctor._found_only_in_current_directory(executable)
                    bridge_result = self.bridge._found_only_in_current_directory(executable)
                    self.assertEqual(doctor_result, bridge_result)
                    outcomes.add(doctor_result)
        # Both outcomes occur, so the comparison is not vacuous.
        self.assertEqual(outcomes, {True, False})
        in_current = os.path.join(current, name)
        with mock.patch.dict(os.environ, {"PATH": elsewhere}):
            self.assertTrue(self.doctor._found_only_in_current_directory(in_current))
            self.assertFalse(self.doctor._found_only_in_current_directory(os.path.join(elsewhere, name)))
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([elsewhere, current])}):
            self.assertFalse(self.doctor._found_only_in_current_directory(in_current))


class HostVersionCliTests(HostVersionTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def make_vault(self, name: str, hot_max_tokens: int = 100, note: str = "Ship the verified change.",
                   raw_config: str | None = None) -> Path:
        vault = self.root / name
        (vault / "00-meta").mkdir(parents=True)
        config = raw_config if raw_config is not None else json.dumps({
            "handoff_note": "",
            "hot_note": "00-meta/hot.md",
            "deny_zones": ["90-assets"],
            "hot_max_tokens": hot_max_tokens,
        })
        (vault / "00-meta" / "vault-config.json").write_text(config, encoding="utf-8")
        (vault / "00-meta" / "hot.md").write_text(note, encoding="utf-8")
        return vault

    def run_doctor(self, vault: Path, *arguments: str,
                   env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(DOCTOR), "--vault", str(vault), *arguments],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )

    def test_default_output_bytes_match_the_pre_option_doctor(self) -> None:
        not_vault = self.root / "not-vault"
        not_vault.mkdir()
        ready = self.make_vault("ready")
        cases = (
            (not_vault, (), NOT_VAULT_TEXT),
            (not_vault, ("--format", "json"),
             json.dumps(NOT_VAULT_REPORT, ensure_ascii=False, sort_keys=True) + "\n"),
            (ready, (), READY_TEXT),
            (ready, ("--format", "json"),
             json.dumps(READY_REPORT, ensure_ascii=False, sort_keys=True) + "\n"),
        )
        for vault, arguments, expected in cases:
            with self.subTest(vault=vault.name, arguments=arguments):
                result = self.run_doctor(vault, *arguments)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, platform_bytes(expected))
                self.assertEqual(result.stderr, b"")

    def test_default_run_never_looks_up_or_starts_the_host(self) -> None:
        ready = self.make_vault("ready")
        refuse = mock.MagicMock(side_effect=AssertionError("host probed without --host-version"))
        with mock.patch.object(self.doctor.shutil, "which", refuse), \
                mock.patch.object(self.doctor.subprocess, "Popen", refuse):
            text_code, text = self.run_main(ready)
            json_code, raw_json = self.run_main(ready, "--format", "json")

        refuse.assert_not_called()
        self.assertEqual((text_code, text), (0, READY_TEXT))
        self.assertEqual(json_code, 0)
        self.assertEqual(json.loads(raw_json), READY_REPORT)
        self.assertNotIn("host_version", raw_json)

    def test_option_only_adds_the_host_block(self) -> None:
        ready = self.make_vault("ready")
        with mock.patch.object(self.doctor.shutil, "which", return_value=FAKE_CLAUDE), \
                mock.patch.object(self.doctor.subprocess, "Popen",
                                  return_value=FakeProcess(b"2.1.252 (Claude Code)\n")):
            json_code, raw_json = self.run_main(ready, "--format", "json", "--host-version")
            text_code, text = self.run_main(ready, "--host-version")

        report = json.loads(raw_json)
        host = report.pop("host_version")
        self.assertEqual(report, READY_REPORT)
        self.assertEqual(host["status"], "below_recommended")
        self.assertEqual(host["version"], "2.1.252")
        self.assertEqual((json_code, text_code), (0, 0))
        self.assertTrue(text.startswith(READY_TEXT))
        self.assertEqual(
            text[len(READY_TEXT):].splitlines(),
            [
                "host_version: below_recommended (version_below_recommended_min)",
                "host: claude-code",
                "host_detected_version: 2.1.252",
                "host_recommended_min: 2.1.295",
                "host_next_action: update_claude_code",
                "host_trust_boundary: path_executable_version_only_running_host_unverified",
            ],
        )

    def test_option_never_changes_the_exit_code(self) -> None:
        not_vault = self.root / "not-vault"
        not_vault.mkdir()
        vaults = (
            (not_vault, 0),
            (self.make_vault("ready"), 0),
            (self.make_vault("degraded", hot_max_tokens=1), 1),
            (self.make_vault("invalid", raw_config='{"broken"'), 2),
        )
        doctor = self.doctor
        blocks = (
            doctor._host_report("ok", "version_at_or_above_recommended_min", "2.1.296"),
            doctor._host_report("below_recommended", "version_below_recommended_min", "2.1.252"),
            doctor._host_report("unavailable", "timeout"),
            doctor._host_report("unparsed", "version_unparsed"),
        )
        for vault, expected_code in vaults:
            default_code, _ = self.run_main(vault, "--format", "json")
            self.assertEqual(default_code, expected_code)
            for block in blocks:
                with self.subTest(vault=vault.name, status=block["status"]):
                    with mock.patch.object(doctor, "diagnose_host_version", return_value=block):
                        code, raw_json = self.run_main(vault, "--format", "json", "--host-version")
                    self.assertEqual(code, expected_code)
                    self.assertEqual(json.loads(raw_json)["host_version"], block)

    def test_cli_reports_a_missing_host_and_keeps_the_exit_code(self) -> None:
        empty_path = self.root / "empty-path"
        empty_path.mkdir()
        not_vault = self.root / "not-vault"
        not_vault.mkdir()

        result = self.run_doctor(
            not_vault, "--format", "json", "--host-version",
            env_overrides={"PATH": str(empty_path)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["host_version"]["status"], "unavailable")
        self.assertEqual(report["host_version"]["reason_code"], "executable_not_found")
        report.pop("host_version")
        self.assertEqual(report, NOT_VAULT_REPORT)


if __name__ == "__main__":
    unittest.main()
