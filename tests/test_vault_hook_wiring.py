"""Tests for read-only hook-wiring target existence auditing (vault_hook_wiring + doctor --hook-wiring)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_hook_wiring  # noqa: E402


class VaultHookWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="av-wiring-")
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name).resolve() / "vault"
        (self.vault / "00-meta" / "scripts").mkdir(parents=True)
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({
                "deny_zones": ["90-assets"],
                "exclude_dirs": [".git"],
                "handoff_note": "00-meta/handoff.md",
                "hot_note": "00-meta/hot.md",
            }),
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "handoff.md").write_text("Continue.\n", encoding="utf-8")
        (self.vault / "00-meta" / "hot.md").write_text("Hot.\n", encoding="utf-8")

    def test_none_when_no_settings_or_git_hooks(self) -> None:
        report = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(report["status"], "none")
        self.assertEqual(report["settings_checked"], 0)
        self.assertEqual(report["targets_missing"], 0)

    def test_detects_ok_missing_and_skipped_targets_without_leaking_raw_command(self) -> None:
        present_script = self.vault / "00-meta" / "scripts" / "deny-guard.py"
        present_script.write_text("# present\n", encoding="utf-8")
        secret_arg = "SUPER_SECRET_INLINE_FLAG_VALUE_98765"
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python",
                                "args": ["-X", "utf8", "${CLAUDE_PROJECT_DIR}/00-meta/scripts/deny-guard.py"],
                            },
                            {
                                "type": "command",
                                "command": f"python \"${{CLAUDE_PROJECT_DIR}}/00-meta/scripts/missing-hook.py\" --token {secret_arg}",
                            },
                            {
                                "type": "command",
                                "command": "python \"${UNKNOWN_CUSTOM_ROOT}/custom.py\"",
                            },
                        ],
                    }
                ]
            }
        }
        claude_dir = self.vault / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.local.json").write_text(json.dumps(settings), encoding="utf-8")

        report = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(report["status"], "missing_targets")
        self.assertEqual(report["settings_checked"], 1)
        self.assertEqual(report["commands_checked"], 3)
        self.assertEqual(report["targets_ok"], 1)
        self.assertEqual(report["targets_missing"], 1)
        self.assertEqual(report["commands_skipped"], 1)
        self.assertEqual(report["missing_targets"][0]["missing_path"], "00-meta/scripts/missing-hook.py")
        # No-leak contract: raw command arguments never appear in JSON or text output.
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret_arg, serialized)
        self.assertNotIn(secret_arg, "\n".join(vault_hook_wiring.text_lines(report)))

    def test_git_hooks_require_vault_healthcheck_copy(self) -> None:
        gh_dir = self.vault / "00-meta" / "scripts" / "git-hooks"
        gh_dir.mkdir(parents=True)
        (gh_dir / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")

        report = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(report["status"], "missing_targets")
        self.assertEqual(report["missing_targets"][0]["missing_path"], "00-meta/scripts/vault_healthcheck.py")

        (self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").write_text("# ok\n", encoding="utf-8")
        ok_report = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(ok_report["status"], "ok")
        self.assertEqual(ok_report["targets_missing"], 0)

    def test_unreadable_settings_json_fails_soft(self) -> None:
        claude_dir = self.vault / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("{not-valid-json", encoding="utf-8")

        report = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(report["status"], "unreadable_settings")
        self.assertEqual(report["settings_errors"], [{"source": ".claude/settings.json", "reason": "invalid_json"}])

    def test_doctor_cli_hook_wiring_preserves_exit_code(self) -> None:
        claude_dir = self.vault / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text(
            json.dumps({
                "hooks": {
                    "SessionStart": [{
                        "hooks": [{"type": "command", "command": "python ${CLAUDE_PROJECT_DIR}/00-meta/scripts/nope.py"}]
                    }]
                }
            }),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_doctor.py"), "--vault", str(self.vault), "--hook-wiring", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["hook_wiring"]["status"], "missing_targets")
        self.assertEqual(data["hook_wiring"]["targets_missing"], 1)

    def test_windows_backslashes_single_quoted_spaces_prepush_only_and_uncapped_count(self) -> None:
        # 1. pre-push alone must NOT demand vault_healthcheck.py or blame pre-commit
        gh_dir = self.vault / "00-meta" / "scripts" / "git-hooks"
        gh_dir.mkdir(parents=True)
        (gh_dir / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
        prepush_only = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(prepush_only["status"], "ok")
        self.assertEqual(prepush_only["targets_missing"], 0)

        # 2. Windows unquoted backslashes + single-quoted path with spaces + >50 missing count
        (self.vault / "00-meta" / "scripts" / "aside-up.ps1").write_text("# ok\n", encoding="utf-8")
        hooks_list = [
            {"type": "command", "command": r"powershell -NoProfile -File 00-meta\scripts\aside-up.ps1"},
            {"type": "command", "command": "python '${CLAUDE_PROJECT_DIR}/my dir/placeholder.py'"},
        ]
        for idx in range(55):
            hooks_list.append({
                "type": "command",
                "command": f"python 00-meta/scripts/missing_{idx:02d}.py",
            })
        claude_dir = self.vault / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [{"hooks": hooks_list}]}}),
            encoding="utf-8",
        )
        rep = vault_hook_wiring.diagnose_hook_wiring(self.vault)
        self.assertEqual(rep["status"], "missing_targets")
        self.assertEqual(rep["targets_ok"], 1)
        self.assertEqual(rep["commands_skipped"], 1)
        self.assertEqual(rep["targets_missing"], 55)
        self.assertEqual(len(rep["missing_targets"]), vault_hook_wiring.MAX_MISSING_REPORTED)


if __name__ == "__main__":
    unittest.main()
