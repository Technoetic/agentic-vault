"""Opt-in deny-zone guard: decisions, the hook contract and the default install.

assets/adapters/deny-guard/deny-guard.py is a standalone script that a user
copies into a vault and runs from a Claude Code PreToolUse hook. These tests
load it from the plugin assets and give it synthetic hook inputs only, against
a temporary vault: the decision table per tool, case folding, MSYS paths, a
junction into a zone, the guard's own controls, and the process contract (exit
0, only deny or ask and never allow, nothing without CLAUDE_PROJECT_DIR). The
PowerShell branch is tested twice: with a stand-in for its parser on every
system, and on Windows with the real ps-parse.ps1 child process. The last
class pins that the default install does not change: no PreToolUse hook in
hooks/hooks.json and the permission and config templates as in v0.31.0.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "adapters" / "deny-guard"
GUARD = ASSETS / "deny-guard.py"
PARSER = ASSETS / "ps-parse.ps1"
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
TEMPLATE_ZONES = ("10-inbox/_processed", "20-knowledge/_archive", "50-projects/_completed",
                  "90-assets", ".obsidian")
HOOK_MATCHER = "Read|Grep|Glob|Edit|Write|NotebookEdit|Bash|PowerShell"
# SHA-256 of the v0.31.0 blobs (21c4b47) with LF line ends. A Windows checkout
# may write them with CRLF, so the test hashes the file with CRLF folded to LF.
V0310_TEMPLATE_SHA256 = {
    "assets/templates/vault-config.json": "f93011367fa87bde599b8f8174447302ace8dadb2239cf5d31a24f28571c786b",
    "assets/templates/settings-permissions.json": "54ae16fa421b9759e27b3bdc51b902ea94a549ee490aaee22a299f50cc50ae6a",
}
_LOADED: dict = {}


def load_guard():
    """Import the asset script (its file name has a hyphen) once per test run."""
    if "guard" not in _LOADED:
        spec = importlib.util.spec_from_file_location("deny_guard_asset", GUARD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED["guard"] = module
    return _LOADED["guard"]


def msys_form(path: Path) -> str:
    """D:\\x\\y -> /d/x/y, the form Git Bash commands use."""
    text = str(path).replace("\\", "/")
    return "/" + text[0].lower() + text[2:]


def _windows_powershell() -> str | None:
    """powershell.exe in the Windows folder, the only place the guard starts it from; else None."""
    if os.name != "nt":
        return None
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if not root:
        return None
    path = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(path) if path.is_file() else None


def facts(**fields) -> dict:
    """A ps-parse.ps1 answer with nothing in it; keyword fields replace the empty values."""
    answer = {"errors": 0, "commands": [], "strings": [], "expandable": 0, "expandable_strings": [], "flags": []}
    answer.update(fields)
    return answer


class GuardCase(unittest.TestCase):
    zones: tuple = TEMPLATE_ZONES

    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "vault"
        for rel in ("00-meta/scripts", "10-inbox/_processed", "20-knowledge/_archive", "40-people",
                    "90-assets", ".obsidian", ".claude", "notes"):
            (self.vault / rel).mkdir(parents=True, exist_ok=True)
        self.write_config({"deny_zones": list(self.zones)})
        self.warnings: list[str] = []

    def write_config(self, data: object, raw: str | None = None) -> None:
        text = raw if raw is not None else json.dumps(data, ensure_ascii=False)
        (self.vault / "00-meta" / "vault-config.json").write_text(text, encoding="utf-8")

    def path(self, *parts: str) -> str:
        return str(self.vault.joinpath(*parts))

    def decide(self, tool: str, tool_input: object, cwd: str | None = None) -> str | None:
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
        if cwd is not None:
            payload["cwd"] = cwd
        result = self.guard.evaluate(payload, str(self.vault), self.warnings.append)
        if result is None:
            return None
        decision, reason = result
        self.assertIn(decision, ("deny", "ask"))
        output = json.dumps(self.guard.hook_output(decision, reason))
        self.assertNotIn("allow", output.lower())  # the guard can only narrow
        return decision

    def assert_table(self, tool: str, cases: list, cwd: str | None = None) -> None:
        for tool_input, expected in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.decide(tool, tool_input, cwd), expected)


class PathToolDecisionTests(GuardCase):
    def test_file_tools_deny_targets_inside_a_zone(self) -> None:
        self.assert_table("Read", [
            ({"file_path": self.path("90-assets", "a.png")}, "deny"),
            ({"file_path": self.path("notes", "90-assets", "a.png")}, "deny"),  # one segment: any depth
            ({"file_path": self.path("10-inbox", "_processed", "a.md")}, "deny"),
            ({"file_path": self.path("10-inbox", "_processed")}, "deny"),
            ({"file_path": self.path("notes", "10-inbox", "_processed", "a.md")}, None),  # longer zone: from the root
            ({"file_path": self.path("10-inbox", "a.md")}, None),
            ({"file_path": self.path("90-assets-old", "a.md")}, None),
            ({"file_path": self.path("notes", "a.md")}, None),
            ({"file_path": str(self.base / "outside" / "90-assets" / "a.png")}, None),  # not in the vault
            ({"file_path": self.path("00-meta", "vault-config.json")}, None),  # reading a control is fine
        ])
        self.assert_table("Edit", [
            ({"file_path": self.path(".obsidian", "workspace.json"), "old_string": "a", "new_string": "b"}, "deny"),
            ({"file_path": self.path("notes", "a.md"), "old_string": "a", "new_string": "b"}, None),
        ])
        self.assert_table("Write", [
            ({"file_path": self.path("20-knowledge", "_archive", "x.md"), "content": "x"}, "deny"),
            ({"file_path": self.path("20-knowledge", "x.md"), "content": "x"}, None),
        ])

    def test_notebook_edit_scans_every_path_field(self) -> None:
        self.assert_table("NotebookEdit", [
            ({"notebook_path": self.path("90-assets", "x.ipynb"), "new_source": "x"}, "deny"),
            ({"notebook_path": self.path("notes", "x.ipynb"), "new_source": "x"}, None),
            ({"target_path": self.path("50-projects", "_completed", "x.ipynb")}, "deny"),
            ({"new_source": "x", "cell_id": "c1"}, "ask"),  # no path field: an unknown shape
        ])

    def test_unknown_input_shapes_ask(self) -> None:
        self.assert_table("Read", [({}, "ask"), ({"file_path": 42}, "ask"), ({"file_path": ""}, "ask")])
        self.assert_table("Glob", [({"path": str(self.vault)}, "ask")])  # pattern is required
        self.assert_table("Grep", [({"pattern": "x", "glob": ["*.md"]}, "ask")])
        self.assertEqual(self.decide("Read", "not an object"), "ask")
        result = self.guard.evaluate("not json", str(self.vault))
        self.assertEqual(result[0], "ask")

    def test_glob_checks_path_and_pattern(self) -> None:
        vault = str(self.vault)
        self.assert_table("Glob", [
            ({"pattern": "90-assets/**", "path": vault}, "deny"),
            ({"pattern": "**/90-assets/*.png", "path": vault}, "deny"),
            ({"pattern": "*.png", "path": self.path("90-assets")}, "deny"),
            ({"pattern": self.path("90-assets") + "/*.png"}, "deny"),  # an absolute pattern
            ({"pattern": "10-inbox/_processed/*.md"}, "deny"),  # relative to the working directory
            ({"pattern": "**/_processed/*", "path": vault}, "ask"),  # the wildcard may reach the zone
            ({"pattern": "{90-assets,notes}/*.png", "path": vault}, "ask"),
            ({"pattern": "**/90-assets/**", "path": str(self.base)}, "ask"),  # starts above the vault
            ({"pattern": "**/90-assets/**", "path": str(self.base / "elsewhere")}, None),
            ({"pattern": "**/*.md", "path": vault}, None),  # walks through zones: a documented limit
            ({"pattern": "notes/*.md"}, None),
            ({"pattern": "**/90-assets-old/*"}, None),  # a literal name that only contains a zone name
        ])

    def test_grep_checks_path_and_glob_but_not_the_search_text(self) -> None:
        self.assert_table("Grep", [
            ({"pattern": "x", "path": self.path("90-assets")}, "deny"),
            ({"pattern": "x", "path": self.path("90-assets", "a.txt")}, "deny"),
            ({"pattern": "x", "glob": "90-assets/**"}, "deny"),
            ({"pattern": "x", "path": self.path("notes"), "glob": "*.md"}, None),
            ({"pattern": "90-assets", "path": self.path("notes")}, None),  # text to find, not a path
            ({"pattern": "x", "glob": "!90-assets/**"}, None),  # an exclusion glob
            ({"pattern": "x"}, None),  # no path: a documented limit
        ])

    def test_edits_to_guard_controls_ask(self) -> None:
        home_settings = str(self.base / "home" / ".claude" / "settings.json")
        self.assert_table("Write", [
            ({"file_path": self.path("00-meta", "vault-config.json"), "content": "{}"}, "ask"),
            ({"file_path": self.path(".claude", "settings.json"), "content": "{}"}, "ask"),
            ({"file_path": self.path("00-meta", "scripts", "deny-guard.py"), "content": ""}, "ask"),
            ({"file_path": home_settings, "content": "{}"}, "ask"),  # user settings outside the vault
            ({"file_path": self.path(".claude", "rules", "vault-workflow.md"), "content": "x"}, None),
            ({"file_path": self.path("00-meta", "hot.md"), "content": "x"}, None),
        ])
        self.assert_table("Edit", [
            ({"file_path": self.path(".claude", "settings.local.json"), "old_string": "a", "new_string": "b"}, "ask"),
            ({"file_path": self.path(".CLAUDE", "Settings.Local.JSON"), "old_string": "a", "new_string": "b"}, "ask"),
        ])
        self.assert_table("NotebookEdit", [({"notebook_path": self.path("00-meta", "vault-config.json")}, "ask")])
        self.assert_table("Read", [({"file_path": self.path(".claude", "settings.local.json")}, None)])

    def test_the_powershell_parser_is_a_guard_control(self) -> None:
        # ps-parse.ps1 decides what PowerShell commands contain, so changing it can switch the guard off.
        parser = self.path("00-meta", "scripts", "ps-parse.ps1")
        self.assert_table("Write", [({"file_path": parser, "content": ""}, "ask")])
        self.assert_table("Edit", [({"file_path": parser, "old_string": "a", "new_string": "b"}, "ask")])
        self.assert_table("Read", [({"file_path": parser}, None)])
        for command in ("cat 00-meta/scripts/ps-parse.ps1", "echo x > 00-meta/scripts/PS-Parse.ps1",
                        "cp notes/x.ps1 00-meta/scripts/ps-parse.ps1"):
            with self.subTest(command=command):
                self.assertEqual(self.decide("Bash", {"command": command}), "ask")

    def test_case_is_folded_for_zones(self) -> None:
        self.assert_table("Read", [
            ({"file_path": self.path("90-ASSETS", "a.png")}, "deny"),
            ({"file_path": self.path("10-INBOX", "_Processed", "a.md")}, "deny"),
            ({"file_path": self.path(".Obsidian", "app.json")}, "deny"),
        ])

    def test_unrelated_tools_get_no_decision(self) -> None:
        for tool in ("WebFetch", "Task", "MultiEdit", "mcp__x__read"):
            with self.subTest(tool=tool):
                self.assertIsNone(self.decide(tool, {"file_path": self.path("90-assets", "a.png")}))


class BashDecisionTests(GuardCase):
    DENY = (
        "cat 90-assets/a.png", "ls 90-assets", "cat notes/90-assets/a.png", "head -c 10 10-inbox/_processed/a.md",
        "cat .OBSIDIAN/app.json", "echo a>90-assets/b", 'cat "90-assets/x y.png" | head',
        "cat 90-assets/*.png", "cat */90-assets/x", "cat $(echo 90-assets)/x", "X=90-assets; cat $X/a",
        'cat 90-ass"ets"/x', "cat 90-ass\\ets/x", "cd 90-assets", "mv 90-assets/a.png notes/",
        "cp notes/a.md 90-assets/", "echo 90-assets | xargs cat", "echo 90-assets",
        "echo 90-assets 2>/dev/null | xargs cat",  # 2> moves stderr only, so the text still goes to xargs
        "cat x#y 90-assets/a", "mkdir -p x # note\ncat 90-assets/a.png", "f() { cat 90-assets/x; }; f",
        "tar -czf b.tgz --directory=90-assets .", "mv -vt 10-inbox/_processed/2026-10 10-inbox/a.md",
        # Options that read a file or take no value do not exempt the next word.
        "tar --exclude-from=90-assets/list.txt -czf b.tgz notes", "git ls-files --exclude-standard 90-assets",
        "rg --no-ignore foo 90-assets",
        # A search word that equals a zone path is denied too; the reason points to the Grep tool.
        "grep -rn 90-assets notes",
    )
    ASK = (
        "cat $HOME/x/90-assets-old", "bash -c 'cat notes/90-assets-x'", "python -c \"open('90-assets/a')\"",
        'eval "cat 9""0-assets/x"', "cat `echo 90-assets`/x", "cat {90-assets,x}/a",
        "cd notes && cat ../90-assets/x", "echo a\ncd notes\ncat ../90-assets/a",
        "echo a # comment\ncd notes\ncat ../90-assets/a", "cat 'unterminated 90-assets",
        "echo hi # 90-assets", "cat ~other/90-assets/x", "cat 00-meta/vault-config.json",
        "sed -i s/x/y/ .claude/settings.local.json", "cat ~/.claude/settings.json", "cat .claude/*.json",
        "sed -i s/a/b/ .claude/settings{,.local}.json", "python 00-meta/scripts/deny-guard.py", "rm -rf .claude",
        # Inline code that names a control is code the guard cannot read.
        "python -c \"p='00-meta/vault-config.json'; open(p, 'w').write('{}')\"",
        "echo '{}' > .claude/settings.local.json", "mv x.json .claude/settings.local.json",
    )
    NONE = (
        "git status", "ls", "cat notes/a.md", "cat notes/90-assets-old.md", "ls 90-assets-old",
        "cat .claude/rules/vault-workflow.md",
        # The vault rules quarantine processed files into a zone with a shell move.
        "mv -n 10-inbox/a.md 10-inbox/_processed/2026-10/",
        "mkdir -p 10-inbox/_processed/2026-10 && mv -n 10-inbox/a.md 10-inbox/_processed/2026-10/",
        "mv -t 10-inbox/_processed/2026-10 10-inbox/a.md 10-inbox/b.md",
        "mv --target-directory=20-knowledge/_archive 20-knowledge/old.md",
        "git mv 20-knowledge/old.md 20-knowledge/_archive/",
        'for f in 10-inbox/*.md; do mv -n "$f" 10-inbox/_processed/2026-10/; done',
        'if [ -f a.md ]; then mv -n a.md 10-inbox/_processed/2026-10/; fi',
        'mkdir -p "10-inbox/_processed/$(date +%Y-%m)"',
        # Text, commit messages and exclusions name a zone without opening it.
        "echo '90-assets/' >> .gitignore", "printf '%s\\n' .obsidian/ >> .gitignore",
        "echo 90-assets/ 1>> .gitignore", "echo 90-assets/ &> notes/zones.txt",
        "git commit -m \"$(cat <<'EOF'\nMove 10-inbox/a.md to 10-inbox/_processed/2026-10/\nEOF\n)\"",
        "git commit -m 'archive 20-knowledge/_archive/old.md'",
        "grep -rn foo . --exclude-dir=90-assets --exclude-dir .obsidian",
        "rg -g '!90-assets/**' foo", "tar --exclude=90-assets -czf b.tgz notes",
    )

    def test_decision_table(self) -> None:
        for expected, commands in (("deny", self.DENY), ("ask", self.ASK), (None, self.NONE)):
            for command in commands:
                with self.subTest(command=command):
                    self.assertEqual(self.decide("Bash", {"command": command}), expected)

    def test_working_directory_from_the_hook_input_is_used(self) -> None:
        self.assertEqual(self.decide("Bash", {"command": "cat _processed/a.md"}, self.path("10-inbox")), "deny")
        self.assertEqual(self.decide("Bash", {"command": "cat ../_processed/a.md"}, self.path("10-inbox", "sub")),
                         "deny")
        self.assertIsNone(self.decide("Bash", {"command": "cat ../_processed/a.md"}, self.path("notes")))
        self.assertEqual(self.decide("Bash", {"command": "cat ../90-assets/a.png"}, self.path("notes")), "deny")
        self.assertEqual(self.decide("Bash", {"command": "ls"}, self.path("90-assets")), "ask")
        self.assertEqual(self.decide("Bash", {"command": "cat a.png"}, self.path("90-assets", "sub")), "ask")

    def test_deny_reason_names_the_zone_and_the_alternatives(self) -> None:
        decision, reason = self.guard.evaluate({"tool_name": "Bash", "tool_input": {"command": "cat 90-assets/a"}},
                                               str(self.vault))
        self.assertEqual(decision, "deny")
        for literal in ("'90-assets'", "deny_zones", "00-meta/vault-config.json", "mv", "mkdir", "Grep tool"):
            with self.subTest(literal=literal):
                self.assertIn(literal, reason)

    def test_missing_command_asks(self) -> None:
        self.assertEqual(self.decide("Bash", {}), "ask")
        self.assertEqual(self.decide("Bash", {"command": ["cat", "90-assets/a"]}), "ask")


class PowerShellDecisionTests(GuardCase):
    """The PowerShell branch with a stand-in for ps-parse.ps1, so the mapping is tested on every system."""

    def stand_in(self, answer: dict | str, calls: list):
        """A parse_powershell that records its input and returns answer (facts, or a failure text)."""
        def parse(command: str):
            calls.append(command)
            return (answer, "") if isinstance(answer, dict) else (None, answer)
        return parse

    def decide_with(self, command: str, answer: dict | str, cwd: str | None = None) -> tuple[str | None, list]:
        calls: list[str] = []
        with mock.patch.object(self.guard, "parse_powershell", self.stand_in(answer, calls)):
            return self.decide("PowerShell", {"command": command}, cwd), calls

    def reason_with(self, command: str, answer: dict | str) -> str:
        with mock.patch.object(self.guard, "parse_powershell", self.stand_in(answer, [])):
            payload = {"tool_name": "PowerShell", "tool_input": {"command": command}}
            return self.guard.evaluate(payload, str(self.vault))[1]

    def test_commands_without_zone_or_control_names_start_no_parser(self) -> None:
        for command in ("Get-ChildItem", "Get-Content notes/a.md", "Get-ChildItem -Recurse -Filter *.md",
                        "Get-Content (Join-Path 90 -assets)"):  # the measured pre-filter bypass
            with self.subTest(command=command):
                self.assertEqual(self.decide_with(command, facts()), (None, []))
        # A working directory inside a zone asks without a parser run.
        self.assertEqual(self.decide_with("Get-ChildItem", facts(), self.path(".obsidian")), ("ask", []))

    def test_literal_zone_paths_deny(self) -> None:
        target = self.path("90-assets", "a.png")
        cases = [
            ("Get-Content 90-assets/a.png", facts(commands=["Get-Content"], strings=["Get-Content", "90-assets/a.png"])),
            ("Get-Content 10-inbox/_processed/a.md", facts(strings=["Get-Content", "10-inbox/_processed/a.md"])),
            (f"Get-Content '{target}'", facts(strings=["Get-Content", target])),
            (f"Get-Content FileSystem::{target}", facts(strings=["Get-Content", f"FileSystem::{target}"])),
            (f"Get-Content Microsoft.PowerShell.Core\\FileSystem::{target}",
             facts(strings=["Get-Content", f"Microsoft.PowerShell.Core\\FileSystem::{target}"])),
            # The literal folders in front of a variable are enough.
            ('Get-Content "90-assets/$n.png"',
             facts(strings=["Get-Content"], expandable=1, expandable_strings=["90-assets/$n.png"], flags=["expression"])),
            # A literal zone path wins over the run-time parts and over a parse error.
            ("& { Get-Content 90-assets/a.png }",
             facts(commands=[None, "Get-Content"], strings=["Get-Content", "90-assets/a.png"],
                   flags=["expression", "script_block_call"])),
            ("Get-Content notes/a.md && Get-Content 90-assets/a.png",
             facts(errors=1, strings=["Get-Content", "notes/a.md", "Get-Content", "90-assets/a.png"])),
            # None of the Bash exceptions: a move into a zone and an exclude value are denied too.
            ("Move-Item 10-inbox/a.md 10-inbox/_processed/2026-10/",
             facts(commands=["Move-Item"], strings=["Move-Item", "10-inbox/a.md", "10-inbox/_processed/2026-10/"])),
            ("Get-ChildItem -Recurse -Exclude 90-assets", facts(strings=["Get-ChildItem", "90-assets"])),
            ("tool --exclude=90-assets", facts(strings=["tool", "--exclude=90-assets"])),
        ]
        for command, answer in cases:
            with self.subTest(command=command):
                self.assertEqual(self.decide_with(command, answer), ("deny", [command]))
        # A drive-qualified path stays fixed when the command also changes the location.
        moved = facts(commands=["Set-Location", "Get-Content"], strings=["Set-Location", "notes", "Get-Content", target])
        self.assertEqual(self.decide_with(f"Set-Location notes; Get-Content '{target}'", moved)[0], "deny")
        # Relative paths start in the hook's working directory.
        relative = facts(strings=["Get-Content", "_processed/a.md"])
        self.assertEqual(self.decide_with("Get-Content _processed/a.md", relative, self.path("10-inbox"))[0], "deny")
        self.assertIsNone(self.decide_with("Get-Content _processed/a.md", relative)[0])

    def test_deny_reason_names_the_zone_and_the_bash_and_grep_tools(self) -> None:
        reason = self.reason_with("Get-Content 90-assets/a", facts(strings=["Get-Content", "90-assets/a"]))
        for literal in ("PowerShell", "'90-assets'", "deny_zones", "00-meta/vault-config.json", "mv", "Bash tool",
                        "Grep tool"):
            with self.subTest(literal=literal):
                self.assertIn(literal, reason)

    def test_unclear_commands_ask_and_say_why(self) -> None:
        cases = [  # (command, parser answer, words in the ask reason)
            ('Get-Content "unterminated 90-assets', facts(errors=1, strings=["Get-Content", "unterminated 90-assets"]),
             "does not parse"),
            ("iex 'gc 90-assets-old/a'",
             facts(commands=["iex"], strings=["iex", "gc 90-assets-old/a"], flags=["invoke_expression"]),
             "Invoke-Expression"),
            ("Start-Process notepad 90-assets-old/a",
             facts(strings=["Start-Process", "notepad", "90-assets-old/a"], flags=["start_process"]), "Start-Process"),
            ("icm { gci } 90-assets-old", facts(strings=["icm", "gci", "90-assets-old"],
                                                flags=["expression", "invoke_command"]), "Invoke-Command"),
            ("& $tool 90-assets-old", facts(commands=[None], strings=["90-assets-old"],
                                            flags=["expression", "script_block_call"]), "& or ."),
            (". ./x.ps1 90-assets-old", facts(commands=["./x.ps1"], strings=["./x.ps1", "90-assets-old"],
                                              flags=["dot_source"]), "dot-sourcing"),
            ("powershell -enc AA== 90-assets-old", facts(commands=["powershell"],
                                                         strings=["powershell", "AA==", "90-assets-old"],
                                                         flags=["encoded_command"]), "-EncodedCommand"),
            ('cmd /c "type 90-assets-old\\a"', facts(commands=["cmd"], strings=["cmd", "/c", "type 90-assets-old\\a"]),
             "another shell or interpreter"),
            ('Write-Output "$x 90-assets-old"',
             facts(strings=["Write-Output"], expandable=1, expandable_strings=["$x 90-assets-old"], flags=["expression"]),
             "at run time"),
            ("Get-Content Env:90-assets", facts(strings=["Get-Content", "Env:90-assets"], flags=["provider_path"]),
             "provider path"),
            ("Get-Item Registry::HKLM\\x\\90-assets",
             facts(strings=["Get-Item", "Registry::HKLM\\x\\90-assets"], flags=["provider_path"]), "provider path"),
            # Set-Location before or after a relative path: loops and blocks can run it first.
            ("Set-Location notes; Get-Content ../90-assets/a.png",
             facts(commands=["Set-Location", "Get-Content"],
                   strings=["Set-Location", "notes", "Get-Content", "../90-assets/a.png"]), "Set-Location"),
            ("Get-Content ../90-assets/a.png; cd notes",
             facts(commands=["Get-Content", "cd"], strings=["Get-Content", "../90-assets/a.png", "cd", "notes"]),
             "Set-Location"),
            # \x follows the current drive, which Set-Location can change too.
            ("Set-Location E:/; Get-Content \\vault\\90-assets\\a.png",
             facts(commands=["Set-Location", "Get-Content"],
                   strings=["Set-Location", "E:/", "Get-Content", "\\vault\\90-assets\\a.png"]), "Set-Location"),
            ("<# 90-assets #> Get-Date", facts(commands=["Get-Date"], strings=["Get-Date"]), "not as a literal path"),
            ("Get-Item ${C:\\x\\90-assets}", facts(strings=["Get-Item"], flags=["expression", "provider_path"]),
             "only known when it runs"),
            ("Get-Item 90-assets-old -New", facts(strings=["Get-Item", "90-assets-old"], flags=["flag_from_a_newer_parser"]),
             "flag_from_a_newer_parser"),
        ]
        for command, answer, words in cases:
            with self.subTest(command=command):
                self.assertEqual(self.decide_with(command, answer), ("ask", [command]))
                self.assertIn(words, self.reason_with(command, answer))

    def test_parser_failures_ask_and_say_why(self) -> None:
        for problem in ("did not answer within 5 seconds", "failed (exit code 1)",
                        "was not found (ps-parse.ps1 next to the guard)",
                        "needs Windows PowerShell (powershell.exe), which was not found"):
            with self.subTest(problem=problem):
                self.assertEqual(self.decide_with("Get-Content 90-assets/a.png", problem)[0], "ask")
                self.assertIn("the PowerShell parser " + problem, self.reason_with("Get-Content 90-assets/a.png", problem))

    def test_guard_controls_ask(self) -> None:
        for command, answer in (
                ("Get-Content 00-meta/vault-config.json", facts(strings=["Get-Content", "00-meta/vault-config.json"])),
                ("Set-Content .claude/settings.local.json '{}'",
                 facts(strings=["Set-Content", ".claude/settings.local.json", "{}"])),
                ("Copy-Item x.ps1 00-meta/scripts/ps-parse.ps1",
                 facts(strings=["Copy-Item", "x.ps1", "00-meta/scripts/ps-parse.ps1"])),
                ("Remove-Item 00-meta/scripts/deny-guard.py", facts(strings=["Remove-Item", "00-meta/scripts/deny-guard.py"])),
                ("Get-Content 00-meta/vault-con`fig.json", facts(strings=["Get-Content", "00-meta/vault-config.json"]))):
            with self.subTest(command=command):
                self.assertEqual(self.decide_with(command, answer), ("ask", [command]))
        answer = facts(strings=["Get-Content", ".claude/rules/vault-workflow.md"])
        self.assertIsNone(self.decide_with("Get-Content .claude/rules/vault-workflow.md", answer)[0])

    def test_a_backtick_does_not_hide_a_zone_name(self) -> None:
        # The backtick is PowerShell's escape character: 90-ass`ets is the bare word 90-assets.
        answer = facts(strings=["Get-Content", "90-assets/a.png"])
        self.assertEqual(self.decide_with("Get-Content 90-ass`ets/a.png", answer),
                         ("deny", ["Get-Content 90-ass`ets/a.png"]))

    def test_text_that_only_contains_a_zone_name_decides_nothing(self) -> None:
        for command, answer in (
                ("Write-Output 'see 90-assets in the guide'", facts(strings=["Write-Output", "see 90-assets in the guide"])),
                ("Get-Content notes/90-assets-old.md", facts(strings=["Get-Content", "notes/90-assets-old.md"])),
                ("git commit -m 'note: 20-knowledge/_archive-old'",
                 facts(strings=["git", "commit", "note: 20-knowledge/_archive-old"]))):
            with self.subTest(command=command):
                self.assertEqual(self.decide_with(command, answer), (None, [command]))


class PowerShellParserRunTests(unittest.TestCase):
    """parse_powershell: how the guard starts ps-parse.ps1 and reads its answer, without PowerShell."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard()

    def run_with(self, outcome, command: str = "Get-Content 90-assets/도면.png"):
        """parse_powershell with subprocess.run replaced: outcome is its return value or an exception."""
        calls: list = []

        def run(argv, **options):
            calls.append((argv, options))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with mock.patch.object(self.guard, "_powershell_executable", return_value="C:/Windows/powershell.exe"), \
                mock.patch.object(self.guard.subprocess, "run", run):
            return self.guard.parse_powershell(command), calls

    @staticmethod
    def answered(stdout: bytes, code: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], code, stdout, b"")

    def test_the_child_is_windows_powershell_with_the_command_on_stdin(self) -> None:
        (result, problem), calls = self.run_with(self.answered(json.dumps(facts(strings=["x"])).encode("ascii")))
        self.assertEqual((result, problem), (facts(strings=["x"]), ""))
        self.assertEqual(len(calls), 1)
        argv, options = calls[0]
        self.assertEqual(argv, ["C:/Windows/powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                "Bypass", "-File", str(PARSER)])
        self.assertEqual(options["input"], "Get-Content 90-assets/도면.png".encode("utf-8"))
        self.assertTrue(options["capture_output"])
        self.assertEqual(options["timeout"], 5.0)

    def test_the_answer_is_the_last_json_line(self) -> None:
        stdout = b"a warning line\r\n" + json.dumps(facts(strings=["a"])).encode("ascii") + b"\r\n"
        (result, problem), _ = self.run_with(self.answered(stdout))
        self.assertEqual((result, problem), (facts(strings=["a"]), ""))

    def test_failures_are_reported_not_raised(self) -> None:
        cases = [
            (subprocess.TimeoutExpired(["powershell"], 5.0), "did not answer within 5 seconds"),
            (OSError("blocked by policy"), "could not be started (OSError)"),
            (self.answered(b"", code=1), "failed (exit code 1)"),
            (self.answered(b""), "gave no answer"),
            (self.answered(b"not json"), "gave no answer"),
            (self.answered(b"{not json"), "gave no answer"),
            (self.answered(b"\xff{"), "gave no answer"),
            (self.answered(b'{"errors": 0}'), "gave no answer"),
            (self.answered(json.dumps(facts(errors=True)).encode("ascii")), "gave no answer"),
            (self.answered(json.dumps(facts(errors=-1)).encode("ascii")), "gave no answer"),
            (self.answered(json.dumps(facts(strings=[1])).encode("ascii")), "gave no answer"),
            (self.answered(json.dumps(facts(commands=[1])).encode("ascii")), "gave no answer"),
            (self.answered(json.dumps([facts()]).encode("ascii")), "gave no answer"),
        ]
        for outcome, expected in cases:
            with self.subTest(outcome=outcome):
                (result, problem), _ = self.run_with(outcome)
                self.assertIsNone(result)
                self.assertIn(expected, problem)

    def test_text_that_cannot_be_sent_is_reported(self) -> None:
        (result, problem), calls = self.run_with(self.answered(b""), command="Get-Content 90-assets/" + chr(0xD800))
        self.assertEqual((result, calls), (None, []))
        self.assertIn("could not be started", problem)

    def test_nothing_starts_without_the_parser_file_or_windows_powershell(self) -> None:
        with mock.patch.object(self.guard, "PARSER_NAME", "missing-parser.ps1"), \
                mock.patch.object(self.guard.subprocess, "run") as run:
            result, problem = self.guard.parse_powershell("Get-Content 90-assets/a")
        self.assertIsNone(result)
        self.assertIn("missing-parser.ps1", problem)
        run.assert_not_called()
        with mock.patch.object(self.guard, "_powershell_executable", return_value=None), \
                mock.patch.object(self.guard.subprocess, "run") as run:
            result, problem = self.guard.parse_powershell("Get-Content 90-assets/a")
        self.assertIsNone(result)
        self.assertIn("powershell.exe", problem)
        run.assert_not_called()

    def test_powershell_comes_only_from_the_windows_folder(self) -> None:
        with mock.patch.object(self.guard, "_WINDOWS", False):
            self.assertIsNone(self.guard._powershell_executable())
        with mock.patch.object(self.guard, "_WINDOWS", True), \
                mock.patch.dict(os.environ, {"SystemRoot": "", "windir": ""}):
            self.assertIsNone(self.guard._powershell_executable())
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(self.guard, "_WINDOWS", True), \
                mock.patch.dict(os.environ, {"SystemRoot": temporary}):
            self.assertIsNone(self.guard._powershell_executable())  # not installed there
            target = Path(temporary) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"")
            self.assertEqual(self.guard._powershell_executable(), str(target))


@unittest.skipUnless(_windows_powershell(), "Windows PowerShell is required")
class PowerShellParserTests(GuardCase):
    """The real ps-parse.ps1 in a Windows PowerShell child process (about 0.5 s per start).

    PowerShellDecisionTests pins the decision for each kind of parser answer;
    the table here is a sample that proves the real answers lead there.
    """

    DENY = (
        "Get-Content 90-assets/a.png", "Get-Content 10-inbox/_processed/a.md", "'90-assets/a.png' | Get-Content",
        "Get-Content notes/a.md > 90-assets/x.txt", "Get-Content 90-ass`ets/a.png", "Get-Content @'\n90-assets/a.png\n'@",
        'Get-Content "90-assets/$name.png"', "Move-Item 10-inbox/a.md 10-inbox/_processed/2026-10/",
        # Windows PowerShell 5.1 rejects &&, but the literal path is still in the parse.
        "Get-Content notes/a.md && Get-Content 90-assets/a.png",
    )
    ASK = (
        "iex 'Get-Content 90-assets-old/a'", 'Get-Content "$env:X\\90-assets"', 'Get-Content "unterminated 90-assets',
        "powershell -enc ZQBjAGgAbwA= # 90-assets", "Get-Item Registry::HKLM\\x\\90-assets",
        "Get-ChildItem notes | ForEach-Object { $_.Name } # 90-assets", "Set-Location notes; Get-Content ../90-assets/a.png",
        "Get-Content 00-meta/scripts/ps-parse.ps1", "[scriptblock]::Create('gc 90-assets/a').Invoke()",
        "Get-Item ${C:\\x\\90-assets-old\\a.png}",
    )
    NONE = (
        "Get-ChildItem", "Get-Content notes/90-assets-old.md", "Write-Output 'see 90-assets in the guide'",
        "Get-Content -Raw:$true notes/90-assets-old.md",
    )

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.run_parser_raw("")  # the first start after boot is the slowest; the tests time the later ones

    @staticmethod
    def run_parser_raw(command: str) -> subprocess.CompletedProcess:
        argv = [_windows_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                str(PARSER)]
        return subprocess.run(argv, input=command.encode("utf-8"), capture_output=True, timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def run_parser(self, command: str) -> dict:
        result = self.run_parser_raw(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        line = result.stdout.decode("ascii")  # every other character arrives as a \u escape
        self.assertEqual(line.count("\n"), 1)
        answer = json.loads(line)
        self.assertEqual(list(answer), ["errors", "commands", "strings", "expandable", "expandable_strings", "flags"])
        return answer

    def test_parser_script_parses(self) -> None:
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                 f"'{PARSER}', [ref]$null, [ref]$e); $e.Count")
        out = subprocess.run([_windows_powershell(), "-NoProfile", "-NonInteractive", "-Command", check],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "0")

    def test_the_guard_starts_this_powershell(self) -> None:
        self.assertEqual(self.guard._powershell_executable(), _windows_powershell())

    def test_answers_for_the_measured_probes(self) -> None:
        # The probes recorded when the design was verified, and PowerShell's escape and quote forms.
        self.assertEqual(self.run_parser("iex (gc x)"),
                         facts(commands=["iex", "gc"], strings=["iex", "gc", "x"],
                               flags=["expression", "invoke_expression"]))
        self.assertEqual(self.run_parser('gc "$env:X\\90-assets"'),
                         facts(commands=["gc"], strings=["gc"], expandable=1, expandable_strings=["$env:X\\90-assets"],
                               flags=["expression", "provider_path"]))
        self.assertEqual(self.run_parser('Get-Content "unterminated')["errors"], 1)
        answer = self.run_parser("& { gc 90-assets/a }")
        self.assertEqual(answer["commands"], [None, "gc"])
        self.assertIn("90-assets/a", answer["strings"])
        self.assertIn("script_block_call", answer["flags"])
        # The bypass the guide documents: no string names the zone, so the pre-filter never runs this.
        self.assertEqual(self.run_parser("Get-Content (Join-Path 90 -assets)")["strings"], ["Get-Content", "Join-Path"])
        self.assertEqual(self.run_parser("Get-Content 90-ass`ets/a.png")["strings"], ["Get-Content", "90-assets/a.png"])
        self.assertEqual(self.run_parser("Get-Content 90-ass'ets'/a.png")["strings"], ["Get-Content", "90-assets/a.png"])
        self.assertEqual(self.run_parser("Get-Content -Force:$true $null a.txt")["flags"], [])

    def test_non_ascii_text_and_control_characters_round_trip(self) -> None:
        answer = self.run_parser("Get-Content '90-assets/도면 1.png'; Write-Output \"a`tb\" '\"q\"' 'c\\d'")
        self.assertEqual(answer["strings"], ["Get-Content", "90-assets/도면 1.png", "Write-Output", "a\tb", '"q"', "c\\d"])

    def test_flags_name_each_run_time_construct(self) -> None:
        cases = (
            ("Microsoft.PowerShell.Utility\\Invoke-Expression x", "invoke_expression"),
            ("saps x", "start_process"), ("icm x", "invoke_command"), ("& $x", "script_block_call"),
            (". ./x.ps1", "dot_source"), ("powershell -e x", "encoded_command"),
            ("Get-Item HKLM:\\x", "provider_path"), ("Get-Item ${C:\\x\\a.txt}", "provider_path"),
            ("Get-Item ('a' + 'b')", "expression"), ("'a'.Length", "expression"),
            ("foreach ($i in 1) { }", "expression"),
        )
        for command, flag in cases:
            with self.subTest(command=command):
                self.assertIn(flag, self.run_parser(command)["flags"])
        for command in ("Get-Content a.txt | Out-File b.txt 2>&1", "& 'C:\\tools\\x.exe' -Encoding utf8 -EA Stop 'a$b'"):
            with self.subTest(quiet=command):
                self.assertEqual(self.run_parser(command)["flags"], [])

    def test_provider_paths_agree_with_the_guard(self) -> None:
        # ps-parse.ps1 flags them and deny-guard.py skips them as file paths: one rule in two files.
        for text in ("Registry::HKLM\\x", "Env:X", "Env:", "https://example.com/x", "FileSystem::C:\\x",
                     "Microsoft.PowerShell.Core\\FileSystem::C:\\x", "C:\\x", "notes/a.md", "$x:y",
                     "note: 20-knowledge/_archive"):
            with self.subTest(text=text):
                flagged = "provider_path" in self.run_parser(f"Write-Output '{text}'")["flags"]
                self.assertEqual(flagged, self.guard._ps_file_path(text) is None)

    def test_decision_table(self) -> None:
        target = self.path("90-assets", "a.png")
        deny = (*self.DENY, f"Get-Content '{target}'", f"Get-Content FileSystem::{target}")
        with mock.patch.object(self.guard, "PARSE_TIMEOUT", 60):  # a busy test machine must not turn deny into ask
            for expected, commands in (("deny", deny), ("ask", self.ASK), (None, self.NONE)):
                for command in commands:
                    with self.subTest(command=command):
                        self.assertEqual(self.decide("PowerShell", {"command": command}), expected)
            self.assertEqual(self.decide("PowerShell", {"command": "Get-Content _processed/a.md"}, self.path("10-inbox")),
                             "deny")

    def test_a_parser_that_does_not_answer_in_time_asks(self) -> None:
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "Get-Content 90-assets/a.png"}}
        with mock.patch.object(self.guard, "PARSE_TIMEOUT", 0.001):
            decision, reason = self.guard.evaluate(payload, str(self.vault))
        self.assertEqual(decision, "ask")
        self.assertIn("did not answer within 0.001 seconds", reason)

    def test_an_installed_copy_runs_the_parser_next_to_it(self) -> None:
        scripts = self.vault / "00-meta" / "scripts"
        shutil.copyfile(GUARD, scripts / "deny-guard.py")
        shutil.copyfile(PARSER, scripts / "ps-parse.ps1")
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_PROJECT_DIR"}
        env["CLAUDE_PROJECT_DIR"] = str(self.vault)

        def hook(command: str) -> dict | None:
            stdin = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "PowerShell",
                                "tool_input": {"command": command}}, ensure_ascii=False).encode("utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", "-I", str(scripts / "deny-guard.py")],
                                    input=stdin, capture_output=True, env=env, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout else None

        self.assertEqual(hook("Get-Content 90-assets/도면.png")["permissionDecision"], "deny")
        self.assertIsNone(hook("Get-ChildItem notes"))
        (scripts / "ps-parse.ps1").unlink()
        output = hook("Get-Content 90-assets/a.png")
        self.assertEqual(output["permissionDecision"], "ask")
        self.assertIn("ps-parse.ps1", output["permissionDecisionReason"])


