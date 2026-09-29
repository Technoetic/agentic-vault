"""Optional tool adapters: the Aside assets and the read-only adapter report.

The report must only describe what is installed and what may be offered. These
tests pin its states and actions, keep the shipped assets generic (no personal
paths), and on Windows run the helper script far enough to prove that a hook
call never blocks and never acts on a command that is not an aside command.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
ASSETS = ROOT / "assets" / "adapters" / "aside"
HELPER = ASSETS / "aside-up.ps1"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_adapters
finally:
    sys.path.remove(str(SCRIPTS))


def _manifest_version() -> tuple[int, ...]:
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    return tuple(int(part) for part in data["version"].split("."))


class AdapterAssetTests(unittest.TestCase):
    def test_helper_is_ascii_and_stamped(self) -> None:
        raw = HELPER.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM: the file must stay ASCII")
        text = raw.decode("ascii")  # raises if a non-ASCII byte slipped in
        stamp = vault_adapters._stamp(text)
        self.assertIsNotNone(stamp)
        self.assertEqual(text.splitlines()[0], f"# agentic-vault:adapter aside-up engine={stamp}")
        self.assertLessEqual(vault_adapters._version(stamp), _manifest_version())
        for literal in ("[switch]$Hook", "Stop-With", "ConvertFrom-Json", "SW_MINIMIZE",
                        "Never launch a second time while the browser is up"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_clause_has_one_marker_pair_and_points_to_helper(self) -> None:
        text = (ASSETS / "browser-clause.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(vault_adapters.CLAUSE_BEGIN), 1)
        self.assertEqual(text.count(vault_adapters.CLAUSE_END), 1)
        self.assertTrue(text.startswith("<!-- " + vault_adapters.CLAUSE_BEGIN + " engine="))
        self.assertIn("00-meta/scripts/aside-up.ps1", text)
        self.assertIn("[[Aside CLI 운영 가이드]]", text)
        self.assertNotIn("agentic-vault:begin", text)  # stays outside the managed stub block

    def test_hook_block_is_filtered_to_aside_commands(self) -> None:
        data = json.loads((ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Bash|PowerShell")
        handlers = groups[0]["hooks"]
        self.assertEqual(sorted(h["if"] for h in handlers), ["Bash(aside *)", "PowerShell(aside *)"])
        for handler in handlers:
            with self.subTest(handler=handler["if"]):
                self.assertEqual(handler["type"], "command")
                self.assertIsInstance(handler["timeout"], int)
                self.assertIn('"$CLAUDE_PROJECT_DIR/00-meta/scripts/aside-up.ps1" -Hook', handler["command"])
        self.assertEqual(vault_adapters._hook_commands(data), [h["command"] for h in handlers])

    def test_guide_template_frontmatter_is_valid(self) -> None:
        text = (ASSETS / "operations-guide.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        front = text.split("---\n", 2)[1]
        self.assertLessEqual(len(front.splitlines()), 16)
        keys = {line.split(":", 1)[0] for line in front.splitlines() if ":" in line}
        for key in ("title", "type", "status", "ai_priority", "tags", "created", "updated"):
            with self.subTest(key=key):
                self.assertIn(key, keys)
        self.assertIn("type: tool", front)
        self.assertIn("status: draft", front)
        self.assertIn("created: {{DATE}}", front)
        self.assertNotRegex(front, r"(?<!\")\[\[")

    def test_assets_carry_no_personal_or_vault_specific_data(self) -> None:
        forbidden = re.compile(r"corei|D:[\\/]NS|ns-electric|jmj@|엔에스|\\Users\\", re.IGNORECASE)
        for path in sorted(ASSETS.iterdir()):
            with self.subTest(asset=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))


class AdapterReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.write("00-meta/vault-config.json", "{}")
        self.cli = mock.patch.object(vault_adapters.shutil, "which", return_value=None)
        self.platform = mock.patch.object(vault_adapters, "_platform", return_value="windows")
        self.which = self.cli.start()
        self.platform.start()
        self.addCleanup(self.cli.stop)
        self.addCleanup(self.platform.stop)

    def write(self, rel: str, text: str, newline: str = "\n") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        return path

    def report(self) -> dict:
        report, code = vault_adapters.diagnose(self.vault)
        self.assertEqual(code, 0)
        return report["adapters"]["aside"]

    def install_clause(self) -> None:
        clause = (ASSETS / "browser-clause.md").read_text(encoding="utf-8")
        self.write("CLAUDE.md", "# vault\n\n" + clause)

    def bundled_helper(self) -> str:
        return HELPER.read_text(encoding="utf-8")

    def test_not_a_vault(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        report, code = vault_adapters.diagnose(empty)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "not_vault")

    def test_nothing_installed_and_no_cli_offers_nothing(self) -> None:
        aside = self.report()
        self.assertFalse(aside["cli_on_path"])
        self.assertEqual(aside["helper"]["state"], "missing")
        self.assertEqual(aside["clause"]["state"], "missing")
        self.assertEqual(aside["hook"]["state"], "absent")
        self.assertEqual(aside["actions"], [])

    def test_cli_without_adapter_offers_install(self) -> None:
        self.which.return_value = "/usr/local/bin/aside"
        self.write("CLAUDE.md", "# vault\n")
        aside = self.report()
        self.assertEqual(aside["clause"]["state"], "absent")
        self.assertEqual(aside["actions"], ["offer_install"])

    def test_full_install_then_hook(self) -> None:
        self.which.return_value = "C:/aside/aside.exe"
        self.install_clause()
        self.write(vault_adapters.HELPER_REL, self.bundled_helper())
        self.write(vault_adapters.GUIDE_REL, "---\ntitle: x\n---\n")
        aside = self.report()
        self.assertEqual(aside["helper"]["state"], "current")
        self.assertEqual(aside["clause"]["state"], "present")
        self.assertEqual(aside["actions"], ["hook_optional"])
        hooks = (ASSETS / "settings-hooks.json").read_text(encoding="utf-8")
        self.write(".claude/settings.json", hooks)
        aside = self.report()
        self.assertEqual(aside["hook"]["state"], "configured")
        self.assertEqual(aside["hook"]["configured_in"], [".claude/settings.json"])
        self.assertEqual(aside["actions"], [])

    def test_crlf_copy_of_bundled_helper_is_current(self) -> None:
        self.write(vault_adapters.HELPER_REL, self.bundled_helper(), newline="\r\n")
        self.assertEqual(self.report()["helper"]["state"], "current")

    def test_helper_version_states(self) -> None:
        text = self.bundled_helper()
        bundled = vault_adapters._stamp(text)
        cases = {
            "outdated": (text.replace(f"engine={bundled}", "engine=0.1.0", 1), "offer_helper_update"),
            "newer": (text.replace(f"engine={bundled}", "engine=99.0.0", 1), "review_helper"),
            "modified": (text + "\n# local tweak\n", "review_helper"),
            "unstamped": (text.split("\n", 1)[1], "review_helper"),
        }
        for state, (content, action) in cases.items():
            with self.subTest(state=state):
                self.write(vault_adapters.HELPER_REL, content)
                aside = self.report()
                self.assertEqual(aside["helper"]["state"], state)
                self.assertIn(action, aside["actions"])
                self.assertNotIn("offer_install", aside["actions"])

    def test_clause_without_helper_on_windows_offers_helper_and_note(self) -> None:
        self.install_clause()
        aside = self.report()
        self.assertEqual(aside["actions"], ["offer_helper", "offer_guide_note"])

    def test_non_windows_never_offers_helper_or_hook(self) -> None:
        with mock.patch.object(vault_adapters, "_platform", return_value="macos"):
            self.install_clause()
            report, _ = vault_adapters.diagnose(self.vault)
        aside = report["adapters"]["aside"]
        self.assertFalse(aside["helper_supported"])
        self.assertEqual(aside["actions"], ["offer_guide_note"])

    def test_broken_clause_markers_are_reported(self) -> None:
        self.write("CLAUDE.md", "<!-- " + vault_adapters.CLAUSE_BEGIN + " -->\nno end marker\n")
        aside = self.report()
        self.assertEqual(aside["clause"]["state"], "broken_markers")
        self.assertIn("review_clause_markers", aside["actions"])

    def test_unreadable_settings_are_reported_not_parsed(self) -> None:
        self.write(".claude/settings.json", "{not json")
        aside = self.report()
        self.assertEqual(aside["hook"]["state"], "unreadable")
        self.assertIn("review_settings", aside["actions"])

    def test_linked_scripts_directory_is_not_followed(self) -> None:
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (outside / "aside-up.ps1").write_text(self.bundled_helper(), encoding="utf-8")
        (self.vault / "00-meta").mkdir(exist_ok=True)
        try:
            os.symlink(outside, self.vault / "00-meta" / "scripts", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        aside = self.report()
        self.assertEqual(aside["helper"]["state"], "unsafe_path")
        self.assertIn("review_helper", aside["actions"])

    def test_cli_prints_json(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vault_adapters.main(["--vault", str(self.vault), "--format", "json"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["adapters"]["aside"]["actions"], [])


def _powershell() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell") or shutil.which("pwsh")


def _aside_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Aside.exe"], capture_output=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # unknown: treat as running so the launch-path test skips
    return b"Aside.exe" in (out or b"")


@unittest.skipUnless(_powershell(), "Windows PowerShell is required")
class HelperScriptTests(unittest.TestCase):
    def run_helper(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(HELPER), *args]
        # The console may be in a legacy code page; decode leniently (the script itself is ASCII).
        return subprocess.run(command, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def test_script_parses(self) -> None:
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{HELPER}', [ref]$null, [ref]$e); $e.Count")
        out = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", check],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "0")

    def test_hook_ignores_commands_that_do_not_run_aside(self) -> None:
        for command in ("echo hello", "git status", "python aside_notes.py", "cat aside.txt"):
            with self.subTest(command=command):
                payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
                result = self.run_helper("-Hook", "-AsideExe", "C:/missing/Aside.exe", stdin=payload)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), "")

    def test_hook_never_blocks_even_when_aside_is_missing(self) -> None:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cd x && aside repl \"1\""}})
        result = self.run_helper("-Hook", "-AsideExe", "C:/missing/Aside.exe", "-TimeoutSec", "3",
                                 stdin=payload)
        self.assertEqual(result.returncode, 0)

    @unittest.skipIf(_aside_running(), "Aside is running on this machine")
    def test_interactive_call_reports_missing_app(self) -> None:
        result = self.run_helper("-AsideExe", "C:/missing/Aside.exe", "-TimeoutSec", "3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[aside] Aside.exe not found", result.stdout)

if __name__ == "__main__":
    unittest.main()
