"""Contract texts: the always-loaded architecture rule, the inbox completion condition and the hot wording.

The architecture rule is pinned byte for byte (CRLF folded to LF) to its v0.46.0 blob: the engine-copy ownership
line was tried with the wording test and not shipped, so the rule file and its stamp stay as they were, and the
other rule stamps stay where they were. `/vault-process-inbox` has a completion condition for the whole queue
(section 3-1): a file ends either processed or held with one of four reasons, a permission denial, a hook block or
a deny zone is not worked around, and the inbox is listed again before the report. The hot and handoff templates
and SKILL.md treat 500 words as an editing advice, not as a hard limit.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "assets" / "templates" / "rules"
TEMPLATES = ROOT / "assets" / "templates"
PROCESS_INBOX = ROOT / "commands" / "vault-process-inbox.md"
SKILL = ROOT / "skills" / "agentic-vault" / "SKILL.md"

# SHA-256 of `git show v0.46.0:assets/templates/rules/vault-architecture.md` with CRLF folded to LF.
V0460_ARCHITECTURE_SHA256 = "2194e02db12468586913bf7d2a535ec2eee93530955c9860148b9e9c90e59f46"

COMPLETION_HEADING = "## 3-1. 완료 조건"
RECORD_HEADING = "## 3. 기록"
REPORT_HEADING = "## 4. 보고"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class _TextCase(unittest.TestCase):
    """assertIn and assertNotIn print the whole command file on failure; these name only the literal."""

    def assert_has(self, literal: str, text: str) -> None:
        self.assertTrue(literal in text, f"missing: {literal!r}")

    def assert_lacks(self, literal: str, text: str) -> None:
        self.assertFalse(literal in text, f"still present: {literal!r}")


class ArchitectureRuleTests(unittest.TestCase):
    """Pins: the always-loaded contract is not edited in this release (the ownership line was not shipped)."""

    def test_architecture_rule_is_unchanged_from_v0460(self) -> None:
        text = _read(RULES / "vault-architecture.md")
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), V0460_ARCHITECTURE_SHA256)
        lines = text.split("\n")
        self.assertIn("agentic-vault:rule engine=0.6.0", lines[0])
        self.assertIn("500단어 핫 컨텍스트는", lines[5])
        for literal in ("엔진 사본", "엔진이 설치한 사본"):
            with self.subTest(literal=literal):
                self.assertFalse(any(literal in line for line in lines))

    def test_other_rule_stamps_did_not_move(self) -> None:
        for name, stamp in (("vault-linking.md", "engine=0.6.0"), ("vault-frontmatter.md", "engine=0.6.0"),
                            ("vault-workflow.md", "engine=0.43.0"), ("vault-collab.md", "engine=0.16.0"),
                            ("vault-browser.md", "engine=0.17.0")):
            with self.subTest(rule=name):
                first = _read(RULES / name).split("\n", 1)[0]
                self.assertIn("agentic-vault:rule " + stamp + " ", first)


class ProcessInboxCompletionTests(_TextCase):
    """`/vault-process-inbox` ends only when every file found in step 1 is processed or held with a reason."""

    def setUp(self) -> None:
        self.text = _read(PROCESS_INBOX)

    def section(self) -> str:
        self.assert_has(COMPLETION_HEADING, self.text)
        start = self.text.index(COMPLETION_HEADING)
        return self.text[start:self.text.index(REPORT_HEADING, start)]

    def step_two(self) -> str:
        start = self.text.index("## 2. 파일별 정제")
        return self.text[start:self.text.index(RECORD_HEADING, start)]

    def test_completion_section_sits_between_record_and_report(self) -> None:
        self.assert_has(COMPLETION_HEADING, self.text)
        self.assertEqual(self.text.count(COMPLETION_HEADING), 1)
        self.assertLess(self.text.index(RECORD_HEADING), self.text.index(COMPLETION_HEADING))
        self.assertLess(self.text.index(COMPLETION_HEADING), self.text.index(REPORT_HEADING))

    def test_hold_needs_failure_after_fix_and_recheck(self) -> None:
        self.assert_has("보완하고 다시 검사해도 `check_compile`이 통과하지 않음", self.section())
        self.assert_lacks("check_compile 미통과", self.text)
        self.assert_has("보완한 뒤 재검사하며 원본을 먼저 옮기지 않는다", self.text)

    def test_deliberate_blocks_are_not_ordinary_obstacles(self) -> None:
        section = self.section()
        for literal in ("권한 거부, 훅의 차단(deny zone 가드 포함), deny zone은 일반 장애가 아니다", "우회하지 말고",
                        "가드가 켜진 볼트의 `mv -n`"):
            with self.subTest(literal=literal):
                self.assert_has(literal, section)
        self.assert_has("Claude Code에서 deny zone 가드(`00-meta/scripts/deny-guard.py` 훅)를 켠 볼트는 `Move-Item`이 "
                      "막히므로 Bash 도구의 `mv -n`을 쓴다(이미 git이 추적하는 파일은 `git mv`로 옮기면 이동이 함께 "
                      "스테이징된다).", self.step_two())

    def test_relisting_sends_files_back_to_step_two(self) -> None:
        section = self.section()
        for literal in ("1과 같은 범위로", "그 파일로 2부터 다시 진행하고"):
            with self.subTest(literal=literal):
                self.assert_has(literal, section)

    def test_unrelated_files_continue(self) -> None:
        self.assert_has("그 답과 상관없는 나머지 파일은 계속 처리한다", self.section())

    def test_placeholder_files_are_not_queue_items(self) -> None:
        # /vault-init leaves empty `.gitkeep` files in the inbox subfolders. They fit neither ending of
        # section 3-1, so section 1 says, right after the underscore-folder line, that they are no queue items.
        start = self.text.index("## 1. 대기열 확인")
        lines = self.text[start:self.text.index("## 2. 파일별 정제", start)].split("\n")
        underscore = next(index for index, line in enumerate(lines) if line.startswith("- 언더스코어(`_`)로 시작하는 폴더"))
        self.assertEqual(lines[underscore + 1],
                         "- 내용 없는 자리표시 파일(`.gitkeep` 등)은 대기열 파일이 아니다 — 처리·이동·보고하지 않는다.")

    def test_report_lists_every_hold_reason(self) -> None:
        report = self.text[self.text.index(REPORT_HEADING):]
        self.assert_has("판독 불가·이름 충돌·보완 뒤에도 재검사 미통과·풀리지 않은 장애", report)

    def test_scan_scope_and_move_lines_are_unchanged(self) -> None:
        # Pin: the lines the completion condition refers to keep their v0.46.0 wording.
        for literal in ("- 언더스코어(`_`)로 시작하는 폴더 — 특히 `10-inbox/_processed/` — 와 `deny_zones` 경로는 "
                        "**절대 스캔 금지**.",
                        "호스트가 이미지를 읽지 못하면 추측하지 말고 판독 불가를 보고하고, 그 파일은 b~d를 진행하지 말고 "
                        "인박스에 그대로 남겨라",
                        "검토가 필요한 누락/의미 불확실성/해시 변경이면 보완한 뒤 재검사하며 원본을 먼저 옮기지 않는다.",
                        "이동 뒤에도 원본이 `10-inbox/`에 남아 있으면 그 파일은 처리를 멈추고 이름 충돌로 보고하라"):
            with self.subTest(literal=literal):
                self.assert_has(literal, self.text)


class HotWordingTests(_TextCase):
    """500 words is an editing advice for hot and handoff; the injection budget is `hot_max_tokens`."""

    def test_no_absolute_word_limit_for_hot_and_handoff(self) -> None:
        for path in (TEMPLATES / "hot.md", TEMPLATES / "handoff.md", SKILL):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assert_lacks("500단어 이내", _read(path))
        self.assert_has("hot_max_tokens", _read(TEMPLATES / "hot.md"))


if __name__ == "__main__":
    unittest.main()