class ConfigTests(GuardCase):
    def test_zones_come_from_the_vault_config(self) -> None:
        self.write_config({"deny_zones": [" 40-people\\private ", "secret"]})
        self.assert_table("Read", [
            ({"file_path": self.path("40-people", "private", "a.md")}, "deny"),
            ({"file_path": self.path("notes", "secret", "a.md")}, "deny"),
            ({"file_path": self.path("90-assets", "a.png")}, None),  # not configured here
        ])
        self.assertEqual(self.decide("Bash", {"command": "cat 40-people/private/a.md"}), "deny")
        self.assertEqual(self.warnings, [])

    def test_unreadable_or_invalid_config_falls_back_to_template_zones(self) -> None:
        for raw in ("{not json", "[]", json.dumps({"deny_zones": "90-assets"}),
                    json.dumps({"deny_zones": [42]}), json.dumps({"deny_zones": ["../outside"]}),
                    json.dumps({"deny_zones": ["C:/abs"]}), json.dumps({"deny_zones": ["/abs"]}),
                    json.dumps({"deny_zones": ["notes/"]}), json.dumps({"deny_zones": ["custom", ""]})):
            with self.subTest(raw=raw):
                self.warnings.clear()
                self.write_config(None, raw=raw)
                self.assertEqual(self.decide("Read", {"file_path": self.path("90-assets", "a.png")}), "deny")
                self.assertEqual(len(self.warnings), 1)
                self.assertTrue(self.warnings[0].startswith("deny-guard: "))
        (self.vault / "00-meta" / "vault-config.json").unlink()
        self.warnings.clear()
        self.assertEqual(self.decide("Read", {"file_path": self.path(".obsidian", "a.json")}), "deny")
        self.assertEqual(len(self.warnings), 1)

    def test_missing_key_uses_defaults_and_an_empty_list_denies_nothing(self) -> None:
        self.write_config({"vault_name": "x"})
        self.assertEqual(self.decide("Read", {"file_path": self.path("90-assets", "a.png")}), "deny")
        self.assertEqual(self.warnings, [])
        self.write_config({"deny_zones": []})  # the user's explicit choice, as in the healthcheck
        self.assertIsNone(self.decide("Read", {"file_path": self.path("90-assets", "a.png")}))
        self.assertEqual(self.decide("Write", {"file_path": self.path("00-meta", "vault-config.json")}), "ask")

    def test_fallback_zones_are_the_template_zones(self) -> None:
        template = json.loads((ROOT / "assets" / "templates" / "vault-config.json").read_text(encoding="utf-8"))
        self.assertEqual(self.guard.DEFAULT_DENY_ZONES, tuple(template["deny_zones"]))
        self.assertEqual(self.guard.DEFAULT_DENY_ZONES, TEMPLATE_ZONES)


