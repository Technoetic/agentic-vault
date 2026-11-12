"""The contributor guide at the repository root stays true to the repository.

`AGENTS.md` tells people and coding agents who change this repository which
files carry deliberate verbatim copies, which files the warning-promotion
certificate binds and which commands CI runs; `CLAUDE.md` imports it for
Claude Code. These tests read the guide and the repository side by side, so a
new marked copy, a renamed test or a changed CI command fails here until the
guide follows. The guide is not a plugin component and is never installed into
a vault.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
MANIFESTS = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json",
             ".agents/plugins/marketplace.json")
MARKER = re.compile(r"^# --- BEGIN (.+?)(?: \(| ---)", re.MULTILINE)
MARKERS_TODAY = {"SHARED PATH RULE", "SHARED ZONE RULE", "SHARED WORKTREE RULE", "ATX HEADING"}
# A test module path or a test name written as inline code in the guide.
NAMED_TEST = re.compile(r"`tests/(\w+)\.py`|`((?:[A-Z]\w*\.)?test_\w+)`")
DOTTED_MODULE = re.compile(r"\btests\.(test_\w+)\b")
OWNERSHIP = re.compile(r"(?i)\byou own\b|\byour own code\b|\bthe code is yours\b|\bowner and long-term maintainer\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EXAMPLE_DOMAIN = re.compile(r"(?i).*@(?:[\w-]+\.)*example\.(?:invalid|com|org)")
TEXT_BLOCK = re.compile(r"^```text\n(.*?)^```$", re.MULTILINE | re.DOTALL)
QUICK_COMMAND = "python -m unittest tests.test_rule_sources -v"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _scanned_sources() -> list[Path]:
    """Every engine Python file that runs alone or holds a rule: scripts, adapters and hooks."""
    paths = {*SCRIPTS.rglob("*.py"), *(ROOT / "assets" / "adapters").glob("*/**/*.py"), *(ROOT / "hooks").rglob("*.py")}
    return sorted(path for path in paths if "__pycache__" not in path.parts)


def _definitions(module: Path) -> set[str]:
    """`Class.method` and bare function or method names defined in a test module."""
    names: set[str] = set()
    for node in ast.parse(_text(module)).body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{item.name}")
                    names.add(item.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _workflow_run_values() -> list[str]:
    """The `run:` values of the CI workflow; block scalars are folded (`>`) or kept line by line (`|`)."""
    lines = _text(WORKFLOW).split("\n")
    values: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[index])
        index += 1
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2).strip()
        if value in ("|", "|-", ">", ">-"):
            block = []
            while index < len(lines) and (not lines[index].strip()
                                          or len(lines[index]) - len(lines[index].lstrip()) > indent):
                block.append(lines[index].strip())
                index += 1
            value = ("\n" if value.startswith("|") else " ").join(line for line in block if line)
        values.append(value)
    return values


class ContributorGuideTests(unittest.TestCase):
    def _read(self, name: str) -> str:
        path = ROOT / name
        self.assertTrue(path.is_file(), f"{name} is missing at the repository root")
        return _text(path)

    def test_claude_md_imports_the_guide(self) -> None:
        self.assertEqual(self._read("CLAUDE.md"), "# agentic-vault repository\n\n@AGENTS.md\n")

    def test_guide_lists_every_marked_copy(self) -> None:
        guide = self._read("AGENTS.md")
        holders: dict[str, set[str]] = {}
        for path in _scanned_sources():
            for name in MARKER.findall(_text(path)):
                holders.setdefault(name, set()).add(path.name)
        self.assertTrue(MARKERS_TODAY <= set(holders), sorted(holders))
        for name, files in sorted(holders.items()):
            with self.subTest(marker=name):
                self.assertIn(name, guide)
                for file_name in sorted(files):
                    self.assertIn(file_name, guide, f"{file_name} holds the {name} marker")

    def test_guide_lists_every_fold_path_part_copy(self) -> None:
        guide = self._read("AGENTS.md")
        holders = sorted(path.name for path in _scanned_sources() if "def fold_path_part" in _text(path))
        self.assertIn("vault_paths.py", holders)
        for file_name in holders:
            with self.subTest(file=file_name):
                self.assertIn(file_name, guide)

    def test_guide_names_tests_that_exist(self) -> None:
        # A test name is looked up in the test module named nearest before it in
        # reading order: in its table cell or sentence when that names a module
        # first, otherwise in the module of the text it follows ("The same tests
        # also fix ...").
        guide = self._read("AGENTS.md")
        current: Path | None = None
        modules: set[str] = set()
        names: list[str] = []
        for match in NAMED_TEST.finditer(guide):
            if match.group(1):
                current = ROOT / "tests" / f"{match.group(1)}.py"
                self.assertTrue(current.is_file(), f"the guide names tests/{match.group(1)}.py")
                modules.add(match.group(1))
                continue
            name = match.group(2)
            names.append(name)
            with self.subTest(test=name):
                self.assertIsNotNone(current, f"{name} is named before any test module")
                self.assertIn(name, _definitions(current), f"{name} is not defined in {current.name}")
        self.assertTrue({"test_rule_sources", "test_vault_worktrees"} <= modules, sorted(modules))
        self.assertTrue(names)
        for module in sorted(set(DOTTED_MODULE.findall(guide))):
            with self.subTest(module=module):
                self.assertTrue((ROOT / "tests" / f"{module}.py").is_file(), f"the guide runs tests.{module}")

    def test_guide_names_every_certificate_bound_file(self) -> None:
        guide = self._read("AGENTS.md")
        tree = ast.parse(_text(SCRIPTS / "vault_warning_policy.py"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "checker_sha256")
        loops = [node for node in ast.walk(function)
                 if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List))]
        self.assertEqual(len(loops), 1)
        bound = [ast.literal_eval(element) for element in loops[0].iter.elts]
        self.assertEqual(len(bound), 7, "the guide says the certificate hashes seven files")
        for file_name in bound:
            with self.subTest(file=file_name):
                self.assertIn(file_name, guide)

    def test_guide_commands_are_ci_commands(self) -> None:
        guide = self._read("AGENTS.md")
        runs = _workflow_run_values()
        blocks = TEXT_BLOCK.findall(guide)
        self.assertEqual(guide.count("```text\n"), 1)
        self.assertEqual(len(blocks), 1)
        commands = [line.strip() for line in blocks[0].split("\n") if line.strip()]
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, runs)
        prose = TEXT_BLOCK.sub("", guide)
        stateful = [span for span in re.findall(r"`([^`\n]+)`", prose) if "tests.test_stateful_" in span]
        workflow_stateful = [value for value in runs if "tests.test_stateful_" in value]
        self.assertEqual(len(workflow_stateful), 1)
        self.assertEqual(stateful, workflow_stateful)
        self.assertIn(f"`{QUICK_COMMAND}`", prose)
        self.assertTrue((ROOT / "tests" / "test_rule_sources.py").is_file())

    def test_guide_is_public_safe_and_engine_neutral(self) -> None:
        texts = {name: self._read(name) for name in ("AGENTS.md", "CLAUDE.md")}
        for name, text in texts.items():
            with self.subTest(file=name):
                self.assertNotIn("agentic-vault:", text)
                self.assertIsNone(re.search(r"\b[A-Za-z]:[\\/]", text))
                for fragment in ("/Users/", "/home/", "\\Users\\"):
                    self.assertNotIn(fragment, text)
                for address in EMAIL.findall(text):
                    self.assertRegex(address, EXAMPLE_DOMAIN)
                self.assertIsNone(OWNERSHIP.search(text))
        self.assertLess(len(texts["AGENTS.md"].encode("utf-8")), 32768)

    def test_root_guides_are_not_plugin_components(self) -> None:
        # Both files must exist for the rest of this test to say anything.
        self._read("AGENTS.md")
        self._read("CLAUDE.md")
        for manifest in MANIFESTS:
            with self.subTest(manifest=manifest):
                text = _text(ROOT / manifest)
                json.loads(text)
                self.assertNotIn("AGENTS.md", text)
                self.assertNotIn("CLAUDE.md", text)
        self.assertFalse((ROOT / "SKILL.md").exists())
        self.assertNotIn("export-ignore", _text(ROOT / ".gitattributes"))


if __name__ == "__main__":
    unittest.main()
