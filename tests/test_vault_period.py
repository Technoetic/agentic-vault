"""Opt-in Korean period expressions as half-open date intervals (vault_period)."""
from __future__ import annotations

from datetime import date
import importlib
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    period = importlib.import_module('vault_period')
except ModuleNotFoundError:
    period = None

D = date


class PeriodParsingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(period, 'period normalizer is missing')

    @staticmethod
    def records(text, fiscal_start=None):
        return [(r['kind'], r['label_edtf'], r['start'], r['end'], r['reason'])
                for r in period.parse(text, fiscal_start=fiscal_start)]

    def assertTable(self, table, fiscal_start=None):
        for text, expected in table.items():
            with self.subTest(text=text, fiscal_start=fiscal_start):
                self.assertEqual(self.records(text, fiscal_start), [expected])

    def test_calendar_quarters_halves_and_days_are_half_open_intervals(self):
        q3 = ('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)
        day = ('day', '2026-10-07', D(2026, 10, 7), D(2026, 10, 8), None)
        self.assertTable({
            '2026년 1분기': ('quarter', '2026-33', D(2026, 1, 1), D(2026, 4, 1), None),
            '2026년 3분기 매출': q3, '2026 3/4분기': q3, '2026년도 제3분기': q3, '2026년 3Q': q3,
            '3Q26': q3, '3q26': q3, '3Q2026': q3, '2026Q3': q3, '2026-Q3': q3, '2026 Q3': q3,
            '2026년 4분기': ('quarter', '2026-36', D(2026, 10, 1), D(2027, 1, 1), None),
            '2026년 1/4분기': ('quarter', '2026-33', D(2026, 1, 1), D(2026, 4, 1), None),
            '2026년 상반기': ('half', '2026-40', D(2026, 1, 1), D(2026, 7, 1), None),
            '2026 하반기': ('half', '2026-41', D(2026, 7, 1), D(2027, 1, 1), None),
            '2026년 3분기말 기준': ('period_end', '2026-09-30', D(2026, 9, 30), D(2026, 10, 1), None),
            '2026년 4분기말': ('period_end', '2026-12-31', D(2026, 12, 31), D(2027, 1, 1), None),
            '2026년 상반기말': ('period_end', '2026-06-30', D(2026, 6, 30), D(2026, 7, 1), None),
            '2026 하반기 말 기준': ('period_end', '2026-12-31', D(2026, 12, 31), D(2027, 1, 1), None),
            '2026년 3분기 말레이시아': q3,
            '2026-10-07': day, '2026.10.07': day, '2026. 10. 7.': day, '2026년 10월 7일': day,
        })

    def test_fiscal_years_resolve_only_with_a_declared_first_month(self):
        for text in ('FY2026', 'FY26', "FY'26", 'fy2026', 'FY 2026', '2026 회계연도', '2026년 회계연도',
                     '2026 사업연도', '2026 회계년도'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text), [('fiscal_year', None, None, None, 'fiscal_start_undeclared')])
                self.assertEqual(self.records(text, 4),
                                 [('fiscal_year', '2026-04/2027-03', D(2026, 4, 1), D(2027, 4, 1), None)])
                self.assertEqual(self.records(text, 1),
                                 [('fiscal_year', '2026-01/2026-12', D(2026, 1, 1), D(2027, 1, 1), None)])

    def test_quarters_and_halves_after_a_fiscal_year_count_from_its_first_month(self):
        table = {
            'FY2026 1분기': ('fiscal_quarter', '2026-04/2026-06', D(2026, 4, 1), D(2026, 7, 1), None),
            '2026 회계연도 3분기': ('fiscal_quarter', '2026-10/2026-12', D(2026, 10, 1), D(2027, 1, 1), None),
            'FY26 Q4': ('fiscal_quarter', '2027-01/2027-03', D(2027, 1, 1), D(2027, 4, 1), None),
            '2026 회계연도 하반기': ('fiscal_half', '2026-10/2027-03', D(2026, 10, 1), D(2027, 4, 1), None),
            'FY2026 3분기말': ('period_end', '2026-12-31', D(2026, 12, 31), D(2027, 1, 1), None),
        }
        self.assertTable(table, fiscal_start=4)
        undeclared = {text: (expected[0] if expected[0] != 'period_end' else 'fiscal_quarter',
                             None, None, None, 'fiscal_start_undeclared') for text, expected in table.items()}
        self.assertTable(undeclared)
        self.assertEqual(self.records('FY2026 3분기', 1),
                         [('fiscal_quarter', '2026-07/2026-09', D(2026, 7, 1), D(2026, 10, 1), None)])

    def test_calendar_quarters_abstain_when_the_fiscal_year_does_not_start_in_january(self):
        for text, kind in (('2026년 3분기', 'quarter'), ('3Q26', 'quarter'), ('2026Q3', 'quarter'),
                           ('2026년 상반기', 'half'), ('2026년 3분기말', 'quarter')):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), [(kind, None, None, None, 'quarter_basis_ambiguous')])
                self.assertIsNone(self.records(text, 1)[0][4])
                self.assertEqual(self.records(text, 1), self.records(text))
        self.assertEqual(self.records('2026-10-07', 4), self.records('2026-10-07'))

    def test_yearless_relative_listed_and_impossible_periods_abstain(self):
        reasons = {
            '3분기': ('quarter', 'no_year'), '제3분기': ('quarter', 'no_year'), '1/4분기': ('quarter', 'no_year'),
            '3Q 실적': ('quarter', 'no_year'), '상반기': ('half', 'no_year'), '3분기말': ('quarter', 'no_year'),
            '당기': ('relative', 'relative_needs_anchor'), '전기 대비': ('relative', 'relative_needs_anchor'),
            '전년 동기': ('relative', 'relative_needs_anchor'), '전년동기': ('relative', 'relative_needs_anchor'),
            '올해': ('relative', 'relative_needs_anchor'), '이번 분기': ('relative', 'relative_needs_anchor'),
            '지난해': ('relative', 'relative_needs_anchor'), '작년': ('relative', 'relative_needs_anchor'),
            '전 분기': ('relative', 'relative_needs_anchor'), '당해 연도': ('relative', 'relative_needs_anchor'),
            '2026년 1~3분기 누적': ('multiple', 'multiple_periods'), '1·2분기': ('multiple', 'multiple_periods'),
            '1, 2분기': ('multiple', 'multiple_periods'), '2026년 상·하반기': ('multiple', 'multiple_periods'),
            '상하반기': ('multiple', 'multiple_periods'), '상/하반기': ('multiple', 'multiple_periods'),
            '2026.02.30': ('day', 'invalid_date'), '2026-13-01': ('day', 'invalid_date'),
            '2026년 2월 30일': ('day', 'invalid_date'),
        }
        for fiscal_start in (None, 4):
            self.assertTable({text: (kind, None, None, None, reason) for text, (kind, reason) in reasons.items()},
                             fiscal_start=fiscal_start)
        # The quarter after 9999 has no date; the basis check comes first.
        self.assertEqual(self.records('9999년 4분기'), [('quarter', None, None, None, 'invalid_date')])
        self.assertEqual(self.records('9999년 4분기', 4), [('quarter', None, None, None, 'quarter_basis_ambiguous')])
        self.assertEqual(self.records('FY9999', 4), [('fiscal_year', None, None, None, 'invalid_date')])

    def test_no_anchor_year_is_filled_in(self):
        self.assertEqual(self.records('2025년 1분기와 3분기'),
                         [('quarter', '2025-33', D(2025, 1, 1), D(2025, 4, 1), None),
                          ('quarter', None, None, None, 'no_year')])
        self.assertEqual(self.records('올해 3분기'), [('relative', None, None, None, 'relative_needs_anchor'),
                                                    ('quarter', None, None, None, 'no_year')])

    def test_minutes_durations_and_lookalikes_are_not_periods(self):
        for text in ('회의 3분 지연', '3분 기다렸다', '3분기다렸다', '3분기록 갱신', '12개월', '1년', '1년 6개월',
                     'P12M', 'P1Y', '24개월', '20263분기', '13분기', '5분기', '전기차', '전기요금', '충전기',
                     '발전기', '앞당기다', '당기순이익', '2026.10', '1.2026.10.07', 'v2026.10.07', '2026.10.07.1',
                     '3Q2', 'FY202', '2026년', '2026년 10월', 'Q3 2026', '1H26'):
            with self.subTest(text=text):
                self.assertEqual(period.parse(text), [])
        # Durations are never merged: 12개월 and 1년 name no period to compare.
        self.assertIsNone(period.match(period.parse('12개월'), {'note': period.index(period.parse('1년'))}))

    def test_records_keep_text_order_and_ranges_stay_two_periods(self):
        text = '2025년 4분기~2026년 1분기, 2026.01.01~2026.03.31'
        records = period.parse(text)
        self.assertEqual([text[slice(*record['span'])] for record in records],
                         ['2025년 4분기', '2026년 1분기', '2026.01.01', '2026.03.31'])
        self.assertEqual([record['label_edtf'] for record in records], ['2025-36', '2026-33', '2026-01-01', '2026-03-31'])
        self.assertEqual(set(records[0]), {'span', 'kind', 'label_edtf', 'start', 'end', 'reason'})

    def test_invalid_arguments_raise_a_content_free_error(self):
        for text in (None, b'2026-10-07', 'x' * (period.MAX_TEXT_CHARS + 1)):
            with self.subTest(text=type(text)), self.assertRaisesRegex(period.PeriodError, '^invalid_text$'):
                period.parse(text)
        for value in (0, 13, '04', True, 4.0):
            with self.subTest(fiscal_start=value), self.assertRaisesRegex(period.PeriodError, '^invalid_fiscal_start$'):
                period.parse('FY2026', fiscal_start=value)
        self.assertTrue(issubclass(period.PeriodError, ValueError))

    def test_docstring_declares_the_calendar_convention_and_its_source(self):
        for phrase in ('calendar year', 'Library of Congress EDTF', 'durations', 'report labels'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, ' '.join(period.__doc__.split()))


class PeriodMatchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(period, 'period normalizer is missing')

    @staticmethod
    def classify(value, *bodies, fiscal_start=None):
        indexes = {f'note{number}': period.index(period.parse(body, fiscal_start=fiscal_start))
                   for number, body in enumerate(bodies)}
        return period.match(period.parse(value, fiscal_start=fiscal_start), indexes)

    def test_equal_contained_and_none_use_half_open_bounds(self):
        cases = (
            ('2026년 3분기', '3Q26 실적', 'equal'),
            ('2026-10-07', '회의는 2026. 10. 7.에 열렸다', 'equal'),
            ('2026년 3분기말', '2026.09.30 결산', 'equal'),
            ('2026년 3분기', '2026.07.01 착수', 'contained'),
            ('2026년 3분기', '2026.09.30 종료', 'contained'),
            ('2026년 3분기', '2026.10.01 종료', 'none'),
            ('2026년 3분기', '2026.06.30 착수', 'none'),
            ('2026.08.15', '2026년 3분기 출시', 'none'),
            ('2026년 하반기', '2026년 4분기', 'contained'),
            ('2026년 4분기', '2026년 하반기', 'none'),
        )
        for value, body, expected in cases:
            with self.subTest(value=value, body=body):
                result = self.classify(value, body)
                self.assertEqual(result['normalized_match'], expected)
                self.assertEqual(result.get('via'), None if expected == 'none' else ['note0'])
        for value, body, expected in (('FY2026', '2027.03.31 마감', 'contained'), ('FY2026', '2027.04.01', 'none'),
                                      ('FY2026', '2026 회계연도', 'equal'), ('FY2026', '2026.04.01', 'contained')):
            with self.subTest(value=value, body=body, fiscal_start=4):
                self.assertEqual(self.classify(value, body, fiscal_start=4)['normalized_match'], expected)

    def test_the_weakest_interval_decides_and_via_names_the_matching_bodies(self):
        self.assertEqual(self.classify('2026년 1분기', '2026년 1분기', '2026.02.10'),
                         {'normalized_match': 'equal', 'via': ['note0']})
        self.assertEqual(self.classify('2025년 4분기, 2026년 1분기', '2025년 4분기', '2026.02.10', '무관'),
                         {'normalized_match': 'contained', 'via': ['note0', 'note1']})
        self.assertEqual(self.classify('2026.01.01~2026.03.31', '2026.01.01 시작', '2026년 1분기'),
                         {'normalized_match': 'none'})

    def test_value_abstentions_win_and_body_abstentions_are_ignored(self):
        self.assertEqual(self.classify('당기 2026년 3분기', '2026년 3분기'),
                         {'normalized_match': 'abstain', 'reason': 'relative_needs_anchor'})
        self.assertEqual(self.classify('2026년 3분기 FY2026', '2026년 3분기'),
                         {'normalized_match': 'abstain', 'reason': 'fiscal_start_undeclared'})
        self.assertEqual(self.classify('2026년 3분기', '올해 3분기', '당기'), {'normalized_match': 'none'})
        self.assertIsNone(self.classify('24개월', '2026년 3분기'))
        self.assertEqual(period.index(period.parse('3분기 FY2026 2026.02.30')), ())


if __name__ == '__main__':
    unittest.main()
