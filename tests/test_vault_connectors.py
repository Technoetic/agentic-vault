"""Connector diagnostics: real temporary configs, synthetic responses, no network."""
from __future__ import annotations

import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
CLI = SCRIPTS / "vault_connectors.py"
KEY = "synthetic-connector-test-key"
TOKEN = "123456789:" + "x" * 35


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CLI.is_file(), "Connector stage diagnostics are not implemented")
        sys.path.insert(0, str(SCRIPTS))
        try:
            self.module = importlib.import_module("vault_connectors")
            self.client = importlib.import_module("jev_client")
        finally:
            sys.path.remove(str(SCRIPTS))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve() / "vault"
        self.vault.mkdir()
        self.config_path = self.vault / "00-meta" / "vault-config.json"
        self.write_config()

    def write_config(self, **overrides):
        config = {"hot_note": "", "handoff_note": "", "deny_zones": ["90-assets"],
                  "exclude_dirs": [".git", "step_archive"]}
        config.update(overrides)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def diagnose(self, **options):
        options.setdefault("env", {})
        return self.module.diagnose(self.vault, **options)

    def never_send(self, *_args):
        self.fail("Offline or unsupported diagnostics attempted a network call")

    def response(self, raw, key, timeout):
        payload = json.loads(raw)
        self.assertEqual(key, KEY)
        self.assertEqual(payload["state"], {"context": {
            "kind": "user_input", "text": "This is a public connector health-check fixture."}})
        self.assertEqual(set(payload["questions"]), {"connector_health"})
        self.assertEqual(payload["questions"]["connector_health"]["type"], "noul")
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 10)
        return json.dumps({"model": "jev-1.13.0", "answers": {
            "connector_health": {"type": "noul", "noul": 0.98}},
            "usage": {"input_tokens": 10, "output_tokens": 2}}).encode()

    def test_credentials_present_offline_are_never_authentication_success(self):
        report, code = self.diagnose(env={"TYPESAFE_API_KEY": KEY}, transport=self.never_send)
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "offline")
        self.assertFalse(report["network_attempted"])
        jev = report["connectors"]["jev"]
        self.assertEqual(jev["installed"]["state"], "available")
        self.assertEqual(jev["configured"]["state"], "configured")
        self.assertEqual(jev["credential_available"]["state"], "available")
        self.assertEqual(jev["authentication"], {
            "state": "not_checked", "checked": False, "reason_code": "offline_default"})
        self.assertEqual(jev["operation"]["state"], "not_checked")
        self.assertNotIn(KEY, json.dumps(report))

    def test_missing_credentials_remain_missing_and_do_not_send_on_probe(self):
        report, code = self.diagnose(probe="jev", transport=self.never_send)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "probe_failed")
        self.assertFalse(report["network_attempted"])
        jev = report["connectors"]["jev"]
        self.assertEqual(jev["credential_available"]["state"], "missing")
        self.assertEqual(jev["authentication"]["state"], "not_checked")
        self.assertEqual(jev["next_action"], "provide_typesafe_api_key_in_process_environment")

    def test_invalid_credential_shapes_are_not_sent_or_reported(self):
        for value in (123, True, " leading", "bad\r\nheader", "x" * 4097):
            with self.subTest(value_type=type(value).__name__):
                report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": value},
                                             transport=self.never_send)
                self.assertEqual(code, 1)
                self.assertEqual(report["connectors"]["jev"]["credential_available"]["state"], "invalid")
                self.assertFalse(report["network_attempted"])

    def test_explicit_probe_uses_existing_typed_contract_and_only_synthetic_context(self):
        (self.vault / ".env").write_text("TYPESAFE_API_KEY=should-never-read", encoding="utf-8")
        report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY},
                                    timeout=2, transport=self.response)
        self.assertEqual(code, 0)
        self.assertTrue(report["network_attempted"])
        self.assertEqual(report["status"], "diagnosed")
        self.assertEqual(report["connectors"]["jev"]["authentication"], {
            "state": "passed", "checked": True, "reason_code": "validated_provider_response"})
        self.assertEqual(report["connectors"]["jev"]["operation"], {
            "state": "passed", "checked": True, "reason_code": "validated_synthetic_judgment"})
        self.assertEqual(report["connectors"]["jev"]["proof_limit"], "public_synthetic_request_only")
        self.assertNotIn("should-never-read", json.dumps(report))
        self.assertNotIn("results", report["connectors"]["jev"])

    def test_authentication_rejection_is_failed_and_redacted(self):
        def reject(*_args):
            raise self.client.JevError("authentication")
        report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY}, transport=reject)
        self.assertEqual(code, 1)
        jev = report["connectors"]["jev"]
        self.assertEqual(jev["authentication"], {
            "state": "failed", "checked": True, "reason_code": "authentication"})
        self.assertEqual(jev["operation"]["state"], "failed")
        self.assertNotIn(KEY, json.dumps(report))

    def test_transport_failures_do_not_claim_successful_authentication(self):
        for failure, reason in ((TimeoutError(KEY + " private body"), "timeout"),
                                (RuntimeError(KEY + " private body"), "transport"),
                                (self.client.JevError("rate_limit"), "rate_limit")):
            with self.subTest(reason=reason):
                def fail(*_args):
                    raise failure
                report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY}, transport=fail)
                self.assertEqual(code, 1)
                jev = report["connectors"]["jev"]
                self.assertTrue(report["network_attempted"])
                self.assertEqual(jev["authentication"]["state"], "unknown")
                self.assertTrue(jev["authentication"]["checked"])
                self.assertEqual(jev["operation"]["reason_code"], reason)
                self.assertNotIn(KEY, json.dumps(report))
                self.assertNotIn("private body", json.dumps(report))

    def test_malformed_duplicate_nonfinite_and_oversized_provider_bodies_are_redacted(self):
        bodies = (b'{"model":"jev-1.13.0","model":"jev-1.13.0"}',
                  b'{"model":"jev-1.13.0","answers":NaN}',
                  b"x" * (64 * 1024 + 1), (KEY + " private body").encode())
        for body in bodies:
            with self.subTest(size=len(body)):
                report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY},
                                             transport=lambda *_args: body)
                self.assertEqual(code, 1)
                jev = report["connectors"]["jev"]
                self.assertEqual(jev["authentication"]["state"], "unknown")
                self.assertEqual(jev["operation"]["reason_code"], "malformed_response")
                self.assertNotIn(KEY, json.dumps(report))
                self.assertNotIn("private body", json.dumps(report))

    def test_synthetic_low_confidence_still_proves_response_contract_only(self):
        def uncertain(raw, key, timeout):
            value = json.loads(self.response(raw, key, timeout))
            value["answers"]["connector_health"]["noul"] = 0.5
            return json.dumps(value).encode()
        report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY}, transport=uncertain)
        self.assertEqual(code, 0)
        self.assertEqual(report["connectors"]["jev"]["operation"]["state"], "passed")
        self.assertEqual(report["connectors"]["jev"]["proof_limit"], "public_synthetic_request_only")

    def test_custom_provider_urls_are_never_used(self):
        report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY,
            "TYPESAFE_BASE_URL": "https://private.invalid/leak", "JEV_ENDPOINT": "file:///private"},
            transport=self.response)
        self.assertEqual(code, 0)
        self.assertEqual(report["connectors"]["jev"]["provider"], "typesafe_systemone")
        self.assertNotIn("private.invalid", json.dumps(report))

    def test_jarvis_disabled_and_missing_configuration_are_separate(self):
        report, _code = self.diagnose()
        self.assertEqual(report["connectors"]["jarvis"]["configured"]["state"], "absent")
        self.write_config(jarvis={"enabled": False})
        report, _code = self.diagnose()
        self.assertEqual(report["connectors"]["jarvis"]["configured"]["state"], "disabled")

    def test_jarvis_bare_api_credentials_are_distinct_from_subscription_login(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345]})
        report, code = self.diagnose(env={"JARVIS_TELEGRAM_TOKEN": TOKEN,
            "CLAUDE_CODE_OAUTH_TOKEN": KEY})
        self.assertEqual(code, 0)
        jarvis = report["connectors"]["jarvis"]
        self.assertEqual(jarvis["configured"]["state"], "configured")
        self.assertEqual(jarvis["credentials"]["telegram_token"]["state"], "available")
        self.assertEqual(jarvis["credentials"]["bare_claude_api"]["state"], "missing")
        self.assertEqual(jarvis["credential_available"]["state"], "partial")
        self.assertEqual(jarvis["authentication"]["state"], "not_checked")
        self.assertNotIn(TOKEN, json.dumps(report))
        self.assertNotIn(KEY, json.dumps(report))

    def test_jarvis_env_api_credentials_do_not_imply_working_connection(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345]})
        for env_name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            with self.subTest(env_name=env_name):
                report, _code = self.diagnose(env={"JARVIS_TELEGRAM_TOKEN": TOKEN, env_name: KEY})
                jarvis = report["connectors"]["jarvis"]
                self.assertEqual(jarvis["credential_available"]["state"], "available")
                self.assertEqual(jarvis["authentication"]["state"], "not_checked")
                self.assertFalse(jarvis["operation"]["checked"])

    def test_jarvis_unknown_auth_provider_remains_unknown(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345]})
        for override in ({"CLAUDE_CODE_USE_BEDROCK": "1"}, {"CLAUDE_CODE_USE_VERTEX": "1"},
                         {"CLAUDE_CODE_USE_FOUNDRY": "1"}, {"ANTHROPIC_BASE_URL": "https://private.invalid"}):
            with self.subTest(override_name=next(iter(override))):
                report, _code = self.diagnose(env={"JARVIS_TELEGRAM_TOKEN": TOKEN,
                    "ANTHROPIC_API_KEY": KEY, **override})
                jarvis = report["connectors"]["jarvis"]
                self.assertEqual(jarvis["credentials"]["bare_claude_api"]["state"], "unknown")
                self.assertEqual(jarvis["credential_available"]["state"], "unknown")
                self.assertEqual(jarvis["authentication"]["state"], "not_checked")

    def test_jarvis_unsupported_probe_cannot_launch_a_process(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345],
                                 "claude_cmd": "arbitrary-command --write-secret"})
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("launch")):
            report, code = self.diagnose(probe="jarvis", env={"JARVIS_TELEGRAM_TOKEN": TOKEN,
                "ANTHROPIC_API_KEY": KEY}, transport=self.never_send)
        self.assertEqual(code, 1)
        self.assertFalse(report["network_attempted"])
        jarvis = report["connectors"]["jarvis"]
        self.assertFalse(jarvis["probe_supported"])
        self.assertEqual(jarvis["authentication"], {
            "state": "not_checked", "checked": False, "reason_code": "probe_unsupported"})
        self.assertEqual(jarvis["operation"]["state"], "not_checked")
        self.assertNotIn("arbitrary-command", json.dumps(report))

    def test_invalid_jarvis_settings_are_reported_without_values(self):
        for block in ({"enabled": "yes"}, {"enabled": True, "telegram_user_ids": [-1]},
                      {"enabled": True, "claude_cmd": "private\ncommand"},
                      {"enabled": True, "qa_timeout_sec": 0},
                      {"enabled": True, "briefing_times": ["25:00"]}):
            with self.subTest(block=block):
                self.write_config(jarvis=block)
                report, _code = self.diagnose()
                self.assertEqual(report["connectors"]["jarvis"]["configured"]["state"], "invalid")
                self.assertNotIn("private", json.dumps(report))

    def test_empty_allowlist_is_incomplete_configuration(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": []})
        report, _code = self.diagnose()
        self.assertEqual(report["connectors"]["jarvis"]["configured"]["state"], "incomplete")

    def test_missing_installation_does_not_probe(self):
        with mock.patch.object(self.module, "SCRIPTS_DIR", Path(self.temp.name) / "missing"):
            report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY}, transport=self.never_send)
        self.assertEqual(code, 1)
        self.assertEqual(report["connectors"]["jev"]["installed"]["state"], "missing")
        self.assertFalse(report["network_attempted"])

    def test_environment_files_and_denied_content_are_never_opened(self):
        (self.vault / ".env").write_text("private-secret", encoding="utf-8")
        denied = self.vault / "90-assets"
        denied.mkdir()
        (denied / ".env").write_text("another-private-secret", encoding="utf-8")
        original_open = os.open
        opened = []
        def checked_open(path, *args, **kwargs):
            opened.append(Path(path))
            self.assertIn(Path(path), (self.config_path, SCRIPTS / "jarvis_bridge.py"))
            return original_open(path, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=checked_open):
            report, code = self.diagnose()
        self.assertEqual(code, 0)
        self.assertTrue(opened)
        self.assertEqual(report["connectors"]["jev"]["credential_available"]["state"], "missing")

    def test_invalid_config_json_duplicate_nonfinite_and_oversized_inputs_fail_closed(self):
        for raw in (b'{"jarvis":{},"jarvis":{}}', b'{"unused":NaN}',
                    b'{"unused":1e999}', b'[]', b'{"unused":"\xff"}',
                    b' ' * (256 * 1024 + 1)):
            with self.subTest(size=len(raw)):
                self.config_path.write_bytes(raw)
                report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY},
                                             transport=self.never_send)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "invalid_config")
                self.assertFalse(report["network_attempted"])

    def test_denied_configuration_is_rejected(self):
        self.write_config(deny_zones=["00-meta"])
        report, code = self.diagnose()
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "invalid_config")

    def test_config_hardlinks_are_rejected(self):
        try:
            os.link(self.config_path, self.config_path.parent / "linked.json")
        except OSError as error:
            self.skipTest(str(error))
        report, code = self.diagnose()
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "invalid_config")

    def test_config_symlinks_are_rejected(self):
        target = self.config_path.parent / "real.json"
        self.config_path.rename(target)
        try:
            self.config_path.symlink_to(target)
        except OSError as error:
            self.skipTest(str(error))
        report, code = self.diagnose()
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "invalid_config")

    def test_not_vault_does_not_send(self):
        self.config_path.unlink()
        report, code = self.diagnose(probe="jev", env={"TYPESAFE_API_KEY": KEY}, transport=self.never_send)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "not_vault")
        self.assertFalse(report["network_attempted"])

    def test_unsupported_provider_and_invalid_timeouts_fail_before_network(self):
        for options in ({"probe": "custom"}, {"probe": "https://private.invalid"},
                        {"timeout": math.nan}, {"timeout": math.inf}, {"timeout": 0},
                        {"timeout": 11}, {"timeout": True}, {"transport": "arbitrary-command"}):
            with self.subTest(option_names=list(options)):
                report, code = self.diagnose(env={"TYPESAFE_API_KEY": KEY}, **options)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "invalid_arguments")
                self.assertNotIn("private.invalid", json.dumps(report))

    def run_cli(self, *args, script=None):
        env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
               if name in os.environ}
        # Jarvis's import-time state root needs a home; use the temporary root,
        # without inheriting any real login or credential store locations.
        env["USERPROFILE"] = self.temp.name
        env["HOME"] = self.temp.name
        env["TYPESAFE_API_KEY"] = KEY
        return subprocess.run([sys.executable, "-B", str(CLI if script is None else script),
                               "--vault", str(self.vault), *args],
                              cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", timeout=15)

    def isolated_scripts(self, label, *, omit=None):
        directory = Path(self.temp.name) / label
        directory.mkdir()
        # Copy only the helper's fixed source dependencies. No installed plugin
        # is changed and no inherited module cache can mask a missing file.
        for name in ("vault_connectors.py", "vault_evidence.py", "vault_healthcheck.py",
                     "vault_paths.py", "vault_state.py", "jev_ask.py", "jev_client.py",
                     "jarvis_bridge.py"):
            if name != omit:
                shutil.copyfile(SCRIPTS / name, directory / name)
        return directory

    def assert_missing_dependency(self, filename, connector):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345]})
        scripts = self.isolated_scripts("without-" + filename, omit=filename)
        completed = self.run_cli("--format", "json", script=scripts / CLI.name)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["connectors"][connector]["installed"], {
            "state": "missing", "reason_code": "installed_script_missing"})
        other = "jarvis" if connector == "jev" else "jev"
        self.assertEqual(report["connectors"][other]["configured"]["state"], "configured")
        self.assertEqual(report["connectors"][other]["authentication"]["state"], "not_checked")
        self.assertFalse(report["network_attempted"])
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        probe = self.run_cli("--probe", connector, "--format", "json", script=scripts / CLI.name)
        self.assertEqual(probe.returncode, 1, probe.stderr)
        self.assertFalse(json.loads(probe.stdout)["network_attempted"])

    def test_fresh_process_missing_jarvis_bridge_preserves_jev_diagnosis(self):
        self.assert_missing_dependency("jarvis_bridge.py", "jarvis")

    def test_fresh_process_missing_jev_ask_preserves_jarvis_diagnosis(self):
        self.assert_missing_dependency("jev_ask.py", "jev")

    def test_fresh_process_missing_jev_client_preserves_jarvis_diagnosis(self):
        self.assert_missing_dependency("jev_client.py", "jev")

    def test_fresh_process_unimportable_dependencies_are_unknown_and_redacted(self):
        self.write_config(jarvis={"enabled": True, "telegram_user_ids": [12345]})
        for name, connector in (("jarvis_bridge.py", "jarvis"), ("jev_ask.py", "jev"),
                                ("jev_client.py", "jev")):
            with self.subTest(dependency=name):
                scripts = self.isolated_scripts("unimportable-" + name)
                (scripts / name).write_text("raise RuntimeError('synthetic-private-import-error')\n",
                                            encoding="utf-8")
                arguments = ("--format", "json")
                if connector == "jev":
                    # Loading Jev is explicitly lazy: only a requested probe
                    # checks importability, and import failure cancels the call.
                    arguments += ("--probe", "jev")
                completed = self.run_cli(*arguments, script=scripts / CLI.name)
                self.assertEqual(completed.returncode, 1 if connector == "jev" else 0,
                                 completed.stderr)
                self.assertTrue(completed.stdout.strip(), "Structured dependency report missing")
                report = json.loads(completed.stdout)
                self.assertEqual(report["connectors"][connector]["installed"], {
                    "state": "unknown", "reason_code": "installed_dependency_unavailable"})
                other = "jarvis" if connector == "jev" else "jev"
                self.assertEqual(report["connectors"][other]["configured"]["state"], "configured")
                self.assertFalse(report["network_attempted"])
                self.assertNotIn("synthetic-private-import-error", completed.stdout + completed.stderr)
                self.assertNotIn("Traceback", completed.stdout + completed.stderr)

    def test_fresh_process_hardlinked_provider_modules_are_never_executed(self):
        for name, connector in (("jarvis_bridge.py", "jarvis"), ("jev_ask.py", "jev"),
                                ("jev_client.py", "jev")):
            with self.subTest(dependency=name):
                scripts = self.isolated_scripts("hardlinked-" + name, omit=name)
                target = scripts / "unsafe-source.py"
                target.write_text(
                    "from pathlib import Path\n"
                    "Path(__file__).with_name('executed-marker').write_text('unsafe')\n"
                    "raise RuntimeError('synthetic-private-linked-module')\n", encoding="utf-8")
                os.link(target, scripts / name)
                arguments = ("--format", "json")
                if connector == "jev":
                    arguments += ("--probe", "jev")
                completed = self.run_cli(*arguments, script=scripts / CLI.name)
                self.assertEqual(completed.returncode, 1 if connector == "jev" else 0,
                                 completed.stderr)
                self.assertTrue(completed.stdout.strip(), "Structured unsafe-module report missing")
                report = json.loads(completed.stdout)
                self.assertEqual(report["connectors"][connector]["installed"]["state"], "unsafe")
                self.assertFalse(report["network_attempted"])
                self.assertFalse((scripts / "executed-marker").exists())
                self.assertNotIn("synthetic-private-linked-module", completed.stdout + completed.stderr)

    def test_cli_json_and_text_are_offline_read_only(self):
        before = self.config_path.read_bytes()
        completed = self.run_cli("--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["network_attempted"])
        self.assertEqual(report["connectors"]["jev"]["credential_available"]["state"], "available")
        self.assertNotIn(KEY, completed.stdout + completed.stderr)
        text = self.run_cli("--format", "text")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("jev.authentication: not_checked", text.stdout)
        self.assertIn("jarvis.operation: not_checked", text.stdout)
        self.assertEqual(before, self.config_path.read_bytes())
        self.assertFalse((self.vault / "00-meta" / ".agentic-vault").exists())

    def test_cli_unknown_and_duplicate_arguments_do_not_echo_sensitive_values(self):
        for args in (("--probe", KEY), ("--url", "https://private.invalid/" + KEY),
                     ("--probe", "jarvis", "--probe", "jev"), ("--timeout", KEY)):
            with self.subTest(option_names=args[::2]):
                completed = self.run_cli(*args)
                self.assertEqual(completed.returncode, 2)
                report = json.loads(completed.stdout)
                self.assertEqual(report["status"], "invalid_arguments")
                self.assertNotIn(KEY, completed.stdout + completed.stderr)
                self.assertNotIn("private.invalid", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
