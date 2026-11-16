"""Regression tests for SessionStart secret redaction and Antigravity Stop deduplication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"
SCRIPTS = REPO_ROOT / "skills" / "agentic-vault" / "scripts"


class SessionSecurityAndStopDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="av-sec-hook-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.temp = self.base / "temp"
        self.temp.mkdir()
        self.plugin = self.base / "plugin"
        shutil.copytree(HOOKS, self.plugin / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(SCRIPTS, self.plugin / "skills" / "agentic-vault" / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.vault = self.base / "vault"
        (self.vault / "00-meta").mkdir(parents=True)
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({
                "deny_zones": ["90-assets"],
                "exclude_dirs": [".git", ".agentic-vault"],
                "handoff_note": "00-meta/handoff.md",
                "hot_note": "00-meta/hot.md",
                "log_note": "00-meta/log.md",
            }),
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "log.md").write_text("- 2026-11-16 09:00 | t | [ops] init\n", encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE_")}
        e.update({"TMP": str(self.temp), "TEMP": str(self.temp), "TMPDIR": str(self.temp)})
        e.update(extra)
        return e

    def test_session_start_redacts_secrets_across_startup_compact_and_antigravity(self) -> None:
        fake_sk = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        fake_gh = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
        (self.vault / "00-meta" / "handoff.md").write_text(
            f"## Next\n- Safe line 1\n- Leaked token: {fake_sk}\n- Keep going\n",
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "hot.md").write_text(
            f"Safe hot header\nGITHUB_TOKEN={fake_gh}\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAsecret\n-----END RSA PRIVATE KEY-----\nSafe footer\n",
            encoding="utf-8",
        )

        # 1) Default Claude/Codex startup
        startup = subprocess.run(
            [sys.executable, str(self.plugin / "hooks" / "session_start.py"), "--vault", str(self.vault)],
            input='{"source":"startup"}', text=True, encoding="utf-8", capture_output=True,
            env=self.env(), timeout=15, check=False,
        )
        self.assertEqual(startup.returncode, 0, startup.stderr)
        self.assertIn("Safe line 1", startup.stdout)
        self.assertIn("Safe footer", startup.stdout)
        self.assertNotIn(fake_sk, startup.stdout)
        self.assertNotIn(fake_gh, startup.stdout)
        self.assertNotIn("MIIEpAIBAAKCAQEAsecret", startup.stdout)
        self.assertIn("[REDACTED:", startup.stdout)

        # 2) Compact mode
        compact = subprocess.run(
            [sys.executable, str(self.plugin / "hooks" / "session_start.py"), "--vault", str(self.vault)],
            input='{"source":"compact"}', text=True, encoding="utf-8", capture_output=True,
            env=self.env(), timeout=15, check=False,
        )
        self.assertEqual(compact.returncode, 0, compact.stderr)
        self.assertNotIn(fake_sk, compact.stdout)
        self.assertNotIn(fake_gh, compact.stdout)
        self.assertNotIn("MIIEpAIBAAKCAQEAsecret", compact.stdout)

        # 3) Antigravity injectSteps mode
        agy_event = json.dumps({
            "conversationId": "conv-sec-1",
            "workspacePaths": [str(self.vault)],
            "hookEventName": "SessionStart",
        })
        agy = subprocess.run(
            [sys.executable, "hooks/session_start.py", "--host", "antigravity"],
            cwd=self.plugin, input=agy_event, text=True, encoding="utf-8", capture_output=True,
            env=self.env(), timeout=15, check=False,
        )
        self.assertEqual(agy.returncode, 0, agy.stderr)
        msg = json.loads(agy.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("Safe line 1", msg)
        self.assertNotIn(fake_sk, msg)
        self.assertNotIn(fake_gh, msg)
        self.assertNotIn("MIIEpAIBAAKCAQEAsecret", msg)

    def test_stop_without_execution_num_deduplicates_three_handlers_and_records_next_turn(self) -> None:
        (self.vault / "00-meta" / "handoff.md").write_text("Clean handoff\n", encoding="utf-8")
        (self.vault / "00-meta" / "hot.md").write_text("Clean hot\n", encoding="utf-8")

        turn1 = json.dumps({
            "conversationId": "conv-no-exec",
            "workspacePaths": [str(self.vault)],
            "hookEventName": "Stop",
            "fullyIdle": True,
            "lastUserInput": "Turn 1 prompt",
        }).encode("utf-8")
        workers = [
            subprocess.Popen(
                [sys.executable, "hooks/session_checkpoint.py", "--host", "antigravity"],
                cwd=self.plugin, env=self.env(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for _ in range(3)
        ]
        for w in workers:
            out, err = w.communicate(turn1, timeout=20)
            self.assertEqual((w.returncode, out, err), (0, b"{}\n", b""))

        records = list((self.vault / "00-meta" / ".agentic-vault" / "runtime").glob("checkpoint-*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(len(json.loads(records[0].read_text(encoding="utf-8"))["events"]), 1)

    def test_stop_with_execution_num_zero_records_distinct_turns_within_120_seconds(self) -> None:
        (self.vault / "00-meta" / "handoff.md").write_text("Clean handoff\n", encoding="utf-8")
        (self.vault / "00-meta" / "hot.md").write_text("Clean hot\n", encoding="utf-8")

        for turn_text in ("First user message", "Second user message 5s later"):
            ev = json.dumps({
                "conversationId": "conv-zero-exec",
                "workspacePaths": [str(self.vault)],
                "hookEventName": "Stop",
                "fullyIdle": True,
                "executionNum": 0,
                "lastUserInput": turn_text,
            }).encode("utf-8")
            # Simulate 3 handlers firing for each turn
            for _ in range(3):
                r = subprocess.run(
                    [sys.executable, "hooks/session_checkpoint.py", "--host", "antigravity"],
                    cwd=self.plugin, env=self.env(), input=ev, capture_output=True, timeout=20, check=False,
                )
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"{}\n", b""))

        records = list((self.vault / "00-meta" / ".agentic-vault" / "runtime").glob("checkpoint-*.json"))
        self.assertEqual(len(records), 1)
        # Exactly 2 checkpoints recorded (1 per turn, 0 duplicates across 3 handlers each).
        self.assertEqual(len(json.loads(records[0].read_text(encoding="utf-8"))["events"]), 2)


if __name__ == "__main__":
    unittest.main()
