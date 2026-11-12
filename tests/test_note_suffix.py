"""`hot_note` and `handoff_note` must name a Markdown note.

SessionStart injects the two notes named by these keys. A value such as `.env`
would put that file into every session, so `validate_config` refuses any
non-empty value whose suffix is not `.md` (any letter case), as
`vault_state.py` already does for its notes. The refusal goes through the
existing configuration-error paths: the full check stops with exit 1, the
session hook injects nothing, Doctor marks the section unsafe and the staged
commit gate blocks. The message names the key, never the value.

Temporary roots are resolved, so a path such as `/var/...` on macOS is the
same `/private/var/...` the scripts see; Git runs with the user's settings cut
off (`isolate_git`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
DOCTOR = SCRIPTS / "vault_doctor.py"
SESSION_HOOK = ROOT / "hooks" / "session_start.py"
CONFIG = "00-meta/vault-config.json"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
finally:
    sys.path.remove(str(SCRIPTS))

KEYS = ("hot_note", "handoff_note")
REJECTED = (".env", "00-meta/hot.txt", "00-meta/hot", "00-meta/hot.md.bak", "notes/.md")
ACCEPTED = ("", "00-meta/hot.md", "00-meta/HOT.MD", "x/y.Md")
MARKER = "SECRET_ENV_MARKER"
SESSION_DIAGNOSTIC = "agentic-vault: invalid session context configuration"


def message(key: str) -> str:
    return f"{key} must name a Markdown note (.md)"


def run(arguments: list[str], cwd: Path, env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", *arguments], cwd=cwd, env=env,
        input='{"source":"startup"}', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=False, timeout=60,
    )


def without_claude(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("CLAUDE_")}
    env.update(extra)
    return env


class ValidateConfigTests(unittest.TestCase):
    def test_validate_config_requires_md_for_hot_and_handoff(self) -> None:
        for key in KEYS:
            for value in REJECTED:
                with self.subTest(key=key, rejected=value):
                    with self.assertRaises(vault_healthcheck.HealthcheckError) as caught:
                        vault_healthcheck.validate_config({key: value})
                    self.assertEqual(str(caught.exception), message(key))
                    self.assertNotIn(value, str(caught.exception))
            for value in ACCEPTED:
                with self.subTest(key=key, accepted=value):
                    self.assertEqual(vault_healthcheck.validate_config({key: value})[key], value)


class _VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_vault(self, key: str) -> None:
        """A vault whose `key` names `.env`; the other note is a valid Markdown note."""
        config = {"hot_note": "00-meta/hot.md", "handoff_note": "00-meta/handoff.md",
                  "deny_zones": ["90-assets"], key: ".env"}
        self.write(CONFIG, json.dumps(config, ensure_ascii=False))
        self.write(".env", f"TOKEN={MARKER}\n")
        self.write("00-meta/hot.md", "Hot context.\n")
        self.write("00-meta/handoff.md", "Handoff.\n")


class FailClosedTests(_VaultCase):
    def test_healthcheck_cli_fails_closed(self) -> None:
        for key in KEYS:
            with self.subTest(key=key):
                self.write_vault(key)
                result = run([str(HEALTHCHECK), "--vault", str(self.vault)], self.vault,
                             without_claude())
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    f"[vault-healthcheck] 오류: {CONFIG} 파싱 실패 — {message(key)}",
                    result.stderr)
                self.assertNotIn(MARKER, result.stdout + result.stderr)

    def test_session_start_injects_nothing(self) -> None:
        entries = (
            ("claude", [], self.root, {"CLAUDE_PROJECT_DIR": str(self.vault)}),
            ("codex", [], self.vault, {}),
            ("codex explicit", ["--vault", str(self.vault)], self.root, {}),
        )
        for key in KEYS:
            self.write_vault(key)
            for label, arguments, cwd, extra in entries:
                with self.subTest(key=key, entry=label):
                    result = run([str(SESSION_HOOK), *arguments], cwd, without_claude(**extra))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(MARKER, result.stdout + result.stderr)
                    self.assertNotIn("HOT CONTEXT", result.stdout)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr.strip(), SESSION_DIAGNOSTIC)

    def test_doctor_reports_invalid_config(self) -> None:
        for key, section in (("hot_note", "hot"), ("handoff_note", "handoff")):
            with self.subTest(key=key):
                self.write_vault(key)
                result = run([str(DOCTOR), "--vault", str(self.vault), "--format", "json"],
                             self.root, without_claude())
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "invalid_config")
                self.assertEqual(report["reason_code"], "section_fatal")
                self.assertEqual(report["sections"][section]["status"], "unsafe_path")
                self.assertNotIn(MARKER, result.stdout + result.stderr)


class StagedGateTests(_VaultCase):
    def setUp(self) -> None:
        super().setUp()
        isolate_git(self, ceiling=self.root)

    def git(self, *arguments: str) -> None:
        result = subprocess.run(["git", *arguments], cwd=self.vault, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staged_gate_refuses_a_bad_staged_config(self) -> None:
        self.git("init", "-q")
        for key in KEYS:
            with self.subTest(key=key):
                self.write_vault(key)
                self.git("add", "--", CONFIG)
                result = run([str(HEALTHCHECK), "--vault", str(self.vault), "--staged"],
                             self.vault)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("staged 오류", result.stderr)
                self.assertIn(message(key), result.stderr)
                self.assertNotIn(MARKER, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
