"""Opt-in Korean period expressions as half-open date intervals (vault_period)."""
from __future__ import annotations

from datetime import date, timedelta
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
            '2026년 3분기 중': q3,
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

    def test_period_end_takes_a_listed_particle_after_every_period_form(self):
        q3_end = ('period_end', '2026-09-30', D(2026, 9, 30), D(2026, 10, 1), None)
        self.assertTable({text: q3_end for text in (
            '2026년 3분기 말에', '2026년 3분기말에', '2026년 3분기 말까지', '2026년 3분기 말부터', '2026년 3분기 말일',
            '2026년 3분기 말경', '2026년 3분기 말 기준', '2026년 3분기 말기준', '2026년 3분기 말 현재', '2026년 3분기 말의',
            '2026년 3분기 말로', '2026년 3분기 말쯤', '2026년 3분기 말께', '2026년 3분기 말은', '2026년 3분기 말는',
            '2026년 3분기 말이', '2026년 3분기 말가', '2026년 3분기 말도', '2026년 3분기 말엔', '2026년 3분기 말.',
            '2026년 3분기  말에', '2026년 3Q 말', '3Q26말', '3Q26 말에', '2026Q3 말', '2026-Q3말까지')})
        self.assertTable({'2026년 상반기 말에': ('period_end', '2026-06-30', D(2026, 6, 30), D(2026, 7, 1), None)})
        fiscal_end = ('period_end', '2027-03-31', D(2027, 3, 31), D(2027, 4, 1), None)
        self.assertTable({'FY2026말': fiscal_end, 'FY2026 말에': fiscal_end, '2026 회계연도 말': fiscal_end,
                          '2026 사업연도말까지': fiscal_end, 'FY2026 하반기 말': fiscal_end,
                          'FY2026 Q3 말': ('period_end', '2026-12-31', D(2026, 12, 31), D(2027, 1, 1), None)},
                         fiscal_start=4)
        self.assertEqual(self.records('FY2026 말'), [('fiscal_year', None, None, None, 'fiscal_start_undeclared')])

    def test_qualifiers_the_table_does_not_compute_abstain_instead_of_the_whole_period(self):
        qualified = {
            '2026년 3분기 말레이시아': 'quarter', '2026년 3분기말레이시아': 'quarter', '2026년 3분기 말씀': 'quarter',
            '2026년 3분기 말2': 'quarter', '2026년 3분기 초': 'quarter', '2026년 3분기초': 'quarter',
            '2026년 3분기 초에': 'quarter', '2026년 3분기  초': 'quarter', '2026년 상반기 초': 'half',
            '3Q26 초': 'quarter', '2026Q3 초': 'quarter', '2026년 3Q 초': 'quarter',
            '2026년 4분기 연속 흑자': 'quarter', '2026년 4분기연속': 'quarter', '2026년 3분기째': 'quarter',
            '2026년 3분기 만에': 'quarter', '2026년 3분기만에': 'quarter', '4분기 연속': 'quarter', '3분기째': 'quarter',
        }
        for fiscal_start in (None, 4):
            self.assertTable({text: (kind, None, None, None, 'period_qualifier') for text, kind in qualified.items()},
                             fiscal_start=fiscal_start)
        self.assertTable({'FY2026 초': ('fiscal_year', None, None, None, 'period_qualifier'),
                          'FY2026 말레이시아': ('fiscal_year', None, None, None, 'period_qualifier'),
                          '2026 회계연도 하반기 초': ('fiscal_half', None, None, None, 'period_qualifier'),
                          'FY2026 3분기 연속': ('fiscal_quarter', None, None, None, 'period_qualifier')}, fiscal_start=4)

    NARROWING = ('후반', '중후반', '중반', '초반', '초순', '전반', '끝', '마지막', '마지막 날', '마지막 달', '마지막 주',
                 '막바지', '막판', '상순', '중순', '하순', '첫 달', '첫날', '첫 주', '첫째 주', '첫 번째 달', '첫 2주',
                 '첫 두 달', '첫 3일', '첫 영업일', '첫 월요일', '셋째 주', '두 번째 달', '종료일', '마감일', '결산일', '개시일',
                 '시작일', '종료 시점', '마감 시한', '결산월', '시작', '8월', '12월', '2주차')

    def test_narrowing_words_after_every_period_form_abstain(self):
        """Attached, spaced, after the period's 의 and with a particle: never the whole period, never a computed day."""
        forms = (('2026년 3분기', 'quarter'), ('2026년 하반기', 'half'), ('3Q26', 'quarter'), ('2026Q3', 'quarter'),
                 ('2026 Q3', 'quarter'), ('2026년 3Q', 'quarter'), ('FY2026', 'fiscal_year'),
                 ('2026 회계연도', 'fiscal_year'), ('2026 사업연도', 'fiscal_year'), ('FY2026 Q3', 'fiscal_quarter'),
                 ('FY2026 3분기', 'fiscal_quarter'), ('2026 회계연도 하반기', 'fiscal_half'))
        for form, kind in forms:
            for word in self.NARROWING:
                # A digit written onto a form that ends in a letter or digit (3Q268월) is another number.
                attached = () if word[0].isdigit() and form[-1].isascii() else (form + word,)
                for text in (*attached, form + ' ' + word, form + ' ' + word + '에', form + '  ' + word + '까지',
                             form + '의 ' + word + '부터'):
                    for fiscal_start in (None, 4):
                        with self.subTest(text=text, fiscal_start=fiscal_start):
                            self.assertEqual(self.records(text, fiscal_start),
                                             [(kind, None, None, None, 'period_qualifier')])
        # After the period's 의, 말 and 초 abstain as well: 의 is never read as the last day.
        for text in ('2026년 3분기의 말', '2026년 3분기의 말에', '2026년 3분기의 말일', '2026년 3분기의 초'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text), [('quarter', None, None, None, 'period_qualifier')])

    def test_words_that_do_not_narrow_a_period_keep_it_whole(self):
        """중, 내, 동안, modifiers and neutral uses of listed stems stay the whole period; 말 stays the last day."""
        q3 = ('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)
        for word in ('중', '중에', '내', '이내', '동안', '전체', '이후', '이전', '부터', '까지', '실적', '전반적으로',
                     '첫 흑자', '첫 출하', '첫 주문', '첫 세일', '첫째, 매출', '시작하는', '시작된', '시작품', '결산 실적',
                     '종료 예정', '개시 예정', '마감 실적', '3개월'):
            for text in ('2026년 3분기 ' + word, '2026년 3분기' + word, '2026년 3분기의 ' + word):
                with self.subTest(text=text):
                    self.assertEqual(self.records(text), [q3])
        self.assertTable({'2026년 3분기 실적': q3, '2026년 3분기 중': q3, '2026년 3분기(7~9월)': q3,
                          '2026년 3분기 말에': ('period_end', '2026-09-30', D(2026, 9, 30), D(2026, 10, 1), None),
                          '2026년 하반기 말까지': ('period_end', '2026-12-31', D(2026, 12, 31), D(2027, 1, 1), None)})
        self.assertTable({'FY2026 Q3': ('fiscal_quarter', '2026-10/2026-12', D(2026, 10, 1), D(2027, 1, 1), None),
                          'FY2026 Q3 실적': ('fiscal_quarter', '2026-10/2026-12', D(2026, 10, 1), D(2027, 1, 1), None)},
                         fiscal_start=4)

    def test_a_body_period_with_a_narrowing_word_is_not_indexed(self):
        for body in ('신제품은 2026년 하반기 후반 양산 목표.', '3공장은 2026년 3분기 마지막 날 완공.', '2026년 3분기의 첫 달 출시',
                     '2026년 3분기 종료일 정산', '3Q26 끝까지 점검', '2026년 3분기 시작과 함께 가동', '2026Q3 막바지'):
            with self.subTest(body=body):
                self.assertEqual(period.index(period.parse(body)), ())
        self.assertEqual(period.index(period.parse('2026년 3분기 중 완공')), ((D(2026, 7, 1), D(2026, 10, 1)),))

    def test_q_or_h_marks_around_a_fiscal_year_abstain_except_fy_then_q(self):
        marks = {'Q3 FY2026': 'fiscal_quarter', 'Q3 FY26': 'fiscal_quarter', 'q3 fy2026': 'fiscal_quarter',
                 '3Q FY2026': 'fiscal_quarter', 'H1 FY2026': 'fiscal_half', '1H FY2026': 'fiscal_half',
                 'H1 2026 회계연도': 'fiscal_half', 'FY2026 H1': 'fiscal_half', 'FY2026 1H': 'fiscal_half',
                 'FY26 2H': 'fiscal_half', 'FY2026 3Q': 'fiscal_quarter'}
        for fiscal_start in (None, 1, 4):
            self.assertTable({text: (kind, None, None, None, 'period_qualifier') for text, kind in marks.items()},
                             fiscal_start=fiscal_start)
        fiscal_q3 = ('fiscal_quarter', '2026-10/2026-12', D(2026, 10, 1), D(2027, 1, 1), None)
        self.assertTable({'FY2026 Q3': fiscal_q3, 'FY2026Q3': fiscal_q3}, fiscal_start=4)
        self.assertTable({'FY2026 Q3': ('fiscal_quarter', '2026-07/2026-09', D(2026, 7, 1), D(2026, 10, 1), None)},
                         fiscal_start=1)

    def test_a_range_end_no_row_reads_adds_a_partial_range_record(self):
        partial = ('range', None, None, None, 'partial_range')
        q3 = ('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)

        def day(month, number):
            start = D(2026, month, number)
            return ('day', start.isoformat(), start, start + timedelta(days=1), None)
        cases = {
            '2026.10.07~10.09': [day(10, 7), partial], '2026.01.01~03.31': [day(1, 1), partial],
            '2026. 1. 1.~3. 31.': [day(1, 1), partial], '2026년 1월 1일~3월 31일': [day(1, 1), partial],
            '2026년 1월 1일부터 3월 31일까지': [day(1, 1), partial], '2026-10-07~09': [day(10, 7), partial],
            '2026.10.07~09': [day(10, 7), partial], '2026.10.07 ~ 10.09': [day(10, 7), partial],
            '2026.10.07(수)~10.09(금)': [day(10, 7), partial], '2026-10-07—09': [day(10, 7), partial],
            '2026.10.07–10.09': [day(10, 7), partial], '2026.10.07〜10.09': [day(10, 7), partial],
            '2026.10.07∼10.09': [day(10, 7), partial], '10.07~2026.10.09': [partial, day(10, 9)],
            '10.07(수)~2026.10.09(금)': [partial, day(10, 9)], '10월 7일부터 2026년 10월 9일까지': [partial, day(10, 9)],
            '2026년 3분기~2027.03': [q3, partial], '2026Q3~Q4': [q3, partial],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.records(text), expected)
        # One fiscal year written as a span has the same shape and abstains too.
        self.assertEqual(self.records('FY2026-27', 4),
                         [('fiscal_year', '2026-04/2027-03', D(2026, 4, 1), D(2027, 4, 1), None), partial])
        # Both ends written: two periods and no partial record.
        for text in ('2026.10.07~2026.10.09', '2026.10.07(수) ~ 2026.10.09(금)', '2026-10-07–2026-10-09',
                     '2026년 10월 7일부터 2026년 10월 9일까지'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text), [day(10, 7), day(10, 9)])
        text = '2026.10.07~10.09 출장'
        self.assertEqual([text[slice(*record['span'])] for record in period.parse(text)], ['2026.10.07', '~10'])

    def test_short_years_need_a_non_zero_first_digit_and_bare_years_are_1900_to_2099(self):
        for text in ('FY0026', 'FY0000', '3Q0026'):
            with self.subTest(text=text):
                self.assertEqual(period.parse(text, fiscal_start=4), [])
        no_year = ('quarter', None, None, None, 'no_year')
        self.assertTable({'매출 1500 3분기 대비': no_year, '1234 2분기': no_year, '2100 3분기': no_year,
                          '1899 3분기': no_year,
                          '1999 3분기': ('quarter', '1999-35', D(1999, 7, 1), D(1999, 10, 1), None),
                          '2099 하반기': ('half', '2099-41', D(2099, 7, 1), D(2100, 1, 1), None),
                          '1500년 3분기': ('quarter', '1500-35', D(1500, 7, 1), D(1500, 10, 1), None)})

    def test_index_of_is_none_past_the_text_limit(self):
        self.assertEqual(period.index_of(('2026년 3분기', '2026.10.07')),
                         period.index(period.parse('2026년 3분기 2026.10.07')))
        self.assertIsNone(period.index_of(('2026년 3분기', 'x' * (period.MAX_TEXT_CHARS + 1))))
        self.assertEqual(period.index_of((), fiscal_start=4), ())

    def test_invalid_arguments_raise_a_content_free_error(self):
        for text in (None, b'2026-10-07', 'x' * (period.MAX_TEXT_CHARS + 1)):
            with self.subTest(text=type(text)), self.assertRaisesRegex(period.PeriodError, '^invalid_text$'):
                period.parse(text)
        for value in (0, 13, '04', True, 4.0):
            with self.subTest(fiscal_start=value), self.assertRaisesRegex(period.PeriodError, '^invalid_fiscal_start$'):
                period.parse('FY2026', fiscal_start=value)
        self.assertTrue(issubclass(period.PeriodError, ValueError))

    def test_docstring_declares_the_calendar_convention_and_its_source(self):
        for phrase in ('calendar year', 'Library of Congress EDTF', 'durations', 'report labels',
                       'recognised from fixed lists only', 'narrowing word', 'is not recognised', 'period_qualifier',
                       'partial_range'):
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

    def test_period_ends_qualifiers_and_partial_ranges_never_compare_as_a_wider_period(self):
        cases = (
            ('2026년 3분기 말에', '2026년 3분기 실적', {'normalized_match': 'none'}),
            ('2026년 3분기 말에 완공', '2026.09.30 준공', {'normalized_match': 'equal', 'via': ['note0']}),
            ('2026년 3분기 말까지', '2026.08.01 착수', {'normalized_match': 'none'}),
            ('3Q26말', '2026년 3분기 실적', {'normalized_match': 'none'}),
            ('2026Q3 말', '2026.08.01', {'normalized_match': 'none'}),
            ('2026년 3분기 초', '2026년 3분기 실적', {'normalized_match': 'abstain', 'reason': 'period_qualifier'}),
            ('2026.10.07~10.09 출장', '2026.10.07 출발', {'normalized_match': 'abstain', 'reason': 'partial_range'}),
            ('2026.01.01~03.31', '2026.01.01 시작', {'normalized_match': 'abstain', 'reason': 'partial_range'}),
            ('2026-10-07~09', '2026.10.07 출발', {'normalized_match': 'abstain', 'reason': 'partial_range'}),
            # A body keeps the end it writes; its partial record is ignored like other body abstentions.
            ('2026.10.07', '2026.10.07~10.09 출장', {'normalized_match': 'equal', 'via': ['note0']}),
            ('2026년 3분기', '2026년 3분기 말에 완공', {'normalized_match': 'contained', 'via': ['note0']}),
        )
        for value, body, expected in cases:
            with self.subTest(value=value, body=body):
                self.assertEqual(self.classify(value, body), expected)
        for value, body, expected in (('Q3 FY2026', 'FY2026 실적', 'abstain'), ('H1 FY2026', '2026.12.01', 'abstain'),
                                      ('FY2026말', '2026.05.01', 'none'), ('2026 회계연도 말', 'FY2026', 'none'),
                                      ('FY2026말', '2027.03.31 결산', 'equal')):
            for fiscal_start in (1, 4) if value[0] in 'QH' else (4,):
                with self.subTest(value=value, body=body, fiscal_start=fiscal_start):
                    self.assertEqual(self.classify(value, body, fiscal_start=fiscal_start)['normalized_match'], expected)

    def test_narrowing_words_never_compare_as_the_whole_period(self):
        qualifier = {'normalized_match': 'abstain', 'reason': 'period_qualifier'}
        cases = (
            ('2026년 3분기 마지막 날', '2026년 3분기 실적', qualifier),
            ('2026년 3분기 첫 달', '2026.08.15 출시', qualifier),
            ('2026년 3분기 마지막 날', '2026.09.30 결산', qualifier),
            ('2026년 하반기 후반 양산', '2026년 하반기 양산 목표', qualifier),
            ('2026년 하반기 후반', '2026.07.10 착공', qualifier),
            ('2026년 3분기 종료일 완공', '2026년 3분기 완공 예정', qualifier),
            ('2026년 3분기의 끝', '2026년 3분기 완공 예정', qualifier),
            # A body period with a narrowing word is not indexed; 중 keeps the whole period.
            ('2026년 하반기', '2026년 하반기 후반 양산', {'normalized_match': 'none'}),
            ('2026년 3분기', '2026년 3분기의 마지막 날 완공', {'normalized_match': 'none'}),
            ('2026년 3분기 중', '2026년 3분기 실적', {'normalized_match': 'equal', 'via': ['note0']}),
            ('2026년 3분기', '2026년 3분기 중 완공', {'normalized_match': 'equal', 'via': ['note0']}),
            ('2026년 3분기 첫 흑자', '2026년 3분기 흑자 전환', {'normalized_match': 'equal', 'via': ['note0']}),
            ('2026년 3분기 말에', '2026.09.30 결산', {'normalized_match': 'equal', 'via': ['note0']}),
        )
        for value, body, expected in cases:
            with self.subTest(value=value, body=body):
                self.assertEqual(self.classify(value, body), expected)
        self.assertEqual(self.classify('FY2026 후반', 'FY2026 실적', fiscal_start=4), qualifier)
        self.assertEqual(self.classify('FY2026 Q3', 'FY2026 Q3 실적', fiscal_start=4),
                         {'normalized_match': 'equal', 'via': ['note0']})

    def test_reaches_is_via_membership_for_one_index(self):
        bodies = {'note0': '2025년 4분기', 'note1': '2026.02.10', 'note2': '무관', 'note3': '2026년 1분기'}
        indexes = {key: period.index(period.parse(text)) for key, text in bodies.items()}
        results = []
        for value in ('2025년 4분기, 2026년 1분기', '2026년 1분기', '2026.02.10', '2026년 상반기'):
            records = period.parse(value)
            result = period.match(records, indexes)
            results.append(result['normalized_match'])
            with self.subTest(value=value):
                self.assertEqual(result['via'], [key for key in sorted(indexes)
                                                 if period.reaches(records, indexes[key], result['normalized_match'])])
        self.assertEqual(results, ['equal', 'equal', 'equal', 'contained'])


if __name__ == '__main__':
    unittest.main()
