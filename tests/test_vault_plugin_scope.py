"""Opt-in plugin scope check of the doctor (--plugin-scope, --plugin-scope-host).

Every settings file here is synthetic. Secrets are planted in hook commands, other
plugins' keys and environment values: none may reach the text or JSON output. The
host command runs only with mocked subprocess calls.
"""
from __future__ import annotations

import contextlib
import importlib
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

try:
    from test_vault_doctor_host_version import FAKE_CLAUDE, FakeProcess
except ImportError:  # run as tests.<module> from the repository root
    from tests.test_vault_doctor_host_version import FAKE_CLAUDE, FakeProcess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
DOCTOR = SCRIPTS / "vault_doctor.py"
SECRET = "SECRET-"
PLUGIN_ID = "agentic-vault@agentic-vault"
HOOK_GROUP = {"matcher": "compact", "hooks": [
    {"type": "command", "command": "powershell -File SECRET-HOOK-COMMAND.ps1"},
]}
FINDING = "plugin_and_vault_session_hooks"
SOURCE_ORDER = ["managed", "flag", "local", "project", "user", "add_dir"]


def resolved(executable: str = FAKE_CLAUDE):
    return lambda: (executable, "resolved")


def host_output(*entries: dict) -> bytes:
    return json.dumps(list(entries)).encode("utf-8")


def host_entry(enabled: object = True, plugin_id: str = PLUGIN_ID) -> dict:
    return {"id": plugin_id, "enabled": enabled, "version": "SECRET-VERSION",
            "scope": "SECRET-SCOPE", "installPath": "C:/Users/SECRET-ACCOUNT/plugin"}


def load_doctor():
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("doctor_plugin_scope_under_test", DOCTOR)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load module: {DOCTOR}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path


class PluginScopeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The doctor imports the module by name only when the option is given.
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        cls.scope = importlib.import_module("vault_plugin_scope")

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.user_dir = self.root / "user"
        self.user_dir.mkdir()
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.user_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        path.write_bytes(data)

    def user(self, value: object) -> None:
        self.write(self.user_dir / "settings.json", value)

    def project(self, value: object) -> None:
        self.write(self.vault / ".claude" / "settings.json", value)

    def local(self, value: object) -> None:
        self.write(self.vault / ".claude" / "settings.local.json", value)

    def report(self, **options: object) -> dict:
        report = self.scope.diagnose_plugin_scope(self.vault, **options)
        self.assertNotIn(SECRET, json.dumps(report, ensure_ascii=False))
        self.assertNotIn(SECRET, "\n".join(self.scope.text_lines(report)))
        return report

    def assert_winner(self, report: dict, scope: str, value: str) -> None:
        self.assertEqual(report["file_winner"], {"scope": scope, "value": value})


