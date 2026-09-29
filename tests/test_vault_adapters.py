"""Optional tool adapters: the Aside assets and the read-only adapter report.

The report must only describe what is installed and what may be offered. These
tests pin its states and actions, keep the shipped assets generic (no personal
paths), and on Windows run the helper script far enough to prove that a hook
call never blocks, acts only on commands that run aside, and never mistakes
the CLI process (also named aside.exe) for the browser. The helper runs with a
PATH that holds no aside CLI and with a missing ASIDE_EXE, so no test can start
the real app.
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
MISSING_EXE = "C:/missing/Aside.exe"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_adapters
finally:
    sys.path.remove(str(SCRIPTS))


def _manifest_core() -> tuple[int, ...]:
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", data["version"])
    assert match, data["version"]
    return tuple(int(part) for part in match.groups())


class AdapterAssetTests(unittest.TestCase):
    def test_helper_is_ascii_and_stamped(self) -> None:
        raw = HELPER.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM: the file must stay ASCII")
        text = raw.decode("ascii")  # raises if a non-ASCII byte slipped in
        stamp = vault_adapters._stamp(text)
        self.assertIsNotNone(stamp)
        self.assertRegex(stamp, vault_adapters.VERSION_RE)
        self.assertEqual(text.splitlines()[0], f"# agentic-vault:adapter aside-up engine={stamp}")
        self.assertLessEqual(vault_adapters._version(stamp), _manifest_core())
        for literal in ("[switch]$Hook", "[switch]$CheckOnly", "[ValidateRange(1, 45)]", "Stop-With",
                        "ConvertFrom-Json", "SW_MINIMIZE", "Local\\agentic-vault-aside-up",
                        "Test-IsBrowserProcess", "'Aside CLI'", "console.log(6*7)", "WaitForExit"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_clause_has_one_marker_pair_and_states_the_data_path(self) -> None:
        text = (ASSETS / "browser-clause.md").read_text(encoding="utf-8")
        self.assertEqual(len(vault_adapters.CLAUSE_BEGIN_RE.findall(text)), 1)
        self.assertEqual(len(vault_adapters.CLAUSE_END_RE.findall(text)), 1)
        self.assertTrue(text.startswith("<!-- " + vault_adapters.CLAUSE_BEGIN + " engine="))
        for literal in ("00-meta/scripts/aside-up.ps1", "[[Aside CLI 운영 가이드]]", "console.log",
                        "settings.local.json"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        self.assertNotIn("agentic-vault:begin", text)  # stays outside the managed stub block

    def test_hook_block_is_filtered_and_shell_neutral(self) -> None:
        data = json.loads((ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))
        groups = data["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Bash|PowerShell")
        handlers = groups[0]["hooks"]
        self.assertEqual(sorted(h["if"] for h in handlers), sorted(vault_adapters.HOOK_FILTERS))
        for handler in handlers:
            with self.subTest(handler=handler["if"]):
                command = handler["command"]
                self.assertEqual(handler["type"], "command")
                self.assertIsInstance(handler["timeout"], int)
                self.assertGreater(handler["timeout"], 45)  # the helper's own ceiling
                self.assertIn("$env:CLAUDE_PROJECT_DIR", command)
                self.assertIn("-Command '", command)  # single quotes: no outer shell expands it
                self.assertTrue(command.endswith("-Hook'"))
                self.assertIsNone(vault_adapters.SHELL_BOUND_VAR_RE.search(command))
        self.assertEqual(vault_adapters._hook_commands(data), [h["command"] for h in handlers])

    def test_guide_template_frontmatter_is_valid(self) -> None:
        text = (ASSETS / "operations-guide.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        _, front, body = text.split("---\n", 2)
        self.assertLessEqual(len(front.splitlines()), 16)
        keys = {line.split(":", 1)[0] for line in front.splitlines() if ":" in line}
        for key in ("title", "type", "status", "ai_priority", "tags", "created", "updated"):
            with self.subTest(key=key):
                self.assertIn(key, keys)
        self.assertIn("type: tool", front)
        self.assertIn("status: draft", front)
        self.assertIn("created: {{DATE}}", front)
        self.assertNotRegex(front, r"(?<!\")\[\[")
        self.assertRegex(body, r"\[\[[^\]]+\]\]")  # at least one wikilink: no orphan note

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

    def hook_settings(self, command: str | None = None, if_filter: object = "Bash(aside *)") -> str:
        handler: dict = {"type": "command",
                         "command": command or json.loads((ASSETS / "settings-hooks.json").read_text(
                             encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]}
        if if_filter is not None:
            handler["if"] = if_filter
        return json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [handler]}]}})

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
        self.write(".claude/settings.local.json", (ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))
        aside = self.report()
        self.assertEqual(aside["hook"]["state"], "configured")
        self.assertEqual(aside["hook"]["configured_in"], [".claude/settings.local.json"])
        self.assertEqual(aside["actions"], [])

    def test_crlf_and_bom_copies_of_bundled_helper_are_current(self) -> None:
        self.write(vault_adapters.HELPER_REL, self.bundled_helper(), newline="\r\n")
        self.assertEqual(self.report()["helper"]["state"], "current")
        self.write(vault_adapters.HELPER_REL, "\ufeff" + self.bundled_helper())
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

    def test_prerelease_or_garbled_stamp_is_unstamped(self) -> None:
        text = self.bundled_helper()
        bundled = vault_adapters._stamp(text)
        for stamp in ("0.17.0-rc.1", "latest", "0.17.x"):
            with self.subTest(stamp=stamp):
                self.write(vault_adapters.HELPER_REL, text.replace(f"engine={bundled}", f"engine={stamp}", 1))
                helper = self.report()["helper"]
                self.assertEqual(helper["state"], "unstamped")
                self.assertEqual(helper["installed_stamp"], stamp)

    def test_version_compare_pads_short_versions(self) -> None:
        self.assertEqual(vault_adapters._compare("0.17", "0.17.0"), 0)
        self.assertLess(vault_adapters._compare("0.9.0", "0.17.0"), 0)
        self.assertGreater(vault_adapters._compare("1.0", "0.17.9"), 0)

    def test_unreadable_helper_is_reviewed(self) -> None:
        path = self.vault / vault_adapters.HELPER_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe\x00bad")
        aside = self.report()
        self.assertEqual(aside["helper"]["state"], "unreadable")
        self.assertIn("review_helper", aside["actions"])

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

    def test_broken_clause_markers_block_install_offer(self) -> None:
        self.which.return_value = "C:/aside/aside.exe"
        self.write("CLAUDE.md", "<!-- " + vault_adapters.CLAUSE_BEGIN + " -->\nno end marker\n")
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform), mock.patch.object(vault_adapters, "_platform", return_value=platform):
                aside = vault_adapters.diagnose(self.vault)[0]["adapters"]["aside"]
                self.assertEqual(aside["clause"]["state"], "broken_markers")
                self.assertIn("review_clause", aside["actions"])
                self.assertNotIn("offer_install", aside["actions"])

    def test_marker_name_in_prose_is_not_a_marker(self) -> None:
        self.write("CLAUDE.md", "# vault\n\nThe `agentic-vault:adapter aside begin` marker is added by init.\n")
        self.assertEqual(self.report()["clause"]["state"], "absent")
        self.install_clause()
        text = (self.vault / "CLAUDE.md").read_text(encoding="utf-8")
        self.write("CLAUDE.md", text + "\nSee the `agentic-vault:adapter aside end` marker above.\n")
        self.assertEqual(self.report()["clause"]["state"], "present")

    def test_undecodable_claude_md_is_unreadable_not_missing(self) -> None:
        self.which.return_value = "C:/aside/aside.exe"
        (self.vault / "CLAUDE.md").write_bytes(b"\xff\xfe\x00\xd8broken")
        aside = self.report()
        self.assertEqual(aside["clause"]["state"], "unreadable")
        self.assertIn("review_clause", aside["actions"])
        self.assertNotIn("offer_install", aside["actions"])

    def test_unreadable_settings_are_reported_even_when_another_file_has_the_hook(self) -> None:
        self.write(".claude/settings.json", "{not json")
        aside = self.report()
        self.assertEqual(aside["hook"]["state"], "unreadable")
        self.assertIn("review_settings", aside["actions"])
        self.write(".claude/settings.local.json", self.hook_settings())
        aside = self.report()
        self.assertEqual(aside["hook"]["state"], "configured")
        self.assertEqual(aside["hook"]["unreadable"], [".claude/settings.json"])
        self.assertIn("review_settings", aside["actions"])

    def test_old_shell_bound_command_and_missing_filter_need_review(self) -> None:
        old = 'powershell -NoProfile -ExecutionPolicy Bypass -File "$CLAUDE_PROJECT_DIR/00-meta/scripts/aside-up.ps1" -Hook'
        braced = 'powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PROJECT_DIR}/00-meta/scripts/aside-up.ps1" -Hook'
        relative = 'powershell -NoProfile -ExecutionPolicy Bypass -File 00-meta/scripts/aside-up.ps1 -Hook'
        for command, if_filter in ((old, "Bash(aside *)"), (braced, "Bash(aside *)"), (relative, "Bash(aside *)"),
                                   (None, None), (None, "Bash(*)"), (None, ["Bash(aside *)"])):
            with self.subTest(command=command, if_filter=if_filter):
                self.write(".claude/settings.json", self.hook_settings(command, if_filter))
                aside = self.report()
                self.assertEqual(aside["hook"]["state"], "needs_review")
                self.assertTrue(aside["hook"]["problems"])
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

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    def test_junctioned_scripts_directory_is_not_followed(self) -> None:
        import _winapi
        outside = Path(self._tmp.name) / "outside-junction"
        outside.mkdir()
        (outside / "aside-up.ps1").write_text(self.bundled_helper(), encoding="utf-8")
        (self.vault / "00-meta").mkdir(exist_ok=True)
        _winapi.CreateJunction(str(outside), str(self.vault / "00-meta" / "scripts"))
        aside = self.report()
        self.assertEqual(aside["helper"]["state"], "unsafe_path")
        self.assertIn("review_helper", aside["actions"])

    def test_text_output_and_json_cli(self) -> None:
        self.write(".claude/settings.json", self.hook_settings(if_filter=None))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vault_adapters.main(["--vault", str(self.vault)])
        self.assertEqual(code, 0)
        lines = buffer.getvalue().splitlines()
        self.assertEqual(lines[0], "agentic-vault adapters: ok (platform=windows)")
        self.assertIn("aside.hook: needs_review", lines)
        self.assertTrue(any(line.startswith("aside.hook.problem: ") for line in lines))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vault_adapters.main(["--vault", str(self.vault), "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["schema_version"], 1)

    def test_cli_reports_not_vault_with_exit_2(self) -> None:
        empty = Path(self._tmp.name) / "plain"
        empty.mkdir()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vault_adapters.main(["--vault", str(empty)])
        self.assertEqual(code, 2)
        self.assertIn("not_vault", buffer.getvalue())


def _powershell() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell") or shutil.which("pwsh")


def _hermetic_env() -> dict:
    """Environment in which the helper can find neither the CLI nor the app."""
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep)
             if p and not (Path(p) / "aside.exe").exists() and not (Path(p) / "aside.cmd").exists()]
    env["PATH"] = os.pathsep.join(parts)
    env["ASIDE_EXE"] = MISSING_EXE
    return env


def _browser_running() -> bool:
    # Evaluated when the class is defined, also on hosts without PowerShell.
    if _powershell() is None:
        return False
    script = ("@(Get-Process -Name Aside -ErrorAction SilentlyContinue | Where-Object { "
              "-not $_.Path -or (Get-Item -LiteralPath $_.Path).VersionInfo.ProductName -eq 'Aside' }).Count")
    try:
        out = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=60).stdout
    except (OSError, TypeError, subprocess.SubprocessError):
        return True  # unknown: treat as running so the launch-path tests skip
    return (out or b"").strip() not in (b"", b"0")


@unittest.skipUnless(_powershell(), "Windows PowerShell is required")
class HelperScriptTests(unittest.TestCase):
    FALSE_POSITIVES = (
        "echo hello", "git status", "python aside_notes.py", "cat aside.txt",
        'git commit -m "set aside old notes"', "tasklist | grep -i aside.exe", "which aside",
        "Get-Command aside", "Stop-Process -Name Aside", "taskkill //IM aside.exe //F",
        "rg -n aside 20-knowledge", 'echo "put it aside for now"', "grep -rn aside .",
        "echo 한국어 aside 설명",
    )
    TRUE_POSITIVES = (
        "aside repl \"1\"", "ASIDE repl 1", "aside.exe repl 1", "& aside repl 1",
        "cd x && aside repl \"1\"", "echo x; aside repl 1", "$o = (aside repl \"1\")",
        "FOO=1 aside repl 1", "C:/tools/aside.exe repl 1",
        "\"C:\\Program Files\\Aside CLI\\aside.exe\" repl 1", ".\\aside.exe repl 1",
        "aside \"search the web\"", "x | aside repl 1",
        "aside \"한국어로 검색\"", "aside \"한국어\" && echo 끝",
    )

    def run_helper(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(HELPER), *args]
        # The console may be in a legacy code page; decode leniently (the script itself is ASCII).
        return subprocess.run(command, input=stdin, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, env=_hermetic_env())

    @staticmethod
    def payload(command: str, tool: str = "Bash") -> str:
        return json.dumps({"tool_name": tool, "tool_input": {"command": command}})

    def test_script_parses(self) -> None:
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{HELPER}', [ref]$null, [ref]$e); $e.Count")
        out = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", check],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "0")

    def test_hook_classifies_commands_by_position(self) -> None:
        cases = [(c, "other-command") for c in self.FALSE_POSITIVES] + \
                [(c, "aside-command") for c in self.TRUE_POSITIVES]
        for command, expected in cases:
            with self.subTest(command=command):
                result = self.run_helper("-Hook", "-CheckOnly", stdin=self.payload(command))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), expected)

    def test_check_only_without_hook_only_classifies(self) -> None:
        result = self.run_helper("-CheckOnly", stdin=self.payload("aside repl 1"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "aside-command")

    def test_hook_ignores_commands_that_do_not_run_aside(self) -> None:
        for command in self.FALSE_POSITIVES[:6]:
            with self.subTest(command=command):
                result = self.run_helper("-Hook", "-AsideExe", MISSING_EXE, stdin=self.payload(command))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.strip(), "")

    def test_hook_never_blocks_even_when_aside_is_missing(self) -> None:
        result = self.run_helper("-Hook", "-AsideExe", MISSING_EXE, "-TimeoutSec", "3",
                                 stdin=self.payload("cd x && aside repl \"1\""))
        self.assertEqual(result.returncode, 0)

    def test_out_of_range_timeout_is_rejected(self) -> None:
        result = self.run_helper("-TimeoutSec", "90", "-AsideExe", MISSING_EXE)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[aside] started", result.stdout)

    @unittest.skipIf(_browser_running(), "an Aside browser is running on this machine")
    def test_interactive_call_reports_missing_app(self) -> None:
        result = self.run_helper("-AsideExe", MISSING_EXE, "-TimeoutSec", "3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[aside] Aside.exe not found", result.stdout)

    @unittest.skipIf(_browser_running(), "an Aside browser is running on this machine")
    def test_cli_named_aside_is_not_taken_for_the_browser(self) -> None:
        ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
        if not ping.exists():
            self.skipTest("PING.EXE is not available")
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "aside.exe"
            shutil.copyfile(ping, fake)
            proc = subprocess.Popen([str(fake), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                result = self.run_helper("-AsideExe", MISSING_EXE, "-TimeoutSec", "3")
            finally:
                proc.kill()
                proc.wait(timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[aside] Aside.exe not found", result.stdout)
        self.assertNotIn("browser", result.stdout)

    @unittest.skipIf(_browser_running(), "an Aside browser is running on this machine")
    def test_explicit_path_to_the_cli_is_refused(self) -> None:
        cli = shutil.which("aside")
        if not cli or not cli.lower().endswith(".exe"):
            self.skipTest("the Aside CLI is not installed")
        result = self.run_helper("-AsideExe", cli, "-TimeoutSec", "3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is the Aside CLI, not the Aside browser app", result.stdout)


if __name__ == "__main__":
    unittest.main()
