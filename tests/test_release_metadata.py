"""Release-surface contract tests for agentic-vault v0.8.2."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
README = REPO_ROOT / "README.md"
RELEASE_NOTE = REPO_ROOT / "docs" / "releases" / "v0.8.2.md"

EXPECTED = "0.8.2"
EXPECTED_BADGE_LINE = (
    "[![Version](https://img.shields.io/badge/v0.8.2-10B981?style=for-the-badge)]"
    "(https://github.com/Technoetic/agentic-vault/releases/tag/v0.8.2)"
)
EXPECTED_TREE_LINE = (
    "├── .claude-plugin/                    "
    "← plugin.json · marketplace.json (v0.8.2 · MIT)"
)
EXPECTED_HISTORICAL_ORIGINS = (
    "그래서 v0.8.0부터 healthcheck 섹션 11",
    "— v0.8.1부터 healthcheck 섹션 12",
)
EXPECTED_JARVIS_BOUNDARY_LINE = (
    "- 볼트·봇 네임스페이스마다 정확히 하나의 데몬을 실행해야 한다. "
    "예약 브리핑의 at-least-once 보장은 `pending` 커밋 이후에만 성립한다. "
    "`pending` 이전에 프로세스가 중단되면 재시작 시 지난 슬롯을 cold-start miss로 "
    "건너뛸 수 있고, Telegram 수락 후 응답 유실·일부 청크 전송·전송 성공 후 "
    "`fired` 기록 실패에서는 중복될 수 있다."
)
REQUIRED_README_LITERALS = (
    '`chat.type == "private"`',
    "`chat.id == from.id`",
)
REQUIRED_RELEASE_SECTIONS = (
    "## 보안 수정",
    "## 데이터 내구성",
    "## commit gate",
    "## 하위호환",
    "## 업그레이드",
    "## 검증",
    "## 알려진 경계",
    "## 미포함 v0.9.0 항목",
)
REQUIRED_RELEASE_LITERALS = (
    "`briefing_times`",
    "private chat",
    "process-then-ack",
    "offset",
    "staged index",
    "`--no-verify`",
    "`briefing_time`",
    "fallback",
    "git status --short --branch",
    "git log --oneline origin/master..HEAD",
)
EXPECTED_SCOPE_UPGRADE_LINES = (
    "2. 일반 누락 키 보충은 `frontmatter_roots`와 "
    "`frontmatter_exempt_paths`가 없을 때 그 부재를 보존한다. "
    "`frontmatter_exempt_paths`가 없고 기존 `fm_exempt_zones`가 있으면 "
    "호환 alias가 계속 적용되도록 새 키를 자동 추가하지 않는다.",
    "3. 두 범위 키를 추가하려면 full 모드 검사 범위가 어떻게 바뀌는지 "
    "먼저 확인하고, 별도의 검사 범위 마이그레이션으로 명시적으로 승인한다.",
)
EXPECTED_LEGACY_BRIEFING_UPGRADE_LINE = (
    "5. 기존 `jarvis.briefing_time`만 있는 볼트에는 일반 누락 키 보충으로 "
    "`briefing_times`를 추가하지 않는다. 별도 브리핑 일정 마이그레이션을 "
    "승인하면 기존 단일 시각을 배열의 유일한 값으로 옮기며, 템플릿 기본 "
    "`07:30`으로 바꾸지 않는다. 새 config나 `jarvis` 블록이 없는 config는 "
    "계속 새 템플릿의 `briefing_times` 기본값을 사용한다."
)


class ReleaseMetadataTests(unittest.TestCase):
    def test_active_version_surfaces_are_v082(self) -> None:
        plugin = json.loads(
            PLUGIN_MANIFEST.read_text(encoding="utf-8-sig")
        )
        market = json.loads(
            MARKETPLACE_MANIFEST.read_text(encoding="utf-8-sig")
        )
        readme = README.read_text(encoding="utf-8")
        readme_lines = readme.splitlines()

        self.assertEqual(plugin["version"], EXPECTED)
        self.assertEqual(market["plugins"][0]["version"], EXPECTED)
        self.assertIn(EXPECTED_BADGE_LINE, readme_lines)
        self.assertIn(EXPECTED_TREE_LINE, readme_lines)
        for origin in EXPECTED_HISTORICAL_ORIGINS:
            with self.subTest(origin=origin):
                self.assertIn(origin, readme)
        for literal in REQUIRED_README_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, readme)

    def test_release_note_records_v082_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE.is_file(),
            f"missing release note: {RELEASE_NOTE.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE.read_text(encoding="utf-8")
        release_lines = release.splitlines()

        self.assertIn(EXPECTED, release)
        self.assertIn(EXPECTED_JARVIS_BOUNDARY_LINE, release_lines)
        for section in REQUIRED_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        for line in EXPECTED_SCOPE_UPGRADE_LINES:
            with self.subTest(line=line):
                self.assertIn(line, release_lines)
        self.assertIn(EXPECTED_LEGACY_BRIEFING_UPGRADE_LINE, release_lines)