class ScopeFileTests(PluginScopeTestCase):
    def test_no_settings_leave_every_scope_unset_and_the_others_unknown(self) -> None:
        report = self.report()

        self.assertEqual(list(report["sources"]), SOURCE_ORDER)
        self.assertEqual(report["trust_boundary"], "settings_files_only_other_sources_unknown")
        for scope in ("local", "project", "user"):
            self.assertEqual(report["sources"][scope], {"value": "unset", "file": "missing"})
        for scope in ("managed", "flag", "add_dir"):
            self.assertEqual(report["sources"][scope], {"value": "unknown", "file": "not_read"})
        self.assert_winner(report, "none", "unset")
        self.assertEqual(report["findings"], [])

    def test_a_project_false_wins_over_a_user_true(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"enabledPlugins": {PLUGIN_ID: False}})
        report = self.report()

        self.assertEqual(report["sources"]["user"]["value"], "enabled")
        self.assertEqual(report["sources"]["project"]["value"], "disabled")
        self.assert_winner(report, "project", "disabled")

    def test_removing_the_project_key_brings_the_user_value_back(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"enabledPlugins": {"another@market": False}})
        report = self.report()

        self.assertEqual(report["sources"]["project"], {"value": "unset", "file": "read"})
        self.assert_winner(report, "user", "enabled")

    def test_local_wins_over_project(self) -> None:
        self.project({"enabledPlugins": {PLUGIN_ID: False}})
        self.local({"enabledPlugins": {PLUGIN_ID: True}})

        self.assert_winner(self.report(), "local", "enabled")

    def test_another_marketplace_of_this_plugin_is_read(self) -> None:
        self.user({"enabledPlugins": {"agentic-vault@SECRET-MARKET": True}})

        self.assert_winner(self.report(), "user", "enabled")

    def test_two_ids_that_disagree_are_unknown_and_stop_the_walk(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"enabledPlugins": {PLUGIN_ID: True, "agentic-vault@other": False}})
        report = self.report()

        self.assertEqual(report["sources"]["project"]["value"], "unknown")
        self.assert_winner(report, "project", "unknown")

    def test_other_keys_are_not_read(self) -> None:
        self.user({
            "env": {"SECRET-NAME": "SECRET-VALUE"},
            "enabledPlugins": {
                "SECRET-OTHER@market": True, "agentic-vault-extra@market": True,
                "other@agentic-vault": True, "agentic-vault": True, "AGENTIC-VAULT@market": True,
            },
        })
        report = self.report()

        self.assertEqual(report["sources"]["user"], {"value": "unset", "file": "read"})
        self.assert_winner(report, "none", "unset")

    def test_unusable_values_are_unknown_never_guessed(self) -> None:
        cases = {
            "string_value": {"enabledPlugins": {PLUGIN_ID: "true"}},
            "number_value": {"enabledPlugins": {PLUGIN_ID: 1}},
            "null_value": {"enabledPlugins": {PLUGIN_ID: None}},
            "list_value": {"enabledPlugins": {PLUGIN_ID: ["1.0.0"]}},
            "not_an_object": {"enabledPlugins": [PLUGIN_ID]},
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                self.user({"enabledPlugins": {PLUGIN_ID: False}})
                self.project(document)
                report = self.report()
                self.assertEqual(report["sources"]["project"], {"value": "unknown", "file": "read"})
                self.assert_winner(report, "project", "unknown")

    def test_files_that_cannot_be_read_are_unknown_with_a_closed_state(self) -> None:
        cases = {
            "invalid_json": b"{SECRET-NOT-JSON",
            "invalid_encoding": b'{"SECRET": "\xff\xfe"}',
            "not_an_object": b'["SECRET-LIST"]',
            "too_large": b'{"SECRET": "' + b"x" * (self.scope.MAX_SETTINGS_BYTES + 1) + b'"}',
        }
        for state, data in cases.items():
            with self.subTest(state=state):
                self.project(data)
                report = self.report()
                self.assertEqual(report["sources"]["project"], {"value": "unknown", "file": state})
                self.assert_winner(report, "project", "unknown")
                self.assertIsNone(report["session_hooks"]["project"])

    def test_a_folder_in_the_place_of_a_settings_file_is_unknown(self) -> None:
        (self.vault / ".claude" / "settings.local.json").mkdir(parents=True)
        report = self.report()

        self.assertEqual(report["sources"]["local"], {"value": "unknown", "file": "not_a_regular_file"})

    def test_a_byte_order_mark_is_read(self) -> None:
        self.user(b"\xef\xbb\xbf" + json.dumps({"enabledPlugins": {PLUGIN_ID: True}}).encode("utf-8"))

        self.assert_winner(self.report(), "user", "enabled")

    def test_the_user_scope_follows_the_home_folder_without_the_override(self) -> None:
        self.write(self.root / "home" / ".claude" / "settings.json",
                   {"enabledPlugins": {PLUGIN_ID: True}})
        home = str(self.root / "home")
        environment = {key: value for key, value in os.environ.items()
                       if key != "CLAUDE_CONFIG_DIR"}
        with mock.patch.dict(os.environ, {**environment, "HOME": home, "USERPROFILE": home}, clear=True):
            self.assert_winner(self.report(), "user", "enabled")

    def test_a_link_in_the_vault_is_not_followed(self) -> None:
        outside = self.root / "outside.json"
        self.write(outside, {"enabledPlugins": {PLUGIN_ID: True}})
        target = self.vault / ".claude" / "settings.json"
        target.parent.mkdir()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available")
        report = self.report()

        self.assertEqual(report["sources"]["project"], {"value": "unknown", "file": "unsafe_path"})

    def test_a_denied_settings_file_is_not_read(self) -> None:
        self.write(self.vault / "00-meta" / "vault-config.json", {"deny_zones": [".claude"]})
        self.project({"enabledPlugins": {PLUGIN_ID: True}, "hooks": {"SessionStart": [HOOK_GROUP]}})
        report = self.report()

        self.assertEqual(report["sources"]["project"], {"value": "unknown", "file": "denied"})
        self.assertIsNone(report["session_hooks"]["project"])

    def test_a_denied_healthcheck_copy_is_not_read(self) -> None:
        self.write(self.vault / "00-meta" / "vault-config.json", {"deny_zones": ["00-meta/scripts"]})
        self.write(self.vault / "00-meta" / "scripts" / "vault_healthcheck.py", b"# SECRET-COPY\n")
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "denied")
        self.assertEqual(report["findings"], [])

    def test_a_config_that_cannot_be_read_denies_nothing_it_does_not_name(self) -> None:
        self.write(self.vault / "00-meta" / "vault-config.json", b"{SECRET-NOT-JSON")
        self.project({"enabledPlugins": {PLUGIN_ID: True}})

        self.assert_winner(self.report(), "project", "enabled")


