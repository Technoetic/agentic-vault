"""Release metadata consistency and historical contract regressions."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
README = REPO_ROOT / "README.md"
RELEASE_NOTE_V082 = REPO_ROOT / "docs" / "releases" / "v0.8.2.md"
RELEASE_NOTE_V083 = REPO_ROOT / "docs" / "releases" / "v0.8.3.md"
RELEASE_NOTE_V084 = REPO_ROOT / "docs" / "releases" / "v0.8.4.md"
RELEASE_NOTE_V090 = REPO_ROOT / "docs" / "releases" / "v0.9.0.md"
HEALTHCHECK_SCRIPT = (
    REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
)

EXPECTED = "0.9.0"
EXPECTED_BADGE_LINE = (
    "[![Version](https://img.shields.io/badge/v0.9.0-10B981?style=for-the-badge)]"
    "(docs/releases/v0.9.0.md)"
)
EXPECTED_TREE_LINE = (
    "├── .claude-plugin/                    "
    "← plugin.json · marketplace.json (v0.9.0 · MIT)"
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
REQUIRED_V082_RELEASE_SECTIONS = (
    "## 보안 수정",
    "## 데이터 내구성",
    "## commit gate",
    "## 하위호환",
    "## 업그레이드",
    "## 검증",
    "## 알려진 경계",
    "## 미포함 v0.9.0 항목",
)
REQUIRED_V082_RELEASE_LITERALS = (
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
REQUIRED_V083_RELEASE_SECTIONS = (
    "## index 서술 규격",
    "## 기각 대장 (rejected-proposal ledger)",
    "## 승격 검증 게이트 + 가역 롤백",
    "## 하위호환",
    "## 업그레이드",
    "## 검증",
    "## 알려진 경계",
    "## 미포함 v0.9.0 항목",
)
REQUIRED_V083_RELEASE_LITERALS = (
    "arXiv 2608.27454",
    "`promotion_review_sessions`",
    "승격완료(검증중→대상, 승격일, anchor)",
    "롤백(사유)",
    "조항은 가역, 대장은 영속",
    "`(재발)`",
    "## 기각 대장",
    "engine=0.8.3",
    "제목 재진술 금지",
    "git log --oneline origin/master..HEAD",
)
# v0.8.3 behavioral-contract anchors: the promotion probation gate and the
# rejection ledger must stay wired into the session-end command and templates.
REQUIRED_SESSION_END_LITERALS = (
    "0. **승격 검증 게이트**",
    "`promotion_review_sessions`",
    "상태를 `롤백(사유)`로 바꿔라",
    "`(재발)` 마커",
    "**초안을 쓰기 전에 대장의 '기각 대장' 섹션(있으면)에서 같은 교훈의 "
    "이전 기각 사유를 읽어라**",
    "승격완료(검증중→대상, 승격일 오늘, anchor",
    "요약 본문은 `세션 종료 — `로 시작하라",
    "커밋 수에서 1을 뺀 값",
)
# Index description spec must stay wired into every registration path —
# the release note advertises "등록 경로 4곳에 일관 적용".
REQUIRED_INDEX_SPEC_WIRING = (
    ("skills/agentic-vault/SKILL.md", "열지 말지"),
    ("commands/vault-ingest.md", "열지 말지"),
    ("commands/vault-trace.md", "문제+근본 원인+처방"),
    ("commands/vault-lint.md", "열지 말지"),
    ("assets/templates/index.md", "열지 말지"),
    ("assets/templates/rules/vault-workflow.md", "열지 말지"),
)
REQUIRED_LESSONS_TEMPLATE_LITERALS = (
    "## 기각 대장 — 기각된 승격 초안의 전문 보존",
    "승격완료(검증중→대상, 승격일, anchor)",
    "구판 표기 `승격완료(→대상)`(승격일·anchor 없음)는 검증을 통과한 "
    "확정분으로 간주한다",
)
# rules 템플릿은 v0.8.4에서 내용 무변경 — 스탬프는 마지막 내용 변경 버전(0.8.3)에
# 머무는 것이 옳다(/vault-upgrade가 스탬프 숫자 비교로 교체 여부를 판단하므로,
# 내용이 같은데 스탬프만 올리면 전 볼트에 무의미한 교체를 유발한다).
REQUIRED_WORKFLOW_RULE_STAMP = "agentic-vault:rule engine=0.8.3"
REQUIRED_HEALTHCHECK_STAMP = "agentic-vault:healthcheck engine=0.9.0"
REQUIRED_V084_RELEASE_SECTIONS = (
    "## 수정 1 — config 경로 필드의 deny zone 우회 차단 (high)",
    "## 수정 2 — 노트 읽기 실패 fail-soft + §14 신설 (low)",
    "## 하위호환",
    "## 검증",
)
REQUIRED_V084_RELEASE_LITERALS = (
    "`_PATH_KEYS` 7종",
    "engine=0.8.4",
    "§14 읽기 실패",
    "exit 1",
)


REQUIRED_V090_RELEASE_SECTIONS = (
    "## 변경",
    "### Claude Code · Codex 겸용 (local.2)",
    "### 신뢰성 (local.1)",
    "## 하위호환·업그레이드",
    "## 설치",
    "## 검증",
    "## 알려진 한계",
)
REQUIRED_V090_RELEASE_LITERALS = (
    "Claude Code, Codex 겸용",
    "`0.9.0-local.1`",
    "`0.9.0-local.2`",
    "engine=0.9.0",
    "engine=0.8.3",
    "--ref v0.9.0",
    "`/vault-recall`",
    "SHA-256",
    "예산 **0은 주입 비활성화**",
    "BrokenBarrierError",
)

class ReleaseMetadataTests(unittest.TestCase):
    def test_active_version_surfaces_match_release(self) -> None:
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
        codex = json.loads((REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        codex_market = json.loads((REPO_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["version"], EXPECTED)
        self.assertEqual(codex["name"], plugin["name"])
        entry = codex_market["plugins"][0]
        self.assertEqual(entry["name"], codex["name"])
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual((REPO_ROOT / entry["source"]["path"]).resolve(), REPO_ROOT)
        self.assertTrue((REPO_ROOT / codex["skills"] / "agentic-vault/SKILL.md").is_file())
        self.assertTrue((REPO_ROOT / "docs/releases" / f"v{EXPECTED}.md").is_file())
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
            RELEASE_NOTE_V082.is_file(),
            f"missing release note: {RELEASE_NOTE_V082.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V082.read_text(encoding="utf-8")
        release_lines = release.splitlines()

        self.assertIn("0.8.2", release)
        self.assertIn(EXPECTED_JARVIS_BOUNDARY_LINE, release_lines)
        for section in REQUIRED_V082_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V082_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        for line in EXPECTED_SCOPE_UPGRADE_LINES:
            with self.subTest(line=line):
                self.assertIn(line, release_lines)
        self.assertIn(EXPECTED_LEGACY_BRIEFING_UPGRADE_LINE, release_lines)

    def test_release_note_records_v083_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V083.is_file(),
            f"missing release note: {RELEASE_NOTE_V083.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V083.read_text(encoding="utf-8")

        self.assertIn("0.8.3", release)
        for section in REQUIRED_V083_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V083_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)

    def test_v083_loop_contract_is_wired(self) -> None:
        session_end = (REPO_ROOT / "commands" / "vault-session-end.md").read_text(
            encoding="utf-8"
        )
        lessons = (
            REPO_ROOT / "assets" / "templates" / "lessons.md"
        ).read_text(encoding="utf-8")
        workflow_rule = (
            REPO_ROOT / "assets" / "templates" / "rules" / "vault-workflow.md"
        ).read_text(encoding="utf-8")

        for literal in REQUIRED_SESSION_END_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, session_end)
        for literal in REQUIRED_LESSONS_TEMPLATE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, lessons)
        self.assertIn(REQUIRED_WORKFLOW_RULE_STAMP, workflow_rule)

    def test_v083_index_spec_is_wired(self) -> None:
        for rel_path, literal in REQUIRED_INDEX_SPEC_WIRING:
            with self.subTest(path=rel_path):
                content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                self.assertIn(literal, content)

    def test_release_note_records_v084_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V084.is_file(),
            f"missing release note: {RELEASE_NOTE_V084.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V084.read_text(encoding="utf-8")

        self.assertIn("0.8.4", release)
        for section in REQUIRED_V084_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V084_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        script = HEALTHCHECK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(REQUIRED_HEALTHCHECK_STAMP, script)
        self.assertIn('ENGINE_VERSION = "0.9.0"', script)

    def test_release_note_records_v090_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V090.is_file(),
            f"missing release note: {RELEASE_NOTE_V090.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V090.read_text(encoding="utf-8")

        self.assertIn("v0.9.0", release)
        for section in REQUIRED_V090_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V090_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        # 사전 릴리스 식별자가 활성 표면(매니페스트·README 배지·트리·Codex ref)에 남아 있으면 안 된다
        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json"):
            with self.subTest(path=rel):
                self.assertNotIn("local.2", (REPO_ROOT / rel).read_text(encoding="utf-8-sig"))
        readme = README.read_text(encoding="utf-8")
        self.assertNotIn("--ref v0.9.0-local.2", readme)
        self.assertNotIn("v0.9.0--local.2", readme)
        self.assertNotIn("releases/tag/v0.9.0-local.2", readme)
        self.assertIn("(docs/releases/v0.9.0.md)", readme)
