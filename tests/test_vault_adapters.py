"""Optional tool adapters: the Aside assets and the read-only adapter report.

The report must only describe what is installed and what may be offered. These
tests pin its states and actions, keep the shipped assets generic (no personal
paths), and on Windows run the helper script far enough to prove that a hook
call never blocks, acts only on commands that run aside, and never mistakes
the CLI process (also named aside.exe) for the browser. The helper runs with a
PATH that holds no aside CLI and with a missing ASIDE_EXE, so no test can start
the real app. The opt-in deny-zone guard has its own report entry
(adapters.deny_guard): its states and hook checks, the install offer that only
/vault-init asks for, and an Aside entry that stays as it was in v0.31.0.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
ADAPTERS = ROOT / "assets" / "adapters"
ASSETS = ADAPTERS / "aside"
HELPER = ASSETS / "aside-up.ps1"
DENY_GUARD = ADAPTERS / "deny-guard" / "deny-guard.py"
DENY_GUARD_PARSER = ADAPTERS / "deny-guard" / "ps-parse.ps1"
DENY_GUARD_HOOKS = ADAPTERS / "deny-guard" / "settings-hooks.json"
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


# Strings the shipped adapter assets must not contain: the maintainer's personal and company
# identifiers. Only SHA-256 digests are kept, taken after _fold_like_ignorecase and keyed by
# length in characters, so this public test no longer spells the strings out. The digests
# only avoid printing the strings and do not protect them: they are unsalted and the strings
# are short, so a dictionary or exhaustive search can recover them. Earlier revisions of this
# file did spell them out, and those revisions remain in git history.
FORBIDDEN_ASSET_SHA256 = {
    3: frozenset({"7e35922d6cf34e0374d8c0d5f427967f3f5aa9cb093dc3fbc62d217aa6a6725d"}),
    4: frozenset({"f29cc5e0e2e3ede69556cf4185b633da4a4c43945c7c9b3f86137cd5c6f638be"}),
    5: frozenset({"7f6d4d643c6f6b1c64530119c5ce6a41287efe680e4a5520499f9c930732f0f1",
                  "a94c244a52267c909e305da4ef109716db11d7dfb5900260a13e93b7f2232534",
                  "d24d6661afa04235f6ba8d5b462fe5cbe77076a2ff43f383c87ad673fd6f4f4b"}),
    7: frozenset({"748371a1ce9a7f237750d8c8cea7e9540e3178a8aa8db3edcf10d8774d133eef"}),
    11: frozenset({"c4e88a3640098dd5ae1cef7edac5fa46ce2eff38e6b25012a4c7df83d6ec985e"}),
}
# re.IGNORECASE matches dotless i and dotted capital I against i; str.casefold() keeps the
# first and turns the second into two characters.
_IGNORECASE_I = {"\N{LATIN SMALL LETTER DOTLESS I}": "i", "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}": "i"}


def _fold_like_ignorecase(text: str) -> str:
    """Case-fold each character to one character, equating what re.IGNORECASE equates.

    The forbidden strings use only ASCII and Hangul syllables. For those characters this
    fold equates exactly the code points that re.IGNORECASE matches (pinned by a test
    below), so a window matches where the case-insensitive pattern used to. A character
    whose fold is longer, such as sharp s, stays as it is: re.IGNORECASE matches it
    against none of them, and one character per character keeps windows at text offsets.
    """
    folded = []
    for char in text:
        fold = _IGNORECASE_I.get(char, char.casefold())
        folded.append(fold if len(fold) == 1 else char)
    return "".join(folded)


def _forbidden_spans(text: str,
                     digests: dict[int, frozenset[str]] = FORBIDDEN_ASSET_SHA256) -> list[tuple[int, int]]:
    """Return (offset, length) of every window of the folded text whose digest is listed."""
    folded = _fold_like_ignorecase(text)
    return [(start, length) for length, listed in sorted(digests.items())
            for start in range(len(folded) - length + 1)
            if hashlib.sha256(folded[start:start + length].encode("utf-8")).hexdigest() in listed]


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

    def test_deny_guard_is_ascii_and_stamped(self) -> None:
        # The opt-in deny-zone guard is copied into vaults like the Aside helper.
        raw = DENY_GUARD.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM: the file must stay ASCII")
        text = raw.decode("ascii")  # raises if a non-ASCII byte slipped in
        match = re.fullmatch(r"# agentic-vault:adapter deny-guard engine=(\S+)", text.splitlines()[0])
        self.assertIsNotNone(match)
        stamp = match.group(1)
        self.assertRegex(stamp, vault_adapters.VERSION_RE)
        # It first ships after v0.31.0 and never names a release beyond the next minor one.
        major, minor, _ = _manifest_core()
        self.assertGreater(vault_adapters._compare(stamp, "0.31.0"), 0)
        self.assertLessEqual(vault_adapters._version(stamp), (major, minor + 1, 0))
        for literal in ("BEGIN SHARED ZONE RULE", "END SHARED ZONE RULE", "CLAUDE_PROJECT_DIR",
                        "hookSpecificOutput", "permissionDecisionReason", "shlex.shlex", "punctuation_chars",
                        "whitespace_split", "os.path.realpath", "os.path.normcase"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_deny_guard_parser_is_ascii_and_stamped_like_the_guard(self) -> None:
        # Windows PowerShell 5.1 reads a file without a BOM in the ANSI code page, so it stays ASCII.
        raw = DENY_GUARD_PARSER.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM: the file must stay ASCII")
        text = raw.decode("ascii")  # raises if a non-ASCII byte slipped in
        match = re.fullmatch(r"# agentic-vault:adapter ps-parse engine=(\S+)", text.splitlines()[0])
        self.assertIsNotNone(match)
        guard = re.fullmatch(r"# agentic-vault:adapter deny-guard engine=(\S+)",
                             DENY_GUARD.read_text(encoding="ascii").splitlines()[0])
        self.assertEqual(match.group(1), guard.group(1))  # the two files ship and are copied together
        for literal in ("[System.Management.Automation.Language.Parser]::ParseInput", "FindAll", "GetCommandName",
                        "InvocationOperator", "IsDriveQualified", "OpenStandardInput", "UTF8Encoding",
                        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ps-parse.ps1"):
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
        assets = sorted(path for path in ADAPTERS.glob("*/*") if path.is_file())
        self.assertIn(DENY_GUARD, assets)  # every adapter folder, the deny-zone guard included
        self.assertIn(DENY_GUARD_PARSER, assets)
        for path in assets:
            with self.subTest(asset=path.relative_to(ADAPTERS).as_posix()):
                # Offsets and lengths only, so a failure never prints the matched text.
                self.assertEqual(_forbidden_spans(path.read_text(encoding="utf-8")), [])

    def test_digest_matcher_reports_what_ignorecase_search_finds(self) -> None:
        # A public stand-in list: the matcher finds every overlapping window at text offsets,
        # exactly where a re.IGNORECASE search for the same word matches.
        word = "aside"
        stand_in = {len(word): frozenset({hashlib.sha256(word.encode("utf-8")).hexdigest()})}
        samples = ("ASIDE", "x AsIdE y", "as\N{LATIN SMALL LETTER DOTLESS I}de",
                   "AS\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}DE", "a\N{LATIN SMALL LETTER LONG S}ide",
                   "\N{LATIN SMALL LETTER SHARP S}Aside", "asideaside", "asid", "a side", "")
        for sample in samples:
            with self.subTest(sample=sample):
                expected = [(match.start(), len(word))
                            for match in re.finditer(f"(?={word})", sample, re.IGNORECASE)]
                self.assertEqual(_forbidden_spans(sample, stand_in), expected)
        self.assertEqual(_forbidden_spans("\N{LATIN SMALL LETTER SHARP S}Aside", stand_in), [(1, len(word))])

    def test_fold_equates_exactly_what_ignorecase_equates(self) -> None:
        # Run on every interpreter, since each carries its own Unicode tables. Every code point
        # folds to an ASCII letter exactly when re.IGNORECASE matches it to that letter, and the
        # uncased ASCII characters and Hangul syllables equal only themselves under both.
        every = "".join(map(chr, range(sys.maxunicode + 1)))
        folded = _fold_like_ignorecase(every)
        self.assertEqual(len(folded), len(every))
        for letter in string.ascii_lowercase:
            with self.subTest(letter=letter):
                matched = [match.start() for match in re.finditer(letter, every, re.IGNORECASE)]
                self.assertEqual([match.start() for match in re.finditer(letter, folded)], matched)
        uncased = "[ -@\\[-`{-~" + chr(0xAC00) + "-" + chr(0xD7A3) + "]"
        members = [match.start() for match in re.finditer(uncased, every)]
        self.assertEqual(len(members), 43 + 11172)  # printable ASCII other than letters, Hangul syllables
        self.assertEqual([match.start() for match in re.finditer(uncased, every, re.IGNORECASE)], members)
        self.assertEqual([match.start() for match in re.finditer(uncased, folded)], members)
        self.assertEqual([folded[index] for index in members], [every[index] for index in members])


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
        outside = Path(self._tmp.name) / "outside-junction"
        outside.mkdir()
        (outside / "aside-up.ps1").write_text(self.bundled_helper(), encoding="utf-8")
        (self.vault / "00-meta").mkdir(exist_ok=True)
        make_junction(self.vault / "00-meta" / "scripts", outside)
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


def _first_line_stamp(path: Path) -> str:
    """The engine= value on the first line of an adapter script."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    match = re.search(r"engine=(\S+)$", first)
    assert match, first
    return match.group(1)