@unittest.skipUnless(os.name == "nt", "Windows path forms")
class WindowsPathTests(GuardCase):
    def test_msys_paths_are_mapped_to_drive_paths(self) -> None:
        target = msys_form(self.vault / "90-assets" / "a.png")
        self.assertEqual(self.decide("Read", {"file_path": target}), "deny")
        self.assertEqual(self.decide("Bash", {"command": f"cat {target}"}), "deny")
        self.assertEqual(self.decide("Bash", {"command": "cat _processed/a.md"}, msys_form(self.vault / "10-inbox")),
                         "deny")
        self.assertIsNone(self.decide("Read", {"file_path": msys_form(self.vault / "notes" / "a.md")}))

    def test_vault_root_case_and_windows_name_forms_are_folded(self) -> None:
        self.assert_table("Read", [
            ({"file_path": str(self.vault).upper() + "\\90-ASSETS\\A.PNG"}, "deny"),
            ({"file_path": self.path("90-assets.", "a.png")}, "deny"),  # Windows drops a trailing dot
            ({"file_path": self.path("90-assets::$INDEX_ALLOCATION", "a.png")}, "deny"),
            ({"file_path": "\\\\?\\" + self.path("90-assets", "a.png")}, "deny"),
            ({"file_path": str(self.vault).upper() + "\\NOTES\\A.MD"}, None),
        ])
        self.assertEqual(self.decide("Bash", {"command": "cat '" + self.path("90-assets", "a.png") + "'"}), "deny")


