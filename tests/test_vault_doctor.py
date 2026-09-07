from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCTOR = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_doctor.py"
SESSION_HOOK = REPO_ROOT / "hooks" / "session_start.py"


class VaultDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()

    def write(self, relative: str, text: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
            "handoff_note": "",
            "hot_note": "00-meta/hot.md",
            "deny_zones": ["90-assets"],
            "hot_max_tokens": 100,
        }
        config.update(overrides)
        self.write(
            "00-meta/vault-config.json",
            json.dumps(config, ensure_ascii=False),
        )

    def run_doctor(
        self,
        *arguments: str,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(DOCTOR), "--vault", str(self.vault), *arguments],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )

    def run_hook(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SESSION_HOOK), "--vault", str(self.vault)],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )

    def json_report(self) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = self.run_doctor("--format", "json")
        return result, json.loads(result.stdout)

    def snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    def test_valid_fixture_reports_ready_hot_section_as_json(self) -> None:
        self.write_config()
        self.write("00-meta/hot.md", "Ship the verified change.")

        result = self.run_doctor("--format", "json")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["sections"]["hot"]["status"], "ready")
        self.assertEqual(report["sections"]["hot"]["configured_token_budget"], 100)
        self.assertTrue(report["hook"]["would_emit_context"])
        self.assertEqual(report["hook"]["estimated_emitted_tokens"], 13)
        self.assertEqual(result.stderr, "")

    def test_not_vault_is_usable_and_does_not_claim_hook_execution(self) -> None:
        result, report = self.json_report()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["status"], "not_vault")
        self.assertEqual(report["reason_code"], "config_not_found")
        self.assertFalse(report["hook"]["would_emit_context"])
        self.assertEqual(
            report["hook"]["trust_boundary"],
            "diagnosis_only_host_hook_execution_unverified",
        )

    def test_missing_empty_and_disabled_states_have_literal_results(self) -> None:
        cases = (
            ("missing", None, 100, "missing", "note_missing", 1),
            ("empty", "  \n\t", 100, "empty", "note_empty", 1),
            ("disabled-budget", "unused", 0, "disabled", "budget_disabled", 0),
        )
        for label, note, budget, status, reason, exit_code in cases:
            with self.subTest(label=label):
                self.write_config(hot_max_tokens=budget)
                hot = self.vault / "00-meta/hot.md"
                if note is None:
                    if hot.exists():
                        hot.unlink()
                else:
                    self.write("00-meta/hot.md", note)

                result, report = self.json_report()

                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(report["sections"]["hot"]["status"], status)
                self.assertEqual(report["sections"]["hot"]["reason_code"], reason)
                self.assertFalse(report["sections"]["hot"]["would_emit"])

    def test_empty_configured_path_is_disabled(self) -> None:
        self.write_config(hot_note="", hot_max_tokens=100)

        result, report = self.json_report()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["sections"]["hot"]["status"], "disabled")
        self.assertEqual(report["sections"]["hot"]["reason_code"], "path_disabled")
        self.assertEqual(report["sections"]["hot"]["configured_token_budget"], 100)

    def test_malformed_json_and_schema_error_are_distinguished_and_redacted(self) -> None:
        cases = (
            ('{"ARBITRARY_SECRET_KEY": "SECRET_CONFIG_VALUE"', "config_json_invalid"),
            (
                json.dumps({"hot_max_tokens": "SECRET_CONFIG_VALUE"}),
                "config_schema_invalid",
            ),
        )
        for raw, reason in cases:
            with self.subTest(reason=reason):
                self.write("00-meta/vault-config.json", raw)

                result, report = self.json_report()

                self.assertEqual(result.returncode, 2)
                self.assertEqual(report["status"], "invalid_config")
                self.assertEqual(report["reason_code"], reason)
                self.assertNotIn("ARBITRARY_SECRET_KEY", result.stdout)
                self.assertNotIn("SECRET_CONFIG_VALUE", result.stdout)
                self.assertNotIn("SECRET_CONFIG_VALUE", result.stderr)

    @unittest.skipUnless(
        hasattr(sys, "get_int_max_str_digits"),
        "Python integer digit limit unavailable",
    )
    def test_digit_limited_json_value_is_classified_without_a_traceback(self) -> None:
        self.write(
            "00-meta/vault-config.json",
            '{"SECRET_CONFIG_KEY": ' + "9" * 5000 + "}",
        )

        result = self.run_doctor(
            "--format", "json",
            env_overrides={"PYTHONINTMAXSTRDIGITS": "4300"},
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "invalid_config")
        self.assertEqual(report["reason_code"], "config_json_invalid")
        self.assertEqual(result.stderr, "")
        self.assertNotIn("SECRET_CONFIG_KEY", result.stdout + result.stderr)
        self.assertNotIn("9" * 100, result.stdout + result.stderr)

    def test_oversized_config_is_invalid_without_echoing_its_tail(self) -> None:
        self.write("00-meta/vault-config.json", " " * (256 * 1024) + "TAIL_SECRET")

        result, report = self.json_report()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "invalid_config")
        self.assertEqual(report["reason_code"], "config_too_large")
        self.assertNotIn("TAIL_SECRET", result.stdout + result.stderr)

    def test_bad_section_suppresses_ready_section_like_hook(self) -> None:
        self.write_config(
            handoff_note="00-meta/handoff.md",
            handoff_max_tokens=100,
            hot_note="90-assets/private.md",
        )
        self.write("00-meta/handoff.md", "HANDOFF_SECRET")
        self.write("90-assets/private.md", "DENIED_SECRET")

        doctor, report = self.json_report()
        hook = self.run_hook()

        self.assertEqual(doctor.returncode, 2)
        self.assertEqual(report["status"], "invalid_config")
        self.assertEqual(report["reason_code"], "section_fatal")
        self.assertEqual(report["sections"]["handoff"]["status"], "ready")
        self.assertTrue(report["sections"]["handoff"]["independently_would_emit"])
        self.assertFalse(report["sections"]["handoff"]["would_emit"])
        self.assertEqual(report["sections"]["handoff"]["effective_token_budget"], 0)
        self.assertEqual(report["sections"]["hot"]["status"], "unsafe_path")
        self.assertEqual(report["sections"]["hot"]["reason_code"], "note_path_unsafe")
        self.assertFalse(report["hook"]["would_emit_context"])
        self.assertEqual(report["hook"]["estimated_emitted_tokens"], 0)
        self.assertEqual(hook.returncode, 0)
        self.assertEqual(hook.stdout, "")
        self.assertNotIn("HANDOFF_SECRET", doctor.stdout + doctor.stderr)
        self.assertNotIn("DENIED_SECRET", doctor.stdout + doctor.stderr)
        self.assertNotIn("90-assets/private.md", doctor.stdout + doctor.stderr)

    def test_traversal_is_an_unsafe_section_without_reading_outside(self) -> None:
        outside = self.vault.parent / "outside.md"
        outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
        self.write_config(hot_note="../outside.md")

        result, report = self.json_report()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["sections"]["hot"]["status"], "unsafe_path")
        self.assertFalse(report["hook"]["would_emit_context"])
        self.assertNotIn("OUTSIDE_SECRET", result.stdout + result.stderr)
        self.assertNotIn("../outside.md", result.stdout + result.stderr)

    def test_linked_note_is_unsafe_and_content_is_redacted(self) -> None:
        outside = self.vault.parent / "outside-link.md"
        outside.write_text("LINK_SECRET", encoding="utf-8")
        link = self.vault / "00-meta" / "hot.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file symlink unavailable on this host: {exc}")
        self.write_config()

        result, report = self.json_report()

        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["sections"]["hot"]["status"], "unsafe_path")
        self.assertNotIn("LINK_SECRET", result.stdout + result.stderr)

    def test_budget_too_small_reports_no_effective_output(self) -> None:
        self.write_config(hot_max_tokens=1)
        self.write("00-meta/hot.md", "Visible only with a usable budget.")

        result, report = self.json_report()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["sections"]["hot"]["status"], "budget_too_small")
        self.assertEqual(
            report["sections"]["hot"]["reason_code"], "section_header_exceeds_budget"
        )
        self.assertFalse(report["hook"]["would_emit_context"])

    def test_token_budget_truncation_reports_actual_emitted_estimate(self) -> None:
        self.write_config(hot_max_tokens=20)
        self.write("00-meta/hot.md", "English context " * 500)

        result, report = self.json_report()

        self.assertEqual(result.returncode, 1)
        section = report["sections"]["hot"]
        self.assertEqual(section["status"], "truncated")
        self.assertEqual(section["reason_code"], "token_budget_limit")
        self.assertLessEqual(section["estimated_emitted_tokens"], 20)
        self.assertTrue(report["hook"]["would_emit_context"])

    def test_source_byte_limit_is_reported_without_reading_tail(self) -> None:
        self.write_config(hot_max_tokens=1_000_000)
        path = self.vault / "00-meta" / "hot.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"A" * (300 * 1024) + b"TAIL_SECRET")

        result, report = self.json_report()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["sections"]["hot"]["status"], "truncated")
        self.assertEqual(report["sections"]["hot"]["reason_code"], "source_byte_limit")
        self.assertNotIn("TAIL_SECRET", result.stdout + result.stderr)

    def test_unspecified_budget_uses_hook_default(self) -> None:
        self.write_config()
        raw = json.loads((self.vault / "00-meta/vault-config.json").read_text("utf-8"))
        del raw["hot_max_tokens"]
        self.write("00-meta/vault-config.json", json.dumps(raw))
        self.write("00-meta/hot.md", "Default budget context.")

        result, report = self.json_report()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["sections"]["hot"]["configured_token_budget"], 2000)
        self.assertEqual(report["hook"]["configured_token_budget"], 6000)

    def test_diagnosis_does_not_change_any_vault_file_bytes(self) -> None:
        self.write_config(handoff_note="00-meta/handoff.md", handoff_max_tokens=100)
        self.write("00-meta/handoff.md", "Keep these bytes.\r\n")
        self.write("00-meta/hot.md", "Keep these too.\n")
        before = self.snapshot()

        result = self.run_doctor()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("ready", result.stdout)
        self.assertIn("diagnosis_only_host_hook_execution_unverified", result.stdout)
        self.assertNotIn("Keep these bytes", result.stdout)


if __name__ == "__main__":
    unittest.main()
