"""Read-only lesson clusters over a synthetic mistakes note."""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))

LEDGER = ('---\ntitle: Lessons\n---\n## 대장\n'
          '- 2026-10-01 | 횟수 3 | 렌더 결과를 먼저 본다 | 근거: M-910 인용 | 상태: 관찰중\n'
          '- 2026-10-02 | 횟수 1 | 칸이 하나 더 | 근거: M-920 | 상태: 관찰중 | 단서: x\n')


class ClusterBase(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('vault_lesson_clusters')
        except ModuleNotFoundError:
            self.fail('lesson cluster implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        }))
        self.write('00-meta/lessons.md', LEDGER)

    def write(self, name, text):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def run_clusters(self, body, **options):
        self.write('notes/mistakes.md', '---\ntitle: Mistakes\n---\n# 실수\n\n' + body)
        return self.module.cluster_lessons(self.vault, 'notes/mistakes.md', **options)

    def classes(self, report, status=None):
        return {tuple(c['ids']): c for c in report['classes'] if status in (None, c['status'])}


class ParsingTests(ClusterBase):
    def test_heading_separators_h3_and_bullet_entries_all_parse(self):
        report = self.run_clusters(
            '## M-901 · 가운뎃점 제목 (2026-09-01)\n본문\n\n'
            '## M-902 — 긴 줄표 제목 (2026-09-02)\n본문\n\n'
            '## M-903 - 붙임표 제목 (2026-09-03)\n본문\n\n'
            '### M-904 — 세 단계 제목 (2026-09-04)\n본문\n\n'
            '## 앞선 항목\n\n'
            '- **M-905 굵은 글머리 항목 (2026-09-05):** 본문\n'
            '- M-906 · 평문 글머리 항목 (2026-09-06)\n'
            '- M-907: 쌍점 글머리 항목\n')
        self.assertEqual(report['entries'], 7)
        self.assertEqual(report['diagnostics']['duplicate_ids'], [])

    def test_a_reference_bullet_is_not_an_entry(self):
        report = self.run_clusters('## M-901 · 하나\n- M-902와 같은 구조다.\n- 관련: M-902(설명)\n\n'
                                   '## M-902 · 둘\n본문\n')
        self.assertEqual(report['entries'], 2)

    def test_a_bullet_that_names_a_heading_entry_stays_in_the_body(self):
        report = self.run_clusters('## M-901 · 하나\n- **M-902 재발 (2026-10-02):** 또 났다\n\n'
                                   '## M-902 · 둘\n본문\n')
        self.assertEqual(report['entries'], 2)
        self.assertEqual(self.classes(report)[('M-901', 'M-902')]['count'], 2)

    def test_an_id_inside_a_code_fence_is_not_an_entry(self):
        report = self.run_clusters('## M-901 · 하나\n```\n## M-902 · 예시\n```\n')
        self.assertEqual(report['entries'], 1)

    def test_h3_with_the_same_number_joins_the_h2_entry(self):
        report = self.run_clusters('## M-901 · 하나 (2026-09-01)\n본문\n\n'
                                   '### M-901 재발 (2026-10-01) — 다시\n본문\n\n'
                                   '### M-901 보강 (2026-10-02) — 덧붙임\n본문\n')
        self.assertEqual(report['entries'], 1)
        item = self.classes(report)[('M-901',)]
        self.assertEqual(item['count'], 2)
        self.assertEqual((item['first_seen'], item['last_seen']), ('2026-09-01', '2026-10-01'))

    def test_a_reused_number_is_reported_and_does_not_fail_or_add_a_count(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-901 · 둘\n본문\n')
        self.assertEqual(report['entries'], 1)
        self.assertEqual(report['diagnostics']['duplicate_ids'], ['M-901'])
        self.assertEqual(report['classes'], [])

    def test_another_id_prefix_is_a_parameter(self):
        report = self.run_clusters('## ERR-1 · 하나\n본문\n\n## ERR-2 · ERR-1과 같은 구조\n', id_prefix='ERR')
        self.assertIn(('ERR-1', 'ERR-2'), self.classes(report))
        with self.assertRaises(self.module.LessonClusterError) as caught:
            self.run_clusters('## ERR-1 · 하나\n', id_prefix='a|b')
        self.assertEqual(str(caught.exception), 'invalid_id_prefix')


class ExplicitClassTests(ClusterBase):
    def test_same_class_words_join_entries_but_related_labels_do_not(self):
        report = self.run_clusters(
            '## M-901 · 하나\n본문\n\n'
            '## M-902 · 둘\nM-901과 같은 구조다.\n\n'
            '## M-903 · 셋\n- 같은 계열: M-901·M-902\n\n'
            '## M-904 · 넷\n- 관련: M-901(참고만)\n\n'
            '## M-905 · 다섯\n- 관련 실수: M-901, M-902\n\n'
            '## M-906 · 여섯\n같은 부류 M-901.\n')
        self.assertEqual(list(self.classes(report, 'explicit')), [('M-901', 'M-902', 'M-903', 'M-906')])
        self.assertEqual(self.classes(report)[('M-901', 'M-902', 'M-903', 'M-906')]['count'], 4)

    def test_a_related_clause_does_not_hide_a_same_class_clause_on_its_line(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-902 · 둘\n본문\n\n'
                                   '## M-903 · 셋\n관련: M-901. M-902와 같은 부류다.\n')
        self.assertEqual(list(self.classes(report, 'explicit')), [('M-902', 'M-903')])

    def test_number_followed_by_recurrence_word_is_a_same_class_edge(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-902 · 둘\nM-901 재발이다.\n\n'
                                   '## M-903 · 셋\nM-901의 재발 방지책을 썼다.\n\n'
                                   '## M-904 · 넷\nM-902 재발 조짐을 봤다.\n')
        self.assertEqual(list(self.classes(report, 'explicit')), [('M-901', 'M-902')])

    def test_a_list_before_the_class_word_joins_every_number_in_it(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-902 · 둘\n본문\n\n'
                                   '## M-903 · 셋\n앞의 일(M-901·M-902 계열)이다.\n')
        self.assertEqual(list(self.classes(report, 'explicit')), [('M-901', 'M-902', 'M-903')])

    def test_negated_same_class_is_not_an_edge(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-902 · 둘\nM-901과 같은 부류가 아니다.\n')
        self.assertEqual(self.classes(report, 'explicit'), {})

    def test_template_label_and_warning_markers_are_not_recurrences(self):
        report = self.run_clusters('## M-901 · 하나\n**재발 방지책:** 시험을 돈다\n\n- 🔁**재발 조짐(2026-10-01):** 걱정\n')
        self.assertEqual(report['classes'], [])

    def test_recurrence_markers_add_occurrences_and_dates(self):
        report = self.run_clusters('## M-901 · 하나 (2026-09-01)\n본문\n'
                                   '**재발(2026-09-15):** 또\n'
                                   '- **재발 2 (2026-10-20):** 또\n'
                                   '  - 🔁**재발 (2026-11-01):** 또\n')
        item = self.classes(report)[('M-901',)]
        self.assertEqual(item['status'], 'explicit')
        self.assertEqual(item['count'], 4)
        self.assertEqual((item['first_seen'], item['last_seen']), ('2026-09-01', '2026-11-01'))
        self.assertFalse(item['count_is_candidate'])

    def test_ordinal_raises_the_count_only_as_a_candidate(self):
        report = self.run_clusters('## M-901 · 하나\n같은 일이 재발했고 5번째다.\n\n'
                                   '## M-902 · 둘\n세 번째 재발이다.\n\n'
                                   '## M-903 · 셋\n3번째 시도에서 고쳤다.\n')
        first = self.classes(report)[('M-901',)]
        self.assertEqual((first['count'], first['count_is_candidate']), (5, True))
        self.assertEqual(self.classes(report)[('M-902',)]['count'], 3)
        self.assertNotIn(('M-903',), self.classes(report))

    def test_ordinal_is_a_floor_for_the_class_not_an_addition(self):
        report = self.run_clusters('## M-901 · 하나\n본문\n\n## M-902 · 둘\nM-901과 같은 구조\n\n'
                                   '## M-903 · 셋\nM-901과 같은 구조로 재발했고 3번째다.\n')
        item = self.classes(report)[('M-901', 'M-902', 'M-903')]
        self.assertEqual((item['count'], item['count_is_candidate']), (3, False))

    def test_ids_are_not_masked_and_an_unknown_reference_is_counted_not_guessed(self):
        report = self.run_clusters('## M-910 · 하나\n본문\n\n## M-911 · 둘\nM-910과 같은 구조. M-999와 같은 구조.\n')
        self.assertEqual(list(self.classes(report, 'explicit')), [('M-910', 'M-911')])
        self.assertEqual(report['diagnostics']['unresolved_references'], 1)


class CandidateTests(ClusterBase):
    BODY = ('## M-901 · 파이프 뒤 종료 코드를 놓쳤다\n명령 파이프 종료 코드 확인 실패로 다음 단계가 돌았다\n\n'
            '## M-902 · 파이프 뒤 종료 코드를 또 놓쳤다\n명령 파이프 종료 코드 확인 실패로 커밋이 돌았다\n\n'
            '## M-903 · 표 머리글 정렬 어긋남\n엑셀 열 폭과 글꼴 기준 차이\n\n'
            '## M-904 · 달력 시간대 표기 오류\n서버 표준시 저장과 화면 현지시 변환\n')

    def test_similar_remaining_entries_are_candidates_only(self):
        report = self.run_clusters(self.BODY, threshold=0.4)
        self.assertEqual(self.classes(report, 'explicit'), {})
        item = self.classes(report, 'candidate')[('M-901', 'M-902')]
        self.assertEqual(item['count'], 2)
        self.assertGreaterEqual(item['similarity'], 0.4)
        self.assertEqual(report['threshold'], {'value': 0.4, 'source': 'caller'})

    def test_the_default_threshold_says_it_is_not_calibrated(self):
        report = self.run_clusters(self.BODY)
        self.assertEqual(report['threshold']['source'], 'default_uncalibrated')

    def test_entries_in_an_explicit_class_are_not_candidates_again(self):
        report = self.run_clusters(self.BODY + '\n## M-905 · 파이프 뒤 종료 코드를 놓쳤다\nM-901 재발이다.\n', threshold=0.4)
        self.assertIn(('M-901', 'M-905'), self.classes(report, 'explicit'))
        self.assertEqual(self.classes(report, 'candidate'), {})

    def test_no_chaining_through_a_middle_entry(self):
        report = self.run_clusters('## M-901 · 가나다라마바사\n\n## M-902 · 다라마바사아자\n\n## M-903 · 마바사아자차카\n',
                                   threshold=0.45)
        self.assertEqual(list(self.classes(report, 'candidate')), [('M-901', 'M-902')])

    def test_related_references_do_not_add_similarity(self):
        body = ('## M-901 · 파이프\n- 관련: M-902, M-903, M-904\n\n'
                '## M-902 · 달력\n- 관련: M-901, M-903, M-904\n\n'
                '## M-903 · 표\n- 관련: M-901, M-902, M-904\n\n'
                '## M-904 · 글꼴\n- 관련: M-901, M-902, M-903\n')
        self.assertEqual(self.run_clusters(body, threshold=0.1)['classes'], [])

    def test_a_pair_exactly_at_the_threshold_is_a_candidate(self):
        tokens = {'M-1': {1, 2, 3, 4, 5}, 'M-2': {1, 2, 3, 4, 6}}
        score = 4 / 6
        with mock.patch.object(self.module, '_tokens', return_value=tokens):
            self.assertEqual(self.module._candidates([], score), [(['M-1', 'M-2'], score)])
            self.assertEqual(self.module._candidates([], math.nextafter(score, 1)), [])

    def test_a_group_may_hold_a_pair_exactly_at_the_threshold(self):
        tokens = {'M-1': {1, 2, 3, 4, 5, 6, 7}, 'M-2': {1, 2, 3, 4, 5, 6, 8}, 'M-3': {1, 2, 3, 4, 5, 9, 10}}
        score = self.module._jaccard(tokens['M-1'], tokens['M-3'])
        self.assertLess(score, self.module._jaccard(tokens['M-1'], tokens['M-2']))
        self.assertEqual(self.module._jaccard(tokens['M-2'], tokens['M-3']), score)
        with mock.patch.object(self.module, '_tokens', return_value=tokens):
            self.assertEqual(self.module._candidates([], score), [(['M-1', 'M-2', 'M-3'], score)])
            self.assertEqual(self.module._candidates([], math.nextafter(score, 1)),
                             [(['M-1', 'M-2'], self.module._jaccard(tokens['M-1'], tokens['M-2']))])

    def filler(self, count, common):
        """Entries with their own words; the first ones in common also carry the common word."""
        words = '가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후'
        return [SimpleNamespace(id=f'M-{number}', text=[words[number * 5:number * 5 + 5]
                                                        + (' 공통낱말표현' if number < common else '')])
                for number in range(count)]

    def test_a_word_in_more_than_half_of_eight_entries_is_dropped(self):
        tokens = self.module._tokens(self.filler(8, 5))
        self.assertFalse(tokens['M-0'] & tokens['M-1'])

    def test_a_word_in_exactly_half_of_the_entries_stays(self):
        tokens = self.module._tokens(self.filler(8, 4))
        self.assertTrue(tokens['M-0'] & tokens['M-1'])

    def test_fewer_entries_than_the_filter_needs_keep_every_word(self):
        tokens = self.module._tokens(self.filler(self.module.DF_FILTER_MIN_ENTRIES - 1, 7))
        self.assertTrue(tokens['M-0'] & tokens['M-1'])

    def test_entries_alike_only_in_a_common_word_are_not_a_candidate(self):
        entries = self.filler(8, 8)
        self.assertEqual(self.module._candidates(entries, 0.1), [])
        with mock.patch.object(self.module, 'DF_FILTER_MIN_ENTRIES', 100):
            self.assertTrue(self.module._candidates(entries, 0.1))

    def test_bad_threshold_is_a_fixed_code(self):
        for value in (0, 1.5, 'x', True):
            with self.subTest(value=value), self.assertRaises(self.module.LessonClusterError) as caught:
                self.run_clusters(self.BODY, threshold=value)
            self.assertEqual(str(caught.exception), 'invalid_threshold')


class LedgerTests(ClusterBase):
    BODY = ('## M-910 · 하나 (2026-09-01)\n본문\n\n## M-911 · 둘 (2026-09-02)\nM-910과 같은 구조\n\n'
            '## M-912 · 셋 (2026-09-03)\nM-910과 같은 구조\n\n'
            '## M-930 · 넷\n본문\n\n## M-931 · 다섯\nM-930과 같은 구조\n\n## M-932 · 여섯\nM-930과 같은 구조\n\n'
            '## M-940 · 일곱\n본문\n\n## M-941 · 여덟\nM-940과 같은 구조\n')

    def test_three_or_more_without_a_ledger_row_is_unledgered(self):
        report = self.run_clusters(self.BODY)
        linked = self.classes(report)[('M-910', 'M-911', 'M-912')]
        self.assertEqual((linked['count'], linked['ledger_linked'], linked['unledgered']), (3, True, False))
        self.assertEqual(linked['ledger_lines'], [5])
        missing = self.classes(report)[('M-930', 'M-931', 'M-932')]
        self.assertEqual((missing['ledger_linked'], missing['unledgered']), (False, True))
        below = self.classes(report)[('M-940', 'M-941')]
        self.assertEqual((below['ledger_linked'], below['unledgered']), (False, False))

    def test_a_row_the_ledger_parser_drops_still_links_and_is_reported(self):
        report = self.run_clusters('## M-920 · 하나\n본문\n\n## M-921 · 둘\nM-920과 같은 구조\n')
        self.assertTrue(self.classes(report)[('M-920', 'M-921')]['ledger_linked'])
        self.assertEqual(report['ledger']['ignored_rows'], 1)

    def test_missing_or_unreadable_ledger_makes_the_link_unknown(self):
        (self.vault / '00-meta/lessons.md').unlink()
        report = self.run_clusters(self.BODY)
        self.assertEqual(report['ledger']['state'], 'missing')
        item = self.classes(report)[('M-930', 'M-931', 'M-932')]
        self.assertEqual((item['ledger_linked'], item['unledgered']), (None, False))
        self.write('00-meta/lessons.md', '## 대장\n- 2026-10-01 | 횟수 0 | x | 근거: y | 상태: z\n')
        report = self.run_clusters(self.BODY)
        self.assertEqual(report['ledger']['state'], 'invalid')
        self.assertEqual(report['ledger']['error'], 'invalid_lesson_counter')


class BoundaryTests(ClusterBase):
    def test_report_is_deterministic_and_touches_nothing(self):
        body = LedgerTests.BODY
        first = self.run_clusters(body)
        before = sorted((p.relative_to(self.vault).as_posix(), p.read_bytes())
                        for p in self.vault.rglob('*') if p.is_file())
        second = self.module.cluster_lessons(self.vault, 'notes/mistakes.md')
        after = sorted((p.relative_to(self.vault).as_posix(), p.read_bytes())
                       for p in self.vault.rglob('*') if p.is_file())
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first['authority'], 'observation_data_only')
        raw = (self.vault / 'notes/mistakes.md').read_bytes()
        self.assertEqual(first['source']['sha256'], hashlib.sha256(raw).hexdigest())

    def test_deny_zone_and_excluded_notes_are_refused_without_their_path(self):
        self.write('private/mistakes.md', '## M-901 · x\n')
        self.write('excluded/mistakes.md', '## M-901 · x\n')
        for name in ('private/mistakes.md', 'excluded/mistakes.md'):
            with self.subTest(name=name), self.assertRaises(self.module.LessonClusterError) as caught:
                self.module.cluster_lessons(self.vault, name)
            self.assertNotIn('mistakes', str(caught.exception))

    def test_cli_prints_json_and_fixed_error_codes(self):
        self.run_clusters(LedgerTests.BODY)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main(['--vault', str(self.vault), '--mistakes', 'notes/mistakes.md'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['entries'], 8)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main(['--vault', str(self.vault), '--mistakes', 'notes/absent.md'])
        self.assertEqual(code, 2)
        result = json.loads(out.getvalue())
        self.assertEqual(result['ok'], False)
        self.assertNotIn('absent', result['error'])


if __name__ == '__main__':
    unittest.main()