class LinkTests(GuardCase):
    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    def test_junction_parent_resolving_into_a_zone_is_denied(self) -> None:
        make_junction(self.vault / "notes" / "link", self.vault / "90-assets")
        target = self.path("notes", "link", "a.png")
        self.assertEqual(self.decide("Read", {"file_path": target}), "deny")
        self.assertEqual(self.decide("Glob", {"pattern": "*", "path": self.path("notes", "link")}), "deny")
        self.assertEqual(self.decide("Grep", {"pattern": "x", "path": self.path("notes", "link")}), "deny")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    def test_zone_that_is_a_junction_out_of_the_vault_is_still_denied(self) -> None:
        outside = self.base / "outside-assets"
        outside.mkdir()
        (self.vault / "90-assets").rmdir()
        make_junction(self.vault / "90-assets", outside)
        self.assertEqual(self.decide("Read", {"file_path": self.path("90-assets", "a.png")}), "deny")
        self.assertIsNone(self.decide("Read", {"file_path": str(outside / "a.png")}))  # outside the vault

    def test_symlinked_parent_resolving_into_a_zone_is_denied(self) -> None:
        try:
            os.symlink(self.vault / "90-assets", self.vault / "notes" / "slink", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks are not available: {error}")
        self.assertEqual(self.decide("Read", {"file_path": self.path("notes", "slink", "a.png")}), "deny")


class ProcessContractTests(unittest.TestCase):
    """The script as the hook runs it: python -X utf8 -I <file> with JSON on stdin."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name).resolve() / "vault"
        (self.vault / "00-meta").mkdir(parents=True)
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({"deny_zones": list(TEMPLATE_ZONES)}), encoding="utf-8")

    def run_guard(self, stdin: bytes, vault: str | None = "default") -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "CLAUDE_PROJECT_DIR"}
        if vault == "default":
            env["CLAUDE_PROJECT_DIR"] = str(self.vault)
        elif vault is not None:
            env["CLAUDE_PROJECT_DIR"] = vault
        return subprocess.run([sys.executable, "-X", "utf8", "-I", str(GUARD)], input=stdin,
                              capture_output=True, env=env, timeout=60)

    def payload(self, tool: str, tool_input: dict) -> bytes:
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                           "tool_input": tool_input}, ensure_ascii=False).encode("utf-8")

    def test_deny_is_one_json_object_with_exit_zero(self) -> None:
        result = self.run_guard(self.payload("Read", {"file_path": str(self.vault / "90-assets" / "도면.png")}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"\n"), 1)
        output = json.loads(result.stdout)
        self.assertEqual(list(output), ["hookSpecificOutput"])
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(specific), {"hookEventName", "permissionDecision", "permissionDecisionReason"})
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertEqual(specific["permissionDecision"], "deny")
        self.assertTrue(specific["permissionDecisionReason"].startswith("deny-guard: "))
        self.assertEqual(result.stderr, b"")

    def test_no_decision_prints_nothing(self) -> None:
        result = self.run_guard(self.payload("Read", {"file_path": str(self.vault / "notes" / "a.md")}))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_output_never_contains_allow(self) -> None:
        payloads = [
            self.payload("Read", {"file_path": str(self.vault / "90-assets" / "a.png")}),
            self.payload("Read", {"file_path": str(self.vault / "notes" / "a.md")}),
            self.payload("Write", {"file_path": str(self.vault / "00-meta" / "vault-config.json")}),
            self.payload("Bash", {"command": "cat $(echo x)/90-assets-old"}),
            self.payload("Bash", {"command": "mv -n 10-inbox/a.md 10-inbox/_processed/2026-10/"}),
            self.payload("PowerShell", {"command": "Get-Content .obsidian/app.json"}),
            self.payload("Glob", {"pattern": "**/_processed/*"}),
            b"not json", b"\xff\xfe\x00",
        ]
        for stdin in payloads:
            with self.subTest(stdin=stdin[:60]):
                result = self.run_guard(stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(b"allow", result.stdout.lower())
                if result.stdout:
                    decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
                    self.assertIn(decision, ("deny", "ask"))

    def test_unreadable_input_asks(self) -> None:
        for stdin in (b"not json", b"\xff\xfe\x00", b"[]", b'{"tool_input": {}}'):
            with self.subTest(stdin=stdin):
                result = self.run_guard(stdin)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_missing_project_dir_decides_nothing_and_says_so_once(self) -> None:
        stdin = self.payload("Read", {"file_path": str(self.vault / "90-assets" / "a.png")})
        for vault in (None, "", str(self.vault / "missing")):
            with self.subTest(vault=vault):
                result = self.run_guard(stdin, vault=vault)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                lines = result.stderr.decode("utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                self.assertIn("CLAUDE_PROJECT_DIR", lines[0])

    def test_invalid_config_warns_once_and_still_denies_template_zones(self) -> None:
        (self.vault / "00-meta" / "vault-config.json").write_text("{broken", encoding="utf-8")
        result = self.run_guard(self.payload("Read", {"file_path": str(self.vault / ".obsidian" / "a.json")}))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(len(result.stderr.decode("utf-8").splitlines()), 1)

    def test_internal_error_becomes_ask(self) -> None:
        guard = load_guard()
        with mock.patch.object(guard, "evaluate", side_effect=RuntimeError("boom")), \
                mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.vault)}), \
                mock.patch.object(sys, "stdin", mock.Mock(buffer=mock.Mock(read=lambda: b"{}"))), \
                mock.patch.object(sys, "stdout", new_callable=lambda: _Capture()) as captured:
            self.assertEqual(guard.main(), 0)
        output = json.loads(captured.text)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("RuntimeError", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_hook_output_never_widens(self) -> None:
        guard = load_guard()
        self.assertEqual(guard.hook_output("allow", "x")["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertEqual(guard.hook_output("defer", "x")["hookSpecificOutput"]["permissionDecision"], "ask")


class _Capture:
    def __init__(self) -> None:
        self.text = ""

    def write(self, text: str) -> int:
        self.text += text
        return len(text)

    def flush(self) -> None:
        pass


class HookTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))

    def test_one_unfiltered_exec_form_handler_that_blocks_on_failure(self) -> None:
        groups = self.data["hooks"]["PreToolUse"]
        self.assertEqual(list(self.data), ["hooks"])
        self.assertEqual(list(self.data["hooks"]), ["PreToolUse"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], HOOK_MATCHER)
        self.assertRegex(groups[0]["matcher"], r"^[A-Za-z|]+$")  # an exact list, not a regular expression
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]
        self.assertNotIn("if", handler)  # best-effort filters are not used for a guard
        self.assertEqual(handler, {
            "type": "command",
            "command": "python",
            "args": ["-X", "utf8", "-I", "${CLAUDE_PROJECT_DIR}/00-meta/scripts/deny-guard.py"],
            "timeout": 10,
            "onFailure": "block",
        })

    def test_matcher_lists_exactly_the_tools_the_guard_reads(self) -> None:
        guard = load_guard()
        self.assertEqual(set(HOOK_MATCHER.split("|")), set(guard.PATH_TOOLS) | set(guard.SHELL_TOOLS))
        self.assertTrue(self.data["hooks"]["PreToolUse"][0]["hooks"][0]["args"][-1].endswith(guard.GUARD_REL))

    def test_aside_adapter_report_ignores_the_guard_hook(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            import vault_adapters
        finally:
            sys.path.remove(str(SCRIPTS))
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault"
            (vault / "00-meta").mkdir(parents=True)
            (vault / "00-meta" / "vault-config.json").write_text("{}", encoding="utf-8")
            (vault / ".claude").mkdir()
            (vault / ".claude" / "settings.local.json").write_text(
                (ASSETS / "settings-hooks.json").read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.object(vault_adapters.shutil, "which", return_value=None):
                report, code = vault_adapters.diagnose(vault)
        self.assertEqual(code, 0)
        self.assertEqual(report["adapters"]["aside"]["hook"],
                         {"state": "absent", "configured_in": [], "unreadable": [], "problems": []})

    def test_guide_states_how_to_merge_and_the_limits(self) -> None:
        text = (ASSETS / "guide.md").read_text(encoding="utf-8")
        for literal in ("00-meta/scripts/deny-guard.py", "settings.local.json", "onFailure", "v2.1.295",
                        "fail-open", "allow", "샌드박스", "Codex", "끄기", "지연", "PowerShell", "mv", "mkdir",
                        "vault_paths.zone_matches", "code.claude.com/docs/en/hooks", "shlex",
                        "00-meta/scripts/ps-parse.ps1", "Parser.ParseInput", "Join-Path 90 -assets",
                        "-EncodedCommand", "5초(`PARSE_TIMEOUT`)", "expandable_strings"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        self.assertIsNone(re.search(r"\]\((?!https?:)[^)]+\)", text))  # no repository links to keep in sync


class DefaultInstallUnchangedTests(unittest.TestCase):
    """The guard is opt-in: nothing in the default install runs or references it."""

    def test_plugin_hooks_have_no_pre_tool_use_entry(self) -> None:
        text = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.assertNotIn("PreToolUse", json.loads(text)["hooks"])
        self.assertNotIn("deny-guard", text)
        self.assertNotIn("ps-parse", text)  # the PowerShell parser runs only under the opt-in guard

    def test_permission_and_config_templates_are_unchanged_since_v0310(self) -> None:
        for rel_path, expected in V0310_TEMPLATE_SHA256.items():
            with self.subTest(path=rel_path):
                data = (ROOT / rel_path).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
