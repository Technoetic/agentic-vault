"""The plugin's commands and skills inject no shell command.

Claude Code runs an inline `!` followed by a backquoted command, and a fenced block
whose opening fence is followed by `!`, when a slash command or skill body is
loaded, before the model's turn. Text typed after the command name is substituted
into such a command as shell text, and these commands did not reach PreToolUse
hooks in the runs observed for SECURITY.md. The plugin's commands, skills and
command assets therefore carry neither form and grant no tools in front matter.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
# The two documented injection forms: `!` + backquote at a line start or after whitespace,
# and a backquote or tilde fence whose info string starts with `!`. Whitespace is read widely
# (any Unicode whitespace, such as a no-break or an ideographic space): Claude Code's own
# definition of whitespace was not measured.
INLINE = re.compile(r"(?m)(?:^|(?<=\s))!`")
FENCE = re.compile(r"(?m)^[ \t]*(?:`{3,}|~{3,})!")
COMMAND_ASSET = "assets/adapters/aside/aside-repl-command.md"


def scanned_files() -> list[Path]:
    """Every command, every skill file and every command asset an adapter copies into a vault."""
    return sorted({*(ROOT / "commands").glob("*.md"), *(ROOT / "skills").rglob("*.md"),
                   *(ROOT / "assets" / "adapters").glob("*/*-command.md")})


def front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 3)
    return text[4:end] if end != -1 else ""


class CommandBodyTests(unittest.TestCase):
    def test_plugin_commands_and_skills_inject_no_shell(self) -> None:
        files = scanned_files()
        self.assertIn("commands/vault-init.md", {path.relative_to(ROOT).as_posix() for path in files})
        for path in files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                # Offsets only, so a failure shows where without echoing a command.
                self.assertEqual([match.start() for match in INLINE.finditer(text)], [])
                self.assertEqual([match.start() for match in FENCE.finditer(text)], [])
                self.assertIsNone(re.search(r"(?m)^allowed-tools:", front_matter(text)))

    def test_scan_includes_the_aside_command_asset(self) -> None:
        self.assertIn(COMMAND_ASSET, {path.relative_to(ROOT).as_posix() for path in scanned_files()})

    def test_injection_patterns_match_the_documented_forms(self) -> None:
        def injected(text: str) -> bool:
            return bool(INLINE.search(text) or FENCE.search(text))

        for text in ("!`ls`", "see !`git status` now", "\t!`x`", "```!\nls\n```", "  ```!",
                     "a\N{NO-BREAK SPACE}!`x`", "가\N{IDEOGRAPHIC SPACE}!`x`"):
            with self.subTest(positive=text):
                self.assertTrue(injected(text))
        for text in ("`!`로", "KEY=!`cmd`", "a!`b`", "```bash"):
            with self.subTest(negative=text):
                self.assertFalse(injected(text))


if __name__ == "__main__":
    unittest.main()
