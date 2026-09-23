"""README numbers, version stamps and CI checks must match the repository.

Badges and prose counts drifted before (Commands 13 while commands/ held 14,
"5 rules" while six rule files shipped, a healthcheck stamp one release behind).
These tests derive every number from the files themselves, so a new command,
template or rule file fails here until the README says the same thing.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
HEALTHCHECK = ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
TEMPLATES = ROOT / "assets" / "templates"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# (quantity, pattern). Each pattern captures one count claim in README prose.
PROSE_CLAIMS = (
    ("commands", r"슬래시 명령 (\d+)종"),
    ("commands", r"(\d+)개 슬래시 커맨드"),
    ("commands", r"Ships (\d+) slash commands"),
    ("commands", r"all (\d+) are in \[commands/\]"),
    ("templates", r"템플릿 (\d+)종"),
    ("templates", r"(\d+) note/system templates"),
    ("rules", r"rules (\d+)종"),
    ("rules", r"행동 규칙 (\d+)종"),
    ("rules", r"`vault-\*\.md` (\d+)종"),
    ("rules", r"\b(\w+) engine-owned rule files"),
    ("rules", r"engine-owned rule files \((\w+) since"),
)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]


def manifest_version() -> str:
    manifest = ROOT / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8-sig"))["version"]


def actual_counts() -> dict[str, int]:
    return {
        "commands": len(list((ROOT / "commands").glob("*.md"))),
        # Top-level template files (notes, stubs, config, permissions); rules/ is separate.
        "templates": sum(1 for path in TEMPLATES.iterdir() if path.is_file()),
        "rules": len(list((TEMPLATES / "rules").glob("vault-*.md"))),
    }


def relative_links(text: str) -> list[str]:
    """Return Markdown link targets that point into the repository."""
    targets = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        targets.append(unquote(target.split("#", 1)[0]))
    return targets


class ReadmeCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = README.read_text(encoding="utf-8")
        self.counts = actual_counts()

    def test_counts_are_nonzero(self) -> None:
        for name, count in self.counts.items():
            with self.subTest(quantity=name):
                self.assertGreater(count, 0)

    def test_badges_match_files(self) -> None:
        commands = re.findall(r"badge/Commands-(\d+)-", self.readme)
        templates = re.findall(r"badge/Templates-(\d+)%2B(\d+)_rules-", self.readme)
        self.assertEqual(len(commands), 1, "exactly one Commands badge")
        self.assertEqual(len(templates), 1, "exactly one Templates badge")
        self.assertEqual(int(commands[0]), self.counts["commands"])
        self.assertEqual(int(templates[0][0]), self.counts["templates"])
        self.assertEqual(int(templates[0][1]), self.counts["rules"])

    def test_prose_counts_match_files(self) -> None:
        checked = {name: 0 for name in self.counts}
        for quantity, pattern in PROSE_CLAIMS:
            for match in re.finditer(pattern, self.readme):
                token = match.group(1)
                if not token.isdigit() and token.lower() not in NUMBER_WORDS:
                    continue
                checked[quantity] += 1
                with self.subTest(claim=match.group(0)):
                    self.assertEqual(_number(token), self.counts[quantity])
        for quantity, seen in checked.items():
            with self.subTest(quantity=quantity):
                self.assertGreater(seen, 0, f"no README prose claim found for {quantity}")

    def test_readme_tree_lists_every_template_file(self) -> None:
        tree = self.readme[self.readme.index("├── assets/templates/"):]
        tree = tree[:tree.index("```")]
        for path in TEMPLATES.iterdir():
            if path.is_file():
                with self.subTest(template=path.name):
                    self.assertIn(path.name, tree)


class VersionStampTests(unittest.TestCase):
    def test_stamp_engine_version_and_manifests_agree(self) -> None:
        text = HEALTHCHECK.read_text(encoding="utf-8")
        stamp = re.fullmatch(r"# agentic-vault:healthcheck engine=(\S+)", text.splitlines()[1])
        self.assertIsNotNone(stamp, "line 2 must carry the healthcheck engine stamp")
        engine = re.search(r'^ENGINE_VERSION = "([^"]+)"$', text, re.MULTILINE)
        self.assertIsNotNone(engine)
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8-sig"))
        market = json.loads(
            (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8-sig"))
        version = manifest_version()
        self.assertEqual(stamp.group(1), engine.group(1))
        self.assertEqual(engine.group(1), version)
        self.assertEqual(codex["version"], version)
        self.assertEqual(market["plugins"][0]["version"], version)

    def test_readme_version_badge_matches_manifest(self) -> None:
        version = manifest_version()
        readme = README.read_text(encoding="utf-8")
        badges = re.findall(r"img\.shields\.io/badge/v(\d+\.\d+\.\d+)-", readme)
        self.assertEqual(badges, [version])
        self.assertTrue((ROOT / "docs" / "releases" / f"v{version}.md").is_file())


class PublicDocsTests(unittest.TestCase):
    def test_security_policy_is_linked_and_current(self) -> None:
        security = ROOT / "SECURITY.md"
        self.assertTrue(security.is_file())
        text = security.read_text(encoding="utf-8")
        self.assertIn(f"| {manifest_version()} (latest) |", text)
        for heading in ("## Supported versions", "## Reporting a vulnerability",
                        "## Scope", "## Known limitations"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        self.assertIn("](SECURITY.md)", README.read_text(encoding="utf-8"))

    def test_english_overview_follows_korean_intro(self) -> None:
        readme = README.read_text(encoding="utf-8")
        overview = readme.index("## 🌐 English overview")
        self.assertLess(readme.index("## ⚡ 30초 안에 이해하기"), overview)
        self.assertLess(overview, readme.index("## 🎯 무엇을 해주는가"))
        self.assertIn("](#-english-overview)", readme)
        section = readme[overview:readme.index("<details>", overview)]
        for heading in ("**What it is.**", "**Install.**", "**Core commands**", "**Limits.**"):
            with self.subTest(heading=heading):
                self.assertIn(heading, section)

    def test_docs_index_covers_every_docs_entry(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for entry in (ROOT / "docs").iterdir():
            if entry.name == "README.md":
                continue
            with self.subTest(entry=entry.name):
                target = f"]({entry.name}/)" if entry.is_dir() else f"]({entry.name})"
                self.assertIn(target, index)

    def test_relative_links_resolve(self) -> None:
        for document in (README, ROOT / "SECURITY.md", ROOT / "docs" / "README.md",
                         ROOT / "docs" / "codex.md",
                         ROOT / "docs" / "releases" / f"v{manifest_version()}.md"):
            text = document.read_text(encoding="utf-8")
            for target in relative_links(text):
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target).exists(), target)


class ContinuousIntegrationTests(unittest.TestCase):
    def test_whitespace_check_covers_whole_committed_tree(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        runs = [line.strip() for line in workflow.splitlines()
                if line.strip().startswith("run: git diff --check")]
        # A bare `git diff --check` compares the fresh checkout with its index and
        # can never fail; comparing against the empty tree checks every tracked file.
        self.assertEqual(runs, [f"run: git diff --check {EMPTY_TREE} HEAD"])


if __name__ == "__main__":
    unittest.main()
