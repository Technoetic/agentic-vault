"""Repository-wide static security and cross-host portability hygiene tests.

Inspired by ECC's static CI hygiene checks (affaan-m/ECC, MIT), written in
Python standard library only for agentic-vault:
1. Invisible Unicode smuggling / Trojan Source bidi control scan across tracked text files.
2. Personal local path leak scan across shipped runtime, packaging, and guide surfaces.
3. Cross-host skill token portability check (no Claude-only tokens in Codex/Antigravity skills).
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".json", ".yml", ".yaml", ".sh", ".ps1", ".txt", ".toml", ".in",
})
EXTENSIONLESS_TEXT_FILES = frozenset({
    "pre-commit", "pre-push", ".gitattributes", ".gitignore", "LICENSE",
})
PLACEHOLDER_USERS = frozenset({
    "example", "me", "user", "username", "you", "yourname", "yourusername",
    "your-username", "alice", "bob", "tester",
})
POSIX_USER_RE = re.compile(r"/Users/([A-Za-z][A-Za-z0-9._-]*)")
WIN_USER_RE = re.compile(r"[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)([A-Za-z][A-Za-z0-9._-]*)", re.I)
WORKTREE_PATH_RE = re.compile(r"[A-Za-z]:(?:\\{1,2}|/)[^/\\\s\"']*worktrees(?:\\{1,2}|/)", re.I)
WIN_TEMP_RE = re.compile(r"AppData(?:\\{1,2}|/)Local(?:\\{1,2}|/)Temp", re.I)
SHIPPED_PREFIXES = (
    "skills/",
    "hooks/",
    "scripts/",
    "assets/",
    "commands/",
    "antigravity/",
    ".claude-plugin/",
    ".codex-plugin/",
)
SHIPPED_ROOT_FILES = frozenset({"README.md", "SECURITY.md", "AGENTS.md"})
CLAUDE_ONLY_TOKEN_RE = re.compile(
    r"\$\{CLAUDE_(?:PLUGIN_ROOT|PROJECT_DIR|SESSION_ID|SKILL_DIR)\}|\$ARGUMENTS"
)


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    out: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", "__pycache__/")) or "/__pycache__/" in rel:
            continue
        out.append(rel)
    return sorted(out)


def _is_dangerous_invisible(cp: int) -> bool:
    return (
        cp in (
            0x00AD, 0x034F, 0x061C,
            0x115F, 0x1160, 0x180E,
            0x200B, 0x200C, 0x200E, 0x200F, 0x2060,
            0x3164, 0xFFA0,
        )
        or (0x2061 <= cp <= 0x2064)
        or (0x202A <= cp <= 0x202E)
        or (0x2066 <= cp <= 0x2069)
        or (0xE0000 <= cp <= 0xE007F)
        or (0xE0100 <= cp <= 0xE01EF)
    )


class RepoHygieneTests(unittest.TestCase):
    def test_no_invisible_unicode_smuggling_in_tracked_files(self) -> None:
        violations: list[str] = []
        for rel in _tracked_files():
            path = ROOT / rel
            if (path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in EXTENSIONLESS_TEXT_FILES) or not path.is_file():
                continue
            raw = path.read_bytes()
            bad: list[str] = []
            if raw.startswith(b"\xef\xbb\xbf"):
                bad.append("UTF-8 BOM(leading)")
            text = raw.decode("utf-8", errors="replace")
            bad.extend(sorted({f"U+{ord(ch):04X}" for ch in text if _is_dangerous_invisible(ord(ch))}))
            if not rel.startswith("tests/") and "\ufeff" in text[1:]:
                bad.append("U+FEFF(mid)")
            if bad:
                violations.append(f"{rel}: {', '.join(bad)}")
        self.assertEqual(violations, [], "Dangerous invisible Unicode characters or UTF-8 BOM found:\n" + "\n".join(violations))

    def test_dangerous_invisible_helper_detects_v0541_codepoints_and_preserves_benign(self) -> None:
        for cp in (0x00AD, 0x034F, 0x061C, 0x200E, 0x200F, 0x3164, 0xFFA0):
            with self.subTest(bad=f"U+{cp:04X}"):
                self.assertTrue(_is_dangerous_invisible(cp))
        for ch in ("A", "가", "\u26a0", "\ufe0f", "\u2705", "\u200d"):
            with self.subTest(benign=f"U+{ord(ch):04X}"):
                self.assertFalse(_is_dangerous_invisible(ord(ch)))

    def test_no_personal_paths_in_shipped_surfaces(self) -> None:
        violations: list[str] = []
        for rel in _tracked_files():
            in_top_docs = rel.startswith("docs/") and rel.count("/") == 1
            if not (rel.startswith(SHIPPED_PREFIXES) or rel in SHIPPED_ROOT_FILES or in_top_docs):
                continue
            path = ROOT / rel
            if path.suffix.lower() not in TEXT_EXTENSIONS or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            leaks: list[str] = []
            for regex in (POSIX_USER_RE, WIN_USER_RE):
                for match in regex.finditer(text):
                    if match.group(1).lower() not in PLACEHOLDER_USERS:
                        leaks.append(match.group(0))
            for regex in (WORKTREE_PATH_RE, WIN_TEMP_RE):
                for match in regex.finditer(text):
                    leaks.append(match.group(0))
            if leaks:
                violations.append(f"{rel}: {', '.join(leaks[:5])}")
        self.assertEqual(violations, [], "Personal local paths leaked in shipped files:\n" + "\n".join(violations))

    def test_no_claude_only_tokens_in_codex_or_antigravity_skills(self) -> None:
        violations: list[str] = []
        for rel in _tracked_files():
            is_codex_skill = rel.startswith("skills/") and rel.endswith("/SKILL.md")
            is_agy_skill = rel.startswith("antigravity/skills/") and rel.endswith("/SKILL.md")
            is_codex_manifest = rel.startswith(".codex-plugin/") and rel.endswith((".json", ".md"))
            if not (is_codex_skill or is_agy_skill or is_codex_manifest):
                continue
            path = ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = sorted(set(CLAUDE_ONLY_TOKEN_RE.findall(text)))
            if hits:
                violations.append(f"{rel}: {', '.join(hits)}")
        self.assertEqual(violations, [], "Claude-only tokens leaked in cross-host skill/manifest:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
