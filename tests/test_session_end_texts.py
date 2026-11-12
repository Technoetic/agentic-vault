"""Texts of /vault-session-end and of the lessons and mistakes templates.

They tell an agent when a lesson or a mistakes entry may be retired, how a promotion draft states its scope
and reason, who reads the outputs of the session end, and when a remote push is allowed.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/agentic-vault/scripts'))

SESSION_END = ROOT / 'commands/vault-session-end.md'
LESSONS = ROOT / 'assets/templates/lessons.md'
MISTAKES = ROOT / 'assets/templates/mistakes.md'
RETIRED_FORM = 'retired(사유, YYYY-MM-DD)'


def read(path):
    return path.read_text(encoding='utf-8')


def between(text, start, end):
    """The text from the line holding start up to end, which must follow it."""
    first = text.index(start)
    return text[first:text.index(end, first)]


class CautionTextTests(unittest.TestCase):
    def test_mistakes_template_allows_retirement_with_reason_and_date(self):
        text = read(MISTAKES)
        self.assertNotIn('교훈은 만료되지 않는다', text)
        for literal in ('`상태: retired(사유, YYYY-MM-DD)`', '사용자에게 확인받은 뒤', '본문은 그대로 둔다',
                        '사유 없이 은퇴시키지 않는다', '이 줄은 표지 다음 줄에 달고',
                        '사유 없는 RETIRE도 적용해 표지 상태를 retired로 맞춘다'):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        # The template describes the lesson marker; it never carries the marker text itself.
        self.assertNotIn('agentic-vault:', text)
        self.assertEqual([line for line in text.splitlines() if line.lstrip().startswith('<!--')], [])

    def test_retired_token_is_the_ledger_token(self):
        for path in (MISTAKES, LESSONS):
            with self.subTest(path=path.name):
                self.assertIn(RETIRED_FORM, read(path))
        cell = '상태: retired(사유, 2026-11-12)'
        cues = importlib.import_module('vault_lesson_cues')
        self.assertTrue(cues.WITHDRAWN.search(cell))
        delta = importlib.import_module('vault_lesson_delta')
        self.assertTrue(delta.RETIRED_CELL.fullmatch(cell))

    def test_promotion_draft_states_scope_reason_source(self):
        lines = [line for line in read(SESSION_END).splitlines() if '승격 초안은 실행 가능한 조항으로 써라' in line]
        self.assertEqual(len(lines), 1)
        for literal in ('적용 범위', '이유', '출처', '전에 확인받는다', '의도한 차단'):
            with self.subTest(literal=literal):
                self.assertIn(literal, lines[0])
        # The attitude ban and the effort pointer of v0.46.0 stay.
        self.assertIn("'신중히 생각하라'·'단계별로 검토하라'·'조심한다'", lines[0])
        self.assertIn('호스트의 effort(추론 강도) 설정으로 조절한다', lines[0])

    def test_retire_reason_is_offered_with_approval(self):
        intro = between(read(SESSION_END), '## 5. 교훈 루프', '`00-meta/lessons.md`가 존재하면')
        for literal in ('RETIRE에 선택형 `reason`과 `date`(`YYYY-MM-DD`)를 함께 넘기면',
                        '이번 세션에서 확인했을 때만 제안하고', '사용자 승인 뒤에 적용한다', 'RETIRE는 삭제하지 않는다.'):
            with self.subTest(literal=literal):
                self.assertIn(literal, intro)


class SessionEndTextTests(unittest.TestCase):
    def reader_paragraph(self):
        lines = read(SESSION_END).splitlines()
        found = [index for index, line in enumerate(lines) if line.startswith('**읽는 사람:**')]
        self.assertEqual(len(found), 1, 'exactly one reader paragraph')
        return lines, found[0]

    def test_reader_paragraph_precedes_section_zero(self):
        lines, index = self.reader_paragraph()
        self.assertEqual(lines[index - 2:index], ['세션 종료 프로토콜을 수행하라.', ''])
        self.assertEqual(lines[index + 1:index + 3], ['', '## 0. 볼트 감지 (필수 게이트)'])
        first_section = min(number for number, line in enumerate(lines) if line.startswith('## '))
        self.assertLess(index, first_section)

    def test_reader_paragraph_keeps_required_identifiers(self):
        lines, index = self.reader_paragraph()
        paragraph = lines[index]
        for literal in ('그대로 적고', 'anchor', '`status`·`reason`·`lock`', '`current`'):
            with self.subTest(literal=literal):
                self.assertIn(literal, paragraph)
        self.assertNotIn('내부 ID와 저장소 내부 용어는 쓰지 말고', read(SESSION_END))

    def test_remote_push_bullet_states_the_exception_without_absolute(self):
        text = read(SESSION_END)
        self.assertNotIn('원격 push는 절대 금지', text)
        commit = between(text, '## 7. git 커밋', '## 8. 백업 권고')
        for literal in ('기밀 점검 후 명시 승인한 경우에만', '기밀 포함 여부를 먼저 경고하라',
                        'pre-push 훅이 로컬 경로 원격은 허용한다'):
            with self.subTest(literal=literal):
                self.assertIn(literal, commit)


if __name__ == '__main__':
    unittest.main()