class SessionHookTests(PluginScopeTestCase):
    def test_enabled_plugin_and_a_project_hook_are_reported(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [HOOK_GROUP, HOOK_GROUP]}})
        report = self.report()

        self.assertEqual(report["session_hooks"], {"project": 2, "local": 0})
        self.assertEqual(report["findings"], [FINDING])
        self.assertEqual((report["status"], report["reason_code"]), ("attention", FINDING))
        self.assertEqual(report["next_action"], "keep_one_session_context_hook")

    def test_a_hook_in_the_local_settings_counts_too(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.local({"hooks": {"SessionStart": [HOOK_GROUP]}})
        report = self.report()

        self.assertEqual(report["session_hooks"], {"project": 0, "local": 1})
        self.assertEqual(report["findings"], [FINDING])

    def test_no_finding_without_a_hook_or_without_the_plugin(self) -> None:
        other_event = {"hooks": {"PreToolUse": [HOOK_GROUP]}}
        hook = {"hooks": {"SessionStart": [HOOK_GROUP]}}
        cases = (
            ({"enabledPlugins": {PLUGIN_ID: True}}, other_event),
            ({"enabledPlugins": {PLUGIN_ID: False}}, hook),
            ({}, hook),
        )
        for user, project in cases:
            with self.subTest(user=user, project=project):
                self.user(user)
                self.project(project)
                report = self.report()
                self.assertEqual(report["findings"], [])
                self.assertEqual((report["status"], report["reason_code"], report["next_action"]),
                                 ("ok", "no_findings", "none"))

    def test_a_project_false_with_a_hook_is_the_clean_setup(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"enabledPlugins": {PLUGIN_ID: False}, "hooks": {"SessionStart": [HOOK_GROUP]}})

        self.assertEqual(self.report()["findings"], [])

    def test_handlers_are_counted_without_their_commands(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command"}, {"type": "prompt", "prompt": "SECRET-PROMPT"},
            {"type": "command", "command": ["SECRET-ARGV"]},
        ]}]}})
        report = self.report()

        self.assertEqual(report["session_hooks"]["project"], 3)
        self.assertEqual(report["findings"], [FINDING])

    def test_a_hook_section_of_another_shape_is_not_counted(self) -> None:
        cases = {
            "hooks_not_an_object": {"hooks": ["SECRET"]},
            "event_not_a_list": {"hooks": {"SessionStart": {"hooks": []}}},
            "group_not_an_object": {"hooks": {"SessionStart": ["SECRET"]}},
            "group_without_hooks": {"hooks": {"SessionStart": [{"matcher": "SECRET"}]}},
            "handlers_not_a_list": {"hooks": {"SessionStart": [{"hooks": {"SECRET": 1}}]}},
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                self.user({"enabledPlugins": {PLUGIN_ID: True}})
                self.project(document)
                report = self.report()
                self.assertIsNone(report["session_hooks"]["project"])
                self.assertEqual(report["findings"], [])


class HostCheckTests(PluginScopeTestCase):
    def run_host(self, process: object, resolve=None, **options: object) -> tuple[dict, mock.Mock]:
        popen = mock.Mock(return_value=process) if not isinstance(process, BaseException) \
            else mock.Mock(side_effect=process)
        with mock.patch.object(self.scope.subprocess, "Popen", popen):
            report = self.report(host=True, resolve_host=resolve or resolved(), **options)
        return report, popen

    def test_nothing_runs_unless_asked(self) -> None:
        with mock.patch.object(self.scope.subprocess, "Popen") as popen:
            report = self.report(resolve_host=resolved())

        popen.assert_not_called()
        self.assertEqual(report["host"], {
            "status": "not_requested", "reason_code": "not_requested", "enabled": None,
            "comparison": "not_compared",
        })

    def test_a_fixed_command_runs_in_the_vault_without_a_shell(self) -> None:
        report, popen = self.run_host(FakeProcess(host_output(host_entry())))

        arguments, options = popen.call_args
        self.assertEqual(arguments[0], [FAKE_CLAUDE, "plugin", "list", "--json"])
        self.assertEqual(options["cwd"], str(self.vault))
        self.assertNotIn("shell", options)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertEqual(options["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertEqual(report["host"]["status"], "ok")

    def test_agreement_with_the_files(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        report, _ = self.run_host(FakeProcess(host_output(host_entry())))

        self.assertEqual(report["host"], {
            "status": "ok", "reason_code": "listed", "enabled": "enabled", "comparison": "agrees",
        })
        self.assertEqual(report["findings"], [])

    def test_a_host_value_that_differs_is_reported_and_decides(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        report, _ = self.run_host(FakeProcess(host_output(host_entry(enabled=False))))

        self.assertEqual(report["host"]["comparison"], "differs")
        self.assertEqual(report["findings"], ["host_merged_value_differs"])
        self.assertEqual(report["next_action"], "check_the_sources_that_are_not_read_here")

    def test_a_host_enabled_value_decides_when_the_files_are_silent(self) -> None:
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        report, _ = self.run_host(FakeProcess(host_output(host_entry())))

        self.assertEqual(report["host"]["comparison"], "not_compared")
        self.assertEqual(report["findings"], [FINDING])

    def test_other_plugins_and_their_values_are_not_read(self) -> None:
        output = host_output(host_entry(plugin_id="SECRET-OTHER@market"), host_entry())
        report, _ = self.run_host(FakeProcess(output))

        self.assertEqual(report["host"]["enabled"], "enabled")

    def test_a_plugin_the_host_does_not_list(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        report, _ = self.run_host(FakeProcess(host_output(host_entry(plugin_id="SECRET-OTHER@market"))))

        self.assertEqual(report["host"], {
            "status": "not_listed", "reason_code": "plugin_not_listed", "enabled": None,
            "comparison": "not_compared",
        })
        self.assertEqual(report["findings"], [FINDING])

    def test_an_unusable_enabled_value_is_unknown(self) -> None:
        cases = (
            host_output(host_entry(enabled="true")),
            host_output(host_entry(enabled=True), host_entry(enabled=False)),
        )
        for output in cases:
            with self.subTest(output=output[:40]):
                report, _ = self.run_host(FakeProcess(output))
                self.assertEqual(report["host"]["status"], "ok")
                self.assertEqual(report["host"]["enabled"], "unknown")
                self.assertEqual(report["host"]["comparison"], "not_compared")

    def assert_unavailable(self, report: dict, reason_code: str) -> None:
        self.assertEqual(report["host"], {
            "status": "host_unavailable", "reason_code": reason_code, "enabled": None,
            "comparison": "not_compared",
        })

    def test_a_host_that_cannot_answer_ends_as_host_unavailable(self) -> None:
        cases = {
            "nonzero_exit": FakeProcess(b"SECRET-OUTPUT", returncode=1),
            "output_unparsed": FakeProcess(b"SECRET-NOT-JSON"),
            "launch_failed": OSError("SECRET-OS-ERROR"),
        }
        for reason_code, process in cases.items():
            with self.subTest(reason_code=reason_code):
                self.assert_unavailable(self.run_host(process)[0], reason_code)

    def test_output_of_another_shape_is_unparsed(self) -> None:
        for output in (b'{"installed": []}', b'"SECRET"', b"\xff\xfe", b"[" * 100_000):
            with self.subTest(output=output[:20]):
                self.assert_unavailable(self.run_host(FakeProcess(output))[0], "output_unparsed")

    def test_too_much_output_is_refused(self) -> None:
        output = b" " * (self.scope.MAX_HOST_OUTPUT_BYTES + 1) + b"[]"
        self.assert_unavailable(self.run_host(FakeProcess(output))[0], "output_too_large")

    def test_a_host_that_hangs_is_stopped(self) -> None:
        process = FakeProcess(hang=True)
        report, _ = self.run_host(process)

        self.assert_unavailable(report, "timeout")
        self.assertTrue(process.killed)
        self.assertEqual(process.timeouts[0], self.scope.HOST_LIST_TIMEOUT_SECONDS)

    def test_a_launcher_the_doctor_refuses_is_not_run(self) -> None:
        for reason_code in ("executable_not_found", "windows_non_exe_launcher_not_run",
                            "current_directory_executable_refused"):
            with self.subTest(reason_code=reason_code):
                report, popen = self.run_host(FakeProcess(), lambda: (None, reason_code))
                popen.assert_not_called()
                self.assert_unavailable(report, reason_code)

    def test_a_host_without_a_resolver_is_unavailable(self) -> None:
        self.assert_unavailable(self.report(host=True), "executable_not_found")

    def test_a_resolver_failure_is_a_fixed_word(self) -> None:
        def broken():
            raise RuntimeError("SECRET-RESOLVER")

        self.assert_unavailable(self.report(host=True, resolve_host=broken), "probe_failed")


class HealthcheckCopyTests(PluginScopeTestCase):
    def copy(self, text: str) -> Path:
        path = self.vault / "00-meta" / "scripts" / "vault_healthcheck.py"
        self.write(path, text.encode("utf-8"))
        return path

    def bundled(self) -> str:
        return (SCRIPTS / "vault_healthcheck.py").read_text(encoding="utf-8")

    def bundled_stamp(self) -> str:
        return self.scope.HEALTHCHECK_STAMP_RE.search(self.bundled()).group(1)

    def test_no_copy_is_no_warning(self) -> None:
        report = self.report()

        self.assertEqual(report["healthcheck_copy"], {
            "state": "missing", "installed_engine": None, "bundled_engine": self.bundled_stamp(),
        })
        self.assertEqual(report["findings"], [])

    def test_a_copy_without_a_stamp_is_a_warning(self) -> None:
        self.copy("#!/usr/bin/env python3\nprint('fork')\n")
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "unstamped")
        self.assertEqual(report["findings"], ["healthcheck_copy_unstamped"])
        self.assertEqual(report["next_action"], "align_the_healthcheck_copy_with_vault_upgrade")

    def test_an_older_copy_is_a_warning(self) -> None:
        self.copy("#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=0.1.0\n")
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "outdated")
        self.assertEqual(report["healthcheck_copy"]["installed_engine"], "0.1.0")
        self.assertEqual(report["findings"], ["healthcheck_copy_outdated"])

    def test_versions_compare_as_numbers(self) -> None:
        self.copy("#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=0.9.0\n")

        self.assertEqual(self.report()["healthcheck_copy"]["state"], "outdated")

    def test_the_same_copy_and_a_newer_copy_are_not_warnings(self) -> None:
        self.copy(self.bundled())
        self.assertEqual(self.report()["healthcheck_copy"]["state"], "current")
        self.assertEqual(self.report()["findings"], [])

        self.copy("#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=999.0.0\n")
        report = self.report()
        self.assertEqual(report["healthcheck_copy"]["state"], "newer")
        self.assertEqual(report["findings"], [])

    def test_a_changed_copy_with_the_same_stamp_is_named_but_not_a_warning(self) -> None:
        self.copy(self.bundled() + "\n# local edit\n")
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "modified")
        self.assertEqual(report["findings"], [])

    def test_a_stamp_that_is_no_version_is_never_printed(self) -> None:
        self.copy("#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=SECRET-STAMP\n")
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "unstamped")
        self.assertIsNone(report["healthcheck_copy"]["installed_engine"])
        self.assertEqual(report["findings"], ["healthcheck_copy_unstamped"])

    def test_an_unreadable_bundle_is_not_a_warning_about_the_copy(self) -> None:
        self.copy("#!/usr/bin/env python3\nprint('fork')\n")
        with mock.patch.object(self.scope, "BUNDLED_HEALTHCHECK", self.root / "missing.py"):
            report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "not_compared")
        self.assertEqual(report["findings"], [])

    def test_only_the_one_named_path_is_looked_at(self) -> None:
        stale = "#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=0.1.0\n"
        self.write(self.vault / "scripts" / "vault_healthcheck.py", stale.encode("utf-8"))
        self.write(self.vault / "00-meta" / "vault_healthcheck.py", stale.encode("utf-8"))
        report = self.report()

        self.assertEqual(report["healthcheck_copy"]["state"], "missing")
        self.assertEqual(report["findings"], [])

    def test_findings_keep_a_fixed_order(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        self.copy("#!/usr/bin/env python3\nprint('fork')\n")
        report = self.report()

        self.assertEqual(report["findings"], [FINDING, "healthcheck_copy_unstamped"])
        self.assertEqual(report["reason_code"], FINDING)
        lines = self.scope.text_lines(report)
        self.assertIn(f"plugin_scope_finding: {FINDING}", lines)
        self.assertIn("plugin_scope_finding: healthcheck_copy_unstamped", lines)


class FailSoftTests(PluginScopeTestCase):
    def test_an_unexpected_error_gives_fixed_words(self) -> None:
        with mock.patch.object(self.scope, "_report", side_effect=RuntimeError("SECRET-ERROR")):
            report = self.report()

        self.assertEqual((report["status"], report["reason_code"]), ("unknown", "probe_failed"))
        self.assertEqual(self.scope.text_lines(report)[0], "plugin_scope: unknown (probe_failed)")

    def test_a_missing_vault_is_not_an_exception(self) -> None:
        report = self.scope.diagnose_plugin_scope(self.root / "SECRET-MISSING")

        self.assertEqual(report["sources"]["project"]["file"], "unsafe_path")
        self.assertNotIn(SECRET, json.dumps(report))


class DoctorPluginScopeTests(PluginScopeTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.doctor = load_doctor()

    def make_ready_vault(self) -> Path:
        self.write(self.vault / "00-meta" / "vault-config.json", {
            "note_roots": ["00-meta"], "handoff_note": "00-meta/handoff.md",
            "hot_note": "00-meta/hot.md", "handoff_max_tokens": 4000, "hot_max_tokens": 100,
        })
        (self.vault / "00-meta" / "hot.md").write_text("Ship the verified change.\n", encoding="utf-8")
        (self.vault / "00-meta" / "handoff.md").write_text("", encoding="utf-8")
        return self.vault

    def run_main(self, *arguments: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.doctor.main(["--vault", str(self.vault), *arguments])
        return code, stdout.getvalue()

    def test_without_the_option_nothing_is_added(self) -> None:
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        code, text = self.run_main()
        json_code, raw = self.run_main("--format", "json")

        self.assertEqual((code, json_code), (0, 0))
        self.assertNotIn("plugin_scope", text)
        self.assertNotIn("plugin_scope", json.loads(raw))

    def test_the_exit_code_stays_the_vaults(self) -> None:
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        plain_code, _ = self.run_main("--format", "json")
        code, raw = self.run_main("--format", "json", "--plugin-scope")

        self.assertEqual(code, plain_code)
        self.assertEqual(json.loads(raw)["plugin_scope"]["findings"], [FINDING])

    def test_the_text_output_lists_each_source_in_order(self) -> None:
        self.user({"enabledPlugins": {PLUGIN_ID: True}})
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        _, text = self.run_main("--plugin-scope")
        lines = [line for line in text.splitlines() if line.startswith("plugin_scope")]

        self.assertEqual(lines[:9], [
            f"plugin_scope: attention ({FINDING})",
            "plugin_scope_trust_boundary: settings_files_only_other_sources_unknown",
            "plugin_scope_source: scope=managed; value=unknown; file=not_read",
            "plugin_scope_source: scope=flag; value=unknown; file=not_read",
            "plugin_scope_source: scope=local; value=unset; file=missing",
            "plugin_scope_source: scope=project; value=unset; file=read",
            "plugin_scope_source: scope=user; value=enabled; file=read",
            "plugin_scope_source: scope=add_dir; value=unknown; file=not_read",
            "plugin_scope_file_winner: scope=user; value=enabled",
        ])
        self.assertIn("plugin_scope_session_hooks: project=1; local=0", lines)
        self.assertIn("plugin_scope_host: not_requested (not_requested)", lines)
        self.assertEqual(lines[-1], "plugin_scope_next_action: keep_one_session_context_hook")
        self.assertNotIn(SECRET, text)

    def test_the_host_flag_needs_the_scope_flag(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            self.run_main("--plugin-scope-host")

        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--plugin-scope-host requires --plugin-scope", stderr.getvalue())

    def test_only_the_host_flag_runs_the_host(self) -> None:
        with mock.patch.object(self.doctor, "_resolve_host_executable",
                               return_value=(FAKE_CLAUDE, "resolved")), \
                mock.patch.object(self.scope.subprocess, "Popen",
                                  return_value=FakeProcess(host_output(host_entry()))) as popen:
            _, raw = self.run_main("--format", "json", "--plugin-scope")
            popen.assert_not_called()
            _, raw_host = self.run_main("--format", "json", "--plugin-scope", "--plugin-scope-host")

        popen.assert_called_once()
        self.assertEqual(json.loads(raw)["plugin_scope"]["host"]["status"], "not_requested")
        self.assertEqual(json.loads(raw_host)["plugin_scope"]["host"]["status"], "ok")

    def test_nothing_in_the_vault_changes(self) -> None:
        self.make_ready_vault()
        self.project({"hooks": {"SessionStart": [HOOK_GROUP]}})
        self.write(self.vault / "00-meta" / "scripts" / "vault_healthcheck.py", b"# SECRET-COPY\n")
        before = sorted((str(path), path.read_bytes() if path.is_file() else None)
                        for path in self.root.rglob("*"))
        self.run_main("--plugin-scope")
        after = sorted((str(path), path.read_bytes() if path.is_file() else None)
                       for path in self.root.rglob("*"))

        self.assertEqual(after, before)

    def test_a_module_that_cannot_be_loaded_gives_fixed_words(self) -> None:
        with mock.patch.dict(sys.modules, {"vault_plugin_scope": None}):
            text_code, text = self.run_main("--plugin-scope")
            json_code, raw = self.run_main("--format", "json", "--plugin-scope")

        self.assertEqual((text_code, json_code), (0, 0))
        self.assertIn("plugin_scope: unknown (probe_failed)", text.splitlines())
        self.assertEqual(json.loads(raw)["plugin_scope"], self.doctor.PLUGIN_SCOPE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