def _guard_constants() -> dict:
    """PATH_TOOLS, SHELL_TOOLS, GUARD_REL and PARSER_REL as the guard asset writes them (read, not imported)."""
    wanted = {"PATH_TOOLS", "SHELL_TOOLS", "GUARD_REL", "PARSER_REL"}
    values = {}
    for node in ast.parse(DENY_GUARD.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in wanted):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(values) == wanted, sorted(values)
    return values


class _VaultCase(unittest.TestCase):
    """A temporary vault per test (new_vault() starts another one), no aside CLI, Windows."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._count = 0
        self.new_vault()
        for patcher in (mock.patch.object(vault_adapters.shutil, "which", return_value=None),
                        mock.patch.object(vault_adapters, "_platform", return_value="windows")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_vault(self) -> Path:
        self._count += 1
        self.vault = Path(self._tmp.name) / f"vault{self._count}"
        self.write("00-meta/vault-config.json", "{}")
        return self.vault

    def write(self, rel: str, text: str, newline: str = "\n") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        return path

    @staticmethod
    def template() -> dict:
        return json.loads(DENY_GUARD_HOOKS.read_text(encoding="utf-8"))

    def install_scripts(self, guard: bool = True, parser: bool = True) -> None:
        if guard:
            self.write(vault_adapters.GUARD_REL, DENY_GUARD.read_text(encoding="utf-8"))
        if parser:
            self.write(vault_adapters.PARSER_REL, DENY_GUARD_PARSER.read_text(encoding="utf-8"))

    def merge_hook(self, data: dict | None = None, rel: str = ".claude/settings.local.json") -> None:
        self.write(rel, json.dumps(self.template() if data is None else data, indent=2))


class DenyGuardReportTests(_VaultCase):
    """adapters.deny_guard: the guard's two scripts and its hook; offered only when /vault-init asks."""

    def report(self, offer: bool = False) -> dict:
        report, code = vault_adapters.diagnose(self.vault, offer_deny_guard=offer)
        self.assertEqual(code, 0)
        return report["adapters"]["deny_guard"]

    def modified_template(self, **changes) -> dict:
        """The template with handler keys replaced (None removes the key); "matcher" changes the group."""
        data = self.template()
        group = data["hooks"]["PreToolUse"][0]
        for key, value in changes.items():
            target = group if key == "matcher" else group["hooks"][0]
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
        return data

    def test_vault_without_the_guard_reports_absent(self) -> None:
        guard = self.report()
        self.assertEqual(guard["state"], "absent")
        self.assertEqual(guard["actions"], [])
        self.assertEqual(guard["hook"], {"state": "absent", "configured_in": [], "unreadable": [], "problems": []})
        for key, rel, asset in (("guard", "00-meta/scripts/deny-guard.py", DENY_GUARD),
                                ("parser", "00-meta/scripts/ps-parse.ps1", DENY_GUARD_PARSER)):
            with self.subTest(script=key):
                self.assertEqual(guard[key], {"path": rel, "bundled_stamp": _first_line_stamp(asset),
                                              "installed_stamp": None, "state": "missing"})

    def test_install_offer_is_listed_only_when_asked(self) -> None:
        # /vault-doctor runs the report without the flag; only /vault-init passes it.
        self.assertEqual(self.report()["actions"], [])
        self.assertEqual(self.report(offer=True)["actions"], ["offer_deny_guard"])
        for argv, expected in (([], []), (["--offer-deny-guard"], ["offer_deny_guard"])):
            with self.subTest(argv=argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = vault_adapters.main(["--vault", str(self.vault), "--format", "json", *argv])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(buffer.getvalue())["adapters"]["deny_guard"]["actions"], expected)

    def test_offer_needs_a_local_settings_file_it_can_merge_into(self) -> None:
        self.write(".claude/settings.json", "{not json")
        guard = self.report(offer=True)
        self.assertEqual((guard["state"], guard["hook"]["unreadable"]), ("absent", [".claude/settings.json"]))
        self.assertEqual(guard["actions"], ["offer_deny_guard"])  # the merge goes to settings.local.json
        self.write(".claude/settings.local.json", "{not json")
        guard = self.report(offer=True)
        self.assertEqual(guard["state"], "absent")
        self.assertEqual(guard["actions"], [])  # nothing to merge into; the Aside entry reports the files

    def test_not_a_vault_ignores_the_offer_flag(self) -> None:
        empty = Path(self._tmp.name) / "plain"
        empty.mkdir()
        self.assertEqual(vault_adapters.diagnose(empty, offer_deny_guard=True),
                         ({"schema_version": 1, "status": "not_vault", "reason_code": "config_not_found",
                           "adapters": {}}, 2))

    def test_template_install_is_installed_and_not_offered_again(self) -> None:
        self.install_scripts()
        self.merge_hook()
        for offer in (False, True):
            with self.subTest(offer=offer):
                guard = self.report(offer)
                self.assertEqual(guard["state"], "installed")
                self.assertEqual((guard["guard"]["state"], guard["parser"]["state"]), ("current", "current"))
                self.assertEqual(guard["guard"]["installed_stamp"], guard["guard"]["bundled_stamp"])
                self.assertEqual(guard["hook"], {"state": "configured",
                                                 "configured_in": [".claude/settings.local.json"],
                                                 "unreadable": [], "problems": []})
                self.assertEqual(guard["actions"], [])

    def test_crlf_and_bom_copies_are_current(self) -> None:
        self.merge_hook()
        self.write(vault_adapters.GUARD_REL, DENY_GUARD.read_text(encoding="utf-8"), newline="\r\n")
        self.write(vault_adapters.PARSER_REL, "\ufeff" + DENY_GUARD_PARSER.read_text(encoding="utf-8"),
                   newline="\r\n")
        guard = self.report()
        self.assertEqual((guard["guard"]["state"], guard["parser"]["state"], guard["state"]),
                         ("current", "current", "installed"))

    def test_script_version_states(self) -> None:
        for key, rel, asset in (("guard", vault_adapters.GUARD_REL, DENY_GUARD),
                                ("parser", vault_adapters.PARSER_REL, DENY_GUARD_PARSER)):
            text = asset.read_text(encoding="utf-8")
            bundled = _first_line_stamp(asset)
            cases = {
                "outdated": (text.replace(f"engine={bundled}", "engine=0.1.0", 1), "offer_guard_update"),
                "newer": (text.replace(f"engine={bundled}", "engine=99.0.0", 1), "review_guard"),
                "modified": (text + "\n# local tweak\n", "review_guard"),
                "unstamped": (text.split("\n", 1)[1], "review_guard"),
            }
            for state, (content, action) in cases.items():
                with self.subTest(script=key, state=state):
                    self.new_vault()
                    self.install_scripts()
                    self.merge_hook()
                    self.write(rel, content)
                    guard = self.report(offer=True)
                    self.assertEqual(guard[key]["state"], state)
                    self.assertEqual(guard["state"], "installed")
                    self.assertEqual(guard["actions"], [action])

    def test_partial_installs_are_incomplete_and_never_offered(self) -> None:
        cases = {
            "scripts_without_hook": lambda: self.install_scripts(),
            "hook_without_scripts": lambda: self.merge_hook(),
            "guard_and_hook_without_parser": lambda: (self.install_scripts(parser=False), self.merge_hook()),
            "parser_alone": lambda: self.install_scripts(guard=False),
        }
        for name, build in cases.items():
            with self.subTest(case=name):
                self.new_vault()
                build()
                guard = self.report(offer=True)
                self.assertEqual(guard["state"], "incomplete")
                self.assertEqual(guard["actions"], ["review_guard"])

    def test_hook_settings_that_weaken_the_guard_need_review(self) -> None:
        bare = "$CLAUDE_PROJECT_DIR/00-meta/scripts/deny-guard.py"
        cases = {
            "no_on_failure": ({"onFailure": None}, '"onFailure": "block"'),
            "on_failure_continue": ({"onFailure": "continue"}, '"onFailure": "block"'),
            "if_filter": ({"if": "Read(*)"}, "`if` filter"),
            "bare_variable_in_args": ({"args": ["-X", "utf8", "-I", bare]}, "bare $CLAUDE_PROJECT_DIR"),
            "bare_variable_in_shell_form": ({"command": f'python -X utf8 -I "{bare}"', "args": None},
                                            "bare $CLAUDE_PROJECT_DIR"),
            "narrowed_matcher": ({"matcher": "Read|Edit|Write|Bash"},
                                 "leaves out Grep, Glob, NotebookEdit, PowerShell"),
            "comma_matcher_without_powershell": ({"matcher": "Read, Grep, Glob, Edit, Write, NotebookEdit, Bash"},
                                                 "leaves out PowerShell,"),
        }
        for name, (changes, fragment) in cases.items():
            with self.subTest(case=name):
                self.new_vault()
                self.install_scripts()
                self.merge_hook(self.modified_template(**changes))
                guard = self.report(offer=True)
                self.assertEqual(guard["state"], "needs_review")
                self.assertEqual(guard["hook"]["state"], "needs_review")
                self.assertEqual(guard["hook"]["configured_in"], [".claude/settings.local.json"])
                self.assertEqual(len(guard["hook"]["problems"]), 1, guard["hook"]["problems"])
                problem = guard["hook"]["problems"][0]
                self.assertTrue(problem.startswith(".claude/settings.local.json: deny-guard "), problem)
                self.assertIn(fragment, problem)
                self.assertEqual(guard["actions"], ["review_settings"])

    def test_hook_forms_that_keep_every_check_are_configured(self) -> None:
        guard_path = "${CLAUDE_PROJECT_DIR}/00-meta/scripts/deny-guard.py"
        variants = {
            "match_all": {"matcher": "*"},
            "no_matcher": {"matcher": None},
            "wider_list": {"matcher": "Read, Grep, Glob, Edit, Write, NotebookEdit, Bash, PowerShell, WebFetch"},
            "regular_expression_not_judged": {"matcher": "Read|Grep|Glob|Edit|Write|NotebookEdit|Bash|Power.*"},
            "py_launcher": {"command": "py", "args": ["-3", "-X", "utf8", "-I", guard_path]},
            "shell_form": {"command": f'python -X utf8 -I "{guard_path}"', "args": None},
        }
        for name, changes in variants.items():
            with self.subTest(variant=name):
                self.new_vault()
                self.install_scripts()
                self.merge_hook(self.modified_template(**changes))
                guard = self.report()
                self.assertEqual(guard["hook"]["problems"], [])
                self.assertEqual(guard["state"], "installed")
                self.assertEqual(guard["actions"], [])

    def test_unreadable_settings_are_reviewed_once_part_of_the_guard_exists(self) -> None:
        # The hook in the local file still counts; the shared file that cannot be read needs a look.
        self.install_scripts()
        self.merge_hook()
        self.write(".claude/settings.json", "{not json")
        guard = self.report()
        self.assertEqual(guard["state"], "installed")
        self.assertEqual(guard["hook"]["unreadable"], [".claude/settings.json"])
        self.assertEqual(guard["actions"], ["review_settings"])
        # Scripts and only an unreadable local file: whether a hook runs them cannot be told.
        self.new_vault()
        self.install_scripts()
        self.write(".claude/settings.local.json", "{not json")
        guard = self.report(offer=True)
        self.assertEqual((guard["state"], guard["hook"]["state"]), ("needs_review", "unreadable"))
        self.assertEqual(guard["actions"], ["review_guard", "review_settings"])

    def test_linked_scripts_directory_is_not_followed(self) -> None:
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        for asset in (DENY_GUARD, DENY_GUARD_PARSER):
            (outside / asset.name).write_text(asset.read_text(encoding="utf-8"), encoding="utf-8")
        (self.vault / "00-meta").mkdir(exist_ok=True)
        try:
            os.symlink(outside, self.vault / "00-meta" / "scripts", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        self.merge_hook()
        guard = self.report(offer=True)
        self.assertEqual((guard["guard"]["state"], guard["parser"]["state"]), ("unsafe_path", "unsafe_path"))
        self.assertEqual(guard["state"], "needs_review")
        self.assertEqual(guard["actions"], ["review_guard"])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    def test_junctioned_scripts_and_settings_folders_are_not_followed(self) -> None:
        outside = Path(self._tmp.name) / "outside-junction"
        (outside / "scripts").mkdir(parents=True)
        (outside / "claude").mkdir()
        for asset in (DENY_GUARD, DENY_GUARD_PARSER):
            (outside / "scripts" / asset.name).write_text(asset.read_text(encoding="utf-8"), encoding="utf-8")
        (outside / "claude" / "settings.local.json").write_text(DENY_GUARD_HOOKS.read_text(encoding="utf-8"),
                                                                 encoding="utf-8")
        (self.vault / "00-meta").mkdir(exist_ok=True)
        make_junction(self.vault / "00-meta" / "scripts", outside / "scripts")
        self.merge_hook()
        guard = self.report(offer=True)
        self.assertEqual((guard["guard"]["state"], guard["parser"]["state"]), ("unsafe_path", "unsafe_path"))
        self.assertEqual(guard["state"], "needs_review")
        self.assertEqual(guard["actions"], ["review_guard"])
        # A junctioned .claude folder is not read, and nothing is offered into it.
        self.new_vault()
        make_junction(self.vault / ".claude", outside / "claude")
        guard = self.report(offer=True)
        self.assertEqual(guard["hook"]["unreadable"], [".claude/settings.json", ".claude/settings.local.json"])
        self.assertEqual(guard["state"], "absent")
        self.assertEqual(guard["actions"], [])

    def test_guard_tools_and_paths_match_the_shipped_guard_and_template(self) -> None:
        constants = _guard_constants()
        group = self.template()["hooks"]["PreToolUse"][0]
        self.assertEqual(len(set(vault_adapters.GUARD_TOOLS)), len(vault_adapters.GUARD_TOOLS))
        self.assertEqual(set(vault_adapters.GUARD_TOOLS), set(group["matcher"].split("|")))
        self.assertEqual(set(vault_adapters.GUARD_TOOLS),
                         set(constants["PATH_TOOLS"]) | set(constants["SHELL_TOOLS"]))
        self.assertEqual((vault_adapters.GUARD_REL, vault_adapters.PARSER_REL),
                         (constants["GUARD_REL"], constants["PARSER_REL"]))
        self.assertEqual(vault_adapters.BUNDLED_GUARD.resolve(), DENY_GUARD.resolve())
        self.assertEqual(vault_adapters.BUNDLED_PARSER.resolve(), DENY_GUARD_PARSER.resolve())
        # The shipped hook passes the report's own checks, and neither adapter claims the other's hook.
        handler = group["hooks"][0]
        self.assertTrue(vault_adapters._is_guard_handler(handler))
        self.assertFalse(vault_adapters._is_aside_handler(handler))
        self.assertEqual(vault_adapters._guard_problems(group, handler), [])
        aside_hooks = json.loads((ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))
        for aside_handler in vault_adapters._hook_handlers(aside_hooks):
            self.assertFalse(vault_adapters._is_guard_handler(aside_handler))

    def test_text_output_adds_guard_lines_after_the_aside_lines(self) -> None:
        self.install_scripts()
        self.merge_hook(self.modified_template(onFailure=None))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = vault_adapters.main(["--vault", str(self.vault)])
        self.assertEqual(code, 0)
        guard, parser = _first_line_stamp(DENY_GUARD), _first_line_stamp(DENY_GUARD_PARSER)
        self.assertEqual(buffer.getvalue().splitlines()[7:], [
            "deny_guard.state: needs_review",
            f"deny_guard.guard: current (installed={guard}, bundled={guard})",
            f"deny_guard.parser: current (installed={parser}, bundled={parser})",
            "deny_guard.hook: needs_review",
            "deny_guard.actions: review_settings",
            'deny_guard.hook.problem: .claude/settings.local.json: deny-guard handler without "onFailure": '
            '"block", so a crash, a timeout or a missing python lets the tool call run',
        ])


class AdapterReportCompatibilityTests(_VaultCase):
    """The deny_guard entry sits next to aside; what v0.31.0 reported stays exactly as it was."""

    def test_aside_entry_and_text_lines_match_v0310(self) -> None:
        report, code = vault_adapters.diagnose(self.vault)
        stamp = vault_adapters._stamp(HELPER.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual((report["schema_version"], report["status"], report["platform"]), (1, "ok", "windows"))
        self.assertEqual(set(report), {"schema_version", "status", "platform", "adapters"})
        self.assertEqual(report["adapters"]["aside"], {
            "cli_on_path": False,
            "helper_supported": True,
            "helper": {"path": "00-meta/scripts/aside-up.ps1", "bundled_stamp": stamp, "installed_stamp": None,
                       "state": "missing"},
            "clause": {"path": "CLAUDE.md", "state": "missing"},
            "hook": {"state": "absent", "configured_in": [], "unreadable": [], "problems": []},
            "guide_note": {"path": "20-knowledge/tools/Aside CLI 운영 가이드.md", "present": False,
                           "unsafe_path": False},
            "actions": [],
        })
        self.assertEqual(vault_adapters._format_text(report).splitlines()[:7], [
            "agentic-vault adapters: ok (platform=windows)",
            "aside.cli_on_path: false",
            f"aside.helper: missing (installed=None, bundled={stamp})",
            "aside.clause: missing",
            "aside.hook: absent",
            "aside.guide_note: false",
            "aside.actions: none",
        ])

    def test_installing_the_guard_changes_no_aside_field(self) -> None:
        aside_hooks = json.loads((ASSETS / "settings-hooks.json").read_text(encoding="utf-8"))
        self.write("CLAUDE.md", "# vault\n\n" + (ASSETS / "browser-clause.md").read_text(encoding="utf-8"))
        self.write(vault_adapters.HELPER_REL, HELPER.read_text(encoding="utf-8"))
        self.write(vault_adapters.GUIDE_REL, "---\ntitle: x\n---\n")
        self.merge_hook(aside_hooks)
        with mock.patch.object(vault_adapters.shutil, "which", return_value="C:/aside/aside.exe"):
            before, _ = vault_adapters.diagnose(self.vault)
            self.install_scripts()
            merged = json.loads(json.dumps(aside_hooks))
            merged["hooks"]["PreToolUse"] += self.template()["hooks"]["PreToolUse"]
            self.merge_hook(merged)
            after, _ = vault_adapters.diagnose(self.vault)
        self.assertEqual(after["adapters"]["aside"], before["adapters"]["aside"])
        self.assertEqual(after["adapters"]["aside"]["hook"]["state"], "configured")
        self.assertEqual(after["adapters"]["aside"]["actions"], [])
        self.assertEqual(vault_adapters._format_text(after).splitlines()[:7],
                         vault_adapters._format_text(before).splitlines()[:7])
        self.assertEqual((before["adapters"]["deny_guard"]["state"], after["adapters"]["deny_guard"]["state"]),
                         ("absent", "installed"))

    def test_not_a_vault_answer_is_unchanged(self) -> None:
        empty = Path(self._tmp.name) / "plain"
        empty.mkdir()
        self.assertEqual(vault_adapters.diagnose(empty),
                         ({"schema_version": 1, "status": "not_vault", "reason_code": "config_not_found",
                           "adapters": {}}, 2))


class DenyGuardWiringTests(unittest.TestCase):
    """Only /vault-init offers the guard; the docs say where it applies and where it stops."""

    @staticmethod
    def read(rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_only_vault_init_runs_the_report_with_the_offer_flag(self) -> None:
        runs: dict[str, list[bool]] = {}
        for path in sorted((*(ROOT / "commands").glob("*.md"), *(ROOT / "skills").rglob("*.md"))):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "vault_adapters.py" in line and "--vault" in line:
                    runs.setdefault(path.relative_to(ROOT).as_posix(), []).append("--offer-deny-guard" in line)
        self.assertEqual({rel for rel, flags in runs.items() if any(flags)}, {"commands/vault-init.md"})
        self.assertEqual(runs["commands/vault-init.md"].count(True), 1)  # the 4-2 step only
        for rel in ("commands/vault-doctor.md", "commands/vault-upgrade.md",
                    "skills/agentic-vault/references/codex.md"):
            with self.subTest(caller=rel):
                self.assertIn(rel, runs)
                self.assertNotIn(True, runs[rel])

    def test_vault_init_asks_for_the_guard_in_its_own_step_after_aside(self) -> None:
        text = self.read("commands/vault-init.md")
        aside = text.index("## 4-1. 도구 어댑터")
        start = text.index("## 4-2. deny zone 가드")
        end = text.index("## 5. 권한 병합")
        self.assertLess(aside, start)
        self.assertLess(start, end)
        step = text[start:end]
        for literal in ("Codex에서는 이 단계를 생략한다", "--offer-deny-guard", "`offer_deny_guard`", "**따로**",
                        "거부하거나 답이 없으면 아무것도 복사·병합하지 않는다", "00-meta/scripts/deny-guard.py",
                        "00-meta/scripts/ps-parse.ps1", "settings-hooks.json", "`.claude/settings.local.json`",
                        '"onFailure": "block"', "v2.1.295", "fail-open", "우회하지 말고"):
            with self.subTest(literal=literal):
                self.assertIn(literal, step)
        self.assertIn("(4-1·4-2·5·6)", text)
        self.assertNotIn("offer_deny_guard", text[aside:start])

    def test_default_templates_carry_no_guard(self) -> None:
        # /vault-init copies these into every new vault; the guard comes only from its own opt-in step.
        for path in sorted((ROOT / "assets" / "templates").rglob("*")):
            if path.is_file():
                with self.subTest(template=path.relative_to(ROOT).as_posix()):
                    text = path.read_text(encoding="utf-8")
                    self.assertNotIn("deny-guard", text)
                    self.assertNotIn("ps-parse", text)
        init = self.read("commands/vault-init.md")
        self.assertNotIn("deny-guard", init[init.index("## 3. 템플릿 복사"):init.index("## 4-1.")])

    def test_doctor_and_upgrade_report_the_guard_without_offering_it(self) -> None:
        doctor = self.read("commands/vault-doctor.md")
        for literal in ("adapters.deny_guard", "`absent`·`installed`·`incomplete`·`needs_review`",
                        "Doctor는 가드를 제안하지 않는다", "`offer_deny_guard`는 나오지 않고"):
            with self.subTest(doc="vault-doctor.md", literal=literal):
                self.assertIn(literal, doctor)
        upgrade = self.read("commands/vault-upgrade.md")
        for literal in ("`adapters.deny_guard.actions`", "`offer_guard_update`", "`review_guard`",
                        "Codex는 건너뛴다", "가드를 새로 설치하거나 제안하지 않는다",
                        "`agentic-vault:adapter deny-guard engine=`"):
            with self.subTest(doc="vault-upgrade.md", literal=literal):
                self.assertIn(literal, upgrade)

    def test_security_policy_and_codex_docs_state_scope_trust_and_limits(self) -> None:
        security = self.read("SECURITY.md")
        scope = security[security.index("## Scope"):security.index("## Known limitations")]
        self.assertIn("**Optional deny-zone guard**", scope)
        limits = security[security.index("## Known limitations"):security.index("## 한국어 요약")]
        for literal in ("**The deny-zone guard is a guard, not a sandbox.**",
                        "**The deny-zone guard lives in the vault.**", "Join-Path 90 -assets", "v2.1.295",
                        "fail-open", "spawned process", "junction swapped", "Grep or Glob calls without a path"):
            with self.subTest(section="limits", literal=literal):
                self.assertIn(literal, limits)
        summary = security[security.index("## 한국어 요약"):]
        for literal in ("선택형 deny zone 가드 훅·스크립트", "가드이지 샌드박스가 아니다", "v2.1.295", "fail-open"):
            with self.subTest(section="summary", literal=literal):
                self.assertIn(literal, summary)
        self.assertIn("(PreToolUse 훅)은 Claude Code 전용이다", self.read("docs/codex.md"))
        self.assertIn("deny zone 가드(`adapters.deny_guard`)도 Claude Code 전용이다",
                      self.read("skills/agentic-vault/references/codex.md"))


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
