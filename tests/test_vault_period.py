"""Opt-in Korean period expressions as half-open date intervals (vault_period)."""
from __future__ import annotations

from datetime import date, timedelta
import importlib
from pathlib import Path
import sys
import time
import unicodedata
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
            '2026년 3분기 말로', '2026년 3분기 말쯤', '2026년 3분기 말께', '2026년 3분기 말엔', '2026년 3분기 말.',
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
            # 말 before 은, 는, 이, 가 or 도 may be the noun 말, and 초안 may be 초 안에: never a computed day or the whole period.
            '2026년 3분기 말은': 'quarter', '2026년 3분기 말는': 'quarter', '2026년 3분기 말이': 'quarter',
            '2026년 3분기 말가': 'quarter', '2026년 3분기 말도': 'quarter', '2026년 3분기 말도 안 되는': 'quarter',
            '2026년 3분기말이 많다': 'quarter', '2026년 상반기 말은': 'half', '3Q26 말도': 'quarter',
            '2026년 3분기 초안': 'quarter', '2026년 3분기초안': 'quarter',
            # 차이, 차인 and 차였다 may be week 2 with a copula.
            '2026년 3분기 2주 차이': 'quarter', '2026년 3분기 2주 차인': 'quarter', '2026년 3분기 2주 차였다': 'quarter',
            '2026년 3분기 2주 차이다': 'quarter',
        }
        for fiscal_start in (None, 4):
            self.assertTable({text: (kind, None, None, None, 'period_qualifier') for text, kind in qualified.items()},
                             fiscal_start=fiscal_start)
        self.assertTable({'FY2026 초': ('fiscal_year', None, None, None, 'period_qualifier'),
                          'FY2026 말레이시아': ('fiscal_year', None, None, None, 'period_qualifier'),
                          'FY2026 말도': ('fiscal_year', None, None, None, 'period_qualifier'),
                          '2026 회계연도 하반기 초': ('fiscal_half', None, None, None, 'period_qualifier'),
                          'FY2026 3분기 연속': ('fiscal_quarter', None, None, None, 'period_qualifier')}, fiscal_start=4)

    NARROWING = ('후반', '중후반', '중반', '초반', '초순', '전반', '끝', '마지막', '마지막 날', '마지막 달', '마지막 주',
                 '막바지', '막판', '상순', '중순', '하순', '첫 달', '첫날', '첫 주', '첫째 주', '첫 번째 달', '첫 2주',
                 '첫 두 달', '첫 3일', '첫 영업일', '첫 월요일', '셋째 주', '두 번째 달', '종료일', '마감일', '결산일', '개시일',
                 '시작일', '종료 시점', '마감 시한', '결산월', '시작', '8월', '12월', '2주차', '2주 차', '3 주 차', '12주 차')

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
                     '종료 예정', '개시 예정', '마감 실적', '3개월',
                     # 초과 is not 초 (start of period), and 2주 차질, 차량 or 차례 is not week 2.
                     '초과', '초과 달성', '초과분', '2주 차질', '2주 차량', '2주 차례', '1개월 차질', '중 첫 흑자'):
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
                     '2026년 3분기 종료일 정산', '3Q26 끝까지 점검', '2026년 3분기 시작과 함께 가동', '2026Q3 막바지',
                     # After 중, 내 or 동안, a list of months, N주째 and N개월 차, and an English prefix.
                     '2026년 3분기 중 마지막 달 완공', '2026년 하반기 동안 마지막 주 점검', '2026년 3분기 내 8월 출시',
                     '2026년 3분기 7·8월 집중', '2026년 3분기 7~8월 성수기', '2026년 3분기 7, 8월', '2026년 3분기 3주째 지연',
                     '2026년 3분기 1개월 차 점검', '2026Q3 1개월차', 'late 2026Q3 launch', 'Early 2026년 하반기',
                     'mid-2026Q3', 'by the end of 3Q26', 'start of 2026 Q3', 'end-of-2026Q3'):
            with self.subTest(body=body):
                self.assertEqual(period.index(period.parse(body)), ())
        self.assertEqual(period.index(period.parse('2026년 3분기 중 완공')), ((D(2026, 7, 1), D(2026, 10, 1)),))
        # A day is not narrowed by a prefix (a time inside a day), and a longer word is not a prefix.
        for body, interval in (('late 2026.10.07', (D(2026, 10, 7), D(2026, 10, 8))),
                               ('chocolate 2026Q3', (D(2026, 7, 1), D(2026, 10, 1))),
                               ('midterm 2026Q3', (D(2026, 7, 1), D(2026, 10, 1)))):
            with self.subTest(body=body):
                self.assertEqual(period.index(period.parse(body)), (interval,))
        for text in ('late FY2026', 'the end of 2026 회계연도', 'mid FY2026 Q3'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4)[0][4], 'period_qualifier')

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

    def test_marks_joined_to_a_fiscal_year_and_tokens_it_cannot_read_never_give_the_whole_year(self):
        """Only FY2026 Q3, FY2026 3분기 and FY2026 하반기 with one space or tab between are read."""
        marks = {
            'FY2026/Q3': 'fiscal_quarter', 'FY2026, Q3': 'fiscal_quarter', 'FY2026.Q3': 'fiscal_quarter',
            'FY2026_Q3': 'fiscal_quarter', 'FY2026의 Q3': 'fiscal_quarter', 'FY2026 의 Q3': 'fiscal_quarter',
            'FY2026 Q 3': 'fiscal_quarter', 'FY2026: Q3': 'fiscal_quarter', 'FY2026(Q3)': 'fiscal_quarter',
            'FY2026-Q3': 'fiscal_quarter', 'FY2026·Q3': 'fiscal_quarter', 'FY2026/H1': 'fiscal_half',
            'FY26-H1': 'fiscal_half', 'FY2026의 3분기': 'fiscal_quarter', 'FY2026, 하반기': 'fiscal_half',
            '2026 회계연도/Q3': 'fiscal_quarter', 'Q3/FY2026': 'fiscal_quarter', 'Q3, FY2026': 'fiscal_quarter',
            'Q3.FY2026': 'fiscal_quarter', 'Q3_FY2026': 'fiscal_quarter', 'Q3의 FY2026': 'fiscal_quarter',
            'Q 3 FY2026': 'fiscal_quarter', 'Q3-FY2026': 'fiscal_quarter', 'Q3 (FY2026)': 'fiscal_quarter',
            '1H/FY26': 'fiscal_half', '3분기 FY2026': 'fiscal_quarter', '하반기/FY2026': 'fiscal_half',
            # Any run of punctuation, symbols, whitespace or format characters, with at most one 의, 및, 과 or 와.
            'FY2026 & Q3': 'fiscal_quarter', 'FY2026 [Q3]': 'fiscal_quarter', 'FY2026+Q3': 'fiscal_quarter',
            'FY2026 | H1': 'fiscal_half', 'FY2026; 3분기': 'fiscal_quarter', 'FY2026 > Q3': 'fiscal_quarter',
            'FY2026\N{EM DASH}Q3': 'fiscal_quarter', 'FY2026~Q3': 'fiscal_quarter', 'FY2026 #Q3': 'fiscal_quarter',
            'FY2026 및 Q3': 'fiscal_quarter', 'FY2026과 3분기': 'fiscal_quarter', 'FY2026, (Q3)': 'fiscal_quarter',
            'Q3 & FY2026': 'fiscal_quarter', 'Q3 및 FY2026': 'fiscal_quarter', '하반기와 FY2026': 'fiscal_half',
            'Q3 [FY2026]': 'fiscal_quarter', '1H + FY26': 'fiscal_half',
            # Quarter or half tokens the fiscal row cannot read.
            'FY2026 Q5': 'fiscal_quarter', 'FY2026 Q0': 'fiscal_quarter', 'FY2026 5분기': 'fiscal_quarter',
            'FY2026 Q3Q4': 'fiscal_quarter', 'FY2026 Q12': 'fiscal_quarter', 'FY2026Q5': 'fiscal_quarter',
            'Q5 FY2026': 'fiscal_quarter',
        }
        for fiscal_start in (None, 1, 4):
            self.assertTable({text: (kind, None, None, None, 'period_qualifier') for text, kind in marks.items()},
                             fiscal_start=fiscal_start)
        # Direct calls read a mark across any whitespace run or format character, as other abstaining checks do.
        for gap in ('  ', '\n\n', chr(0xA0), chr(0x3000), chr(0x200B), ' ' + chr(0x2060) + ' '):
            for text, kind in (('Q3' + gap + 'FY2026', 'fiscal_quarter'), ('FY2026' + gap + 'H1', 'fiscal_half'),
                               ('H1' + gap + '2026 회계연도', 'fiscal_half'), ('FY2026' + gap + 'Q3', 'fiscal_quarter')):
                with self.subTest(text=text):
                    self.assertEqual(self.records(text, 4), [(kind, None, None, None, 'period_qualifier')])
        fiscal_q3 = ('fiscal_quarter', '2026-10/2026-12', D(2026, 10, 1), D(2027, 1, 1), None)
        self.assertTable({'FY2026\tQ3': fiscal_q3, 'FY2026 3분기': fiscal_q3, '2026 회계연도 Q3': fiscal_q3,
                          'FY2026 하반기': ('fiscal_half', '2026-10/2027-03', D(2026, 10, 1), D(2027, 4, 1), None)},
                         fiscal_start=4)
        # A token with its own year is another period, not a mark.
        fiscal_year = ('fiscal_year', '2026-04/2027-03', D(2026, 4, 1), D(2027, 4, 1), None)
        for text in ('FY2026 3Q26', 'FY2026, 2026Q3'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), [fiscal_year, ('quarter', None, None, None, 'quarter_basis_ambiguous')])
        # A list after the read quarter abstains through its unread item; the fiscal quarter itself is kept.
        multiple = ('multiple', None, None, None, 'multiple_periods')
        for text in ('FY2026 Q3·Q4', 'FY2026 Q3/Q4', 'FY2026 Q3, Q4', 'FY2026 Q3 및 Q4'):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), [fiscal_q3, multiple])

    @staticmethod
    def fiscal_year(year, fiscal_start):
        if fiscal_start is None:
            return ('fiscal_year', None, None, None, 'fiscal_start_undeclared')
        start = D(year, fiscal_start, 1)
        last = D(year + (fiscal_start > 1), (fiscal_start - 2) % 12 + 1, 1)
        return ('fiscal_year', f'{start:%Y-%m}/{last:%Y-%m}', start, D(year + 1, fiscal_start, 1), None)

    def test_a_mark_between_two_fiscal_years_goes_with_the_later_one(self):
        """FY2025, Q1 FY2026: the earlier year is read as written and Q1 FY2026 abstains, never the whole FY2026."""
        quarter = ('fiscal_quarter', None, None, None, 'period_qualifier')
        half = ('fiscal_half', None, None, None, 'period_qualifier')
        cases = (
            ('FY2025, Q1 FY2026', ['FY2025', 'Q1 FY2026'], quarter),
            ('FY2025·1H FY2026', ['FY2025', '1H FY2026'], half),
            ('FY2025: Q4 FY2026', ['FY2025', 'Q4 FY2026'], quarter),
            ('FY2025/Q4 FY2026', ['FY2025', 'Q4 FY2026'], quarter),
            ('2025 회계연도, Q1 2026 회계연도', ['2025 회계연도', 'Q1 2026 회계연도'], quarter),
            # A Markdown list that check-support collapses to one line, as written and as its literal form.
            ('FY2025 - Q1 FY2026', ['FY2025', 'Q1 FY2026'], quarter),
            ('- fy2025 - q1 fy2026', ['fy2025', 'q1 fy2026'], quarter),
            ('FY2024, FY2025, Q1 FY2026', ['FY2024', 'FY2025', 'Q1 FY2026'], quarter),
            ('FY2025(Q1 FY2026)', ['FY2025', 'Q1 FY2026'], quarter),
            ('FY2025. Q1 FY2026', ['FY2025', 'Q1 FY2026'], quarter),
            ('FY2025의 H2 FY2026', ['FY2025', 'H2 FY2026'], half),
            ('FY2025, 3분기 FY2026', ['FY2025', '3분기 FY2026'], quarter),
            ('FY2025, 하반기 FY2026', ['FY2025', '하반기 FY2026'], half),
            # A wider joiner after the earlier year: the hand-off still reads the mark with the later one.
            ('FY2025 & Q1 FY2026', ['FY2025', 'Q1 FY2026'], quarter),
            ('FY2025 [H2 FY2026', ['FY2025', 'H2 FY2026'], half),
            # The mark before the later year may start inside the mark read after the earlier one.
            ('FY2025 Q 1Q FY2026', ['FY2025', '1Q FY2026'], quarter),
            ('FY2025 H' + chr(0x200B) + '4Q FY2026', ['FY2025', '4Q FY2026'], quarter),
        )
        for fiscal_start in (None, 1, 4, 12):
            for text, spans, mark in cases:
                with self.subTest(text=text, fiscal_start=fiscal_start):
                    records = period.parse(text, fiscal_start=fiscal_start)
                    self.assertEqual([text[slice(*record['span'])] for record in records], spans)
                    years = [self.fiscal_year(int(span[-4:] if span[-1].isdigit() else span[:4]), fiscal_start)
                             for span in spans[:-1]]
                    self.assertEqual(self.records(text, fiscal_start), [*years, mark])
                    # The body index holds the earlier years only.
                    self.assertEqual(period.index(records), tuple((year[2], year[3]) for year in years if year[2]))
        # A mark the earlier year reads, H1-H4, 1Q-4Q or 1H-4H right after it or after one space or tab, and a mark
        # on its digits that no mark before the later year starts in stay the earlier year's. With only whitespace
        # before the later year the mark may be its own, so the later year abstains instead of being read whole.
        fiscal_q4 = ('fiscal_quarter', '2026-01/2026-03', D(2026, 1, 1), D(2026, 4, 1), None)
        unread = ('fiscal_year', None, None, None, 'period_qualifier')
        for text, first in (('FY2025 Q4 FY2026', fiscal_q4), ('FY2025 H1 FY2026', half), ('FY2025\t1Q FY2026', quarter),
                            ('FY2025 2H FY2026', half), ('FY2025H1 FY2026', half), ('2025 회계연도 h2 FY2026', half),
                            ('FY2025Q5 FY2026', quarter), ('FY2025 Q4  FY2026', fiscal_q4),
                            ('FY2025 Q4' + chr(0x200B) + 'FY2026', fiscal_q4)):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), [first, unread])
                for fiscal_start in (1, 12):
                    self.assertEqual(self.records(text, fiscal_start)[1:], [unread])
        # Likewise when the hand-off reads the mark with the later year across whitespace only: the earlier year
        # abstains too.
        for text, mark in (('FY2025  H1 FY2026', half), ('FY2025' + chr(0x200B) + 'Q1 FY2026', quarter),
                           ('FY2025\n하반기 FY2026', half)):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), [unread, mark])
        # A list of quarters or halves joined to a fiscal year as a mark would be is not one the year reads.
        multiple = ('multiple', None, None, None, 'multiple_periods')
        for text, expected in (('FY2025 1·2분기 FY2026', [unread, multiple, unread]), ('FY2026 1·2분기', [unread, multiple]),
                               ('1~2분기 FY2026', [multiple, unread]), ('FY2026, 상·하반기', [unread, multiple]),
                               ('FY2026 및 1, 2분기', [unread, multiple]),
                               # A word between keeps the year whole.
                               ('FY2026 실적, 1·2분기', [self.fiscal_year(2026, 4), multiple])):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), expected)
        # Explicit separators keep the whole year beside a mark the other year reads, in either order. A mark joined
        # to the later year by a joiner the hand-off does not read stays the earlier year's (as in v0.31.0), so the
        # later year is compared whole.
        for text, expected in (('FY2025 Q4, FY2026', [fiscal_q4, self.fiscal_year(2026, 4)]),
                               ('FY2026, FY2025 Q4', [self.fiscal_year(2026, 4), fiscal_q4]),
                               ('FY2025, Q1 & FY2026', [quarter, self.fiscal_year(2026, 4)]),
                               ('FY2025/Q3 FY2026/Q1', [self.fiscal_year(2025, 4), quarter])):
            with self.subTest(text=text):
                self.assertEqual(self.records(text, 4), expected)

    def test_a_list_item_no_row_reads_adds_a_multiple_periods_record(self):
        multiple = ('multiple', None, None, None, 'multiple_periods')
        q1 = ('quarter', '2026-33', D(2026, 1, 1), D(2026, 4, 1), None)
        q3 = ('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)

        def day(month, number):
            start = D(2026, month, number)
            return ('day', start.isoformat(), start, start + timedelta(days=1), None)
        cases = {
            '2026Q1·Q2': [q1, multiple], '2026Q1, Q2': [q1, multiple], '2026Q1/Q2': [q1, multiple],
            '2026Q1 및 Q2': [q1, multiple], '2026Q1과 Q2': [q1, multiple], '2026Q1ㆍQ2': [q1, multiple],
            '2026.03.05, 09.05': [day(3, 5), multiple], '2026년 3월 5일과 9월 5일': [day(3, 5), multiple],
            '2026.10.07/09': [day(10, 7), multiple], '2026.10.07·10.09': [day(10, 7), multiple],
            '2026.10.07 및 10.09': [day(10, 7), multiple], '2026년 10월 7일, 9일': [day(10, 7), multiple],
            '2026. 3. 5., 9. 5.': [day(3, 5), multiple], '2026.10.07(수), 10.09(금)': [day(10, 7), multiple],
            '2026.10.07, 10.09': [day(10, 7), multiple], '2026.03.05, 9월 5일': [day(3, 5), multiple],
            # The same shape without a list abstains too, and so does a number in a unit that may be a period.
            '2026년 3분기, 2027년': [q3, multiple], '2026년 3분기, 12월': [q3, multiple], '2026년 3분기, 3개월': [q3, multiple],
            '2026년 3분기, 3 개월': [q3, multiple], '2026년 3분기, 2차 연도': [q3, multiple], '2026년 3분기, 2차년도': [q3, multiple],
            '2026년 3분기, 2시즌': [q3, multiple], '2026.10.07, 2주': [day(10, 7), multiple],
            # A time, or a count in a unit that is never a period, is not another item.
            '2026년 3분기, 2공장 가동': [q3], '2026-10-07, 10:00': [day(10, 7)], '2026.10.07 / 14:00': [day(10, 7)],
            '2026년 3분기, 120억': [q3], '2026년 3분기, 1,200억 원': [q3], '2026년 3분기, 3만 개': [q3],
            '2026년 3분기·3개 신규 고객': [q3], '2026.10.07, 1차 납품': [day(10, 7)], '2026.10.07, 10시': [day(10, 7)],
            '2026년 3분기 및 15%': [q3], '2026년 3분기와 12명': [q3], '2026년 3분기, 7건': [q3],
            '2026.10.07, 5천 원': [day(10, 7)], '2026.10.07(수), 09:30': [day(10, 7)],
            '2026년 3분기, 120억, 3개 신규 고객': [q3], '2026.10.07, 10:00, 2026.10.09': [day(10, 7), day(10, 9)],
            # A unit may carry a particle or be chained, and a count may have one decimal part.
            '2026년 3분기, 120억원': [q3], '2026년 3분기, 3만 5천 원': [q3], '2026년 3분기, 1억 2천만 원': [q3],
            '2026.10.07, 10시부터': [day(10, 7)], '2026년 3분기, 15.3%': [q3], '2026년 3분기, 12명이 참석': [q3],
            '2026.10.07, 10:00~12:00': [day(10, 7)], '2026년 3분기, 3개 주문': [q3],
            # Unless another number or a Q or H mark that is not a time or a count comes before the next period.
            '2026.10.07, 10:00, 10.09': [day(10, 7), multiple], '2026년 3분기, 3개 신규 고객, 10.09': [q3, multiple],
            '2026년 3분기, 120억, Q4': [q3, multiple], '2026.10.07, 10:00~10.09': [day(10, 7), multiple],
            '2026.10.07, 10:00 10.09까지': [day(10, 7), multiple], '2026년 3분기, 120억 Q4': [q3, multiple],
            # A word that starts with a unit syllable, a number written as a day or a year, and a count of periods
            # are not counts.
            '2026.10.07, 10.09 시행': [day(10, 7), multiple], '2026.10.07, 10.09 개최': [day(10, 7), multiple],
            '2026.10.07, 10.09 만료': [day(10, 7), multiple], '2026.10.07/10.09 시작': [day(10, 7), multiple],
            '2026.10.07, 09 개최': [day(10, 7), multiple], '2026.10.07, 10.09 차례': [day(10, 7), multiple],
            '2026.10.07, 10.09만': [day(10, 7), multiple], '2026.10.07, 10.09 건': [day(10, 7), multiple],
            '2026년 3분기, 2027 시점': [q3, multiple], '2026.10.07, 2027 시행': [day(10, 7), multiple],
            '2026년 3분기, 2027만': [q3, multiple], '2026.10.07, 2026.5만': [day(10, 7), multiple],
            '2026년 3분기, 4개 분기': [q3, multiple], '2026년 3분기, 2개 반기': [q3, multiple],
            '2026년 3분기, 3개 주': [q3, multiple], '2026년 3분기, 3개년 평균': [q3, multiple],
            '2026.10.07, 3만 년': [day(10, 7), multiple], '2026년 3분기, 3천 일': [q3, multiple],
            '2026년 3분기, 3만 개월': [q3, multiple], '2026년 3분기, 1차 시즌': [q3, multiple],
            '2026년 3분기, 10개일년': [q3, multiple],
            '3Q26, 4시작 중 H2': [('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None), multiple],
            '도2026.10.07과 2026 시작은 기준 실적': [day(10, 7), multiple],
            # Over-abstentions: a decimal shaped like a day, a year without a comma, 분 and %p are not read.
            '2026년 3분기, 1.5억': [q3, multiple], '2026년 3분기, 2000만 원': [q3, multiple],
            '2026.10.07, 10시 30분': [day(10, 7), multiple], '2026년 3분기, 15%p': [q3, multiple],
            # Unchanged: the later item is read (a yearless quarter abstains on its own), or a word comes first.
            '2026년 1분기, 2분기': [q1, ('quarter', None, None, None, 'no_year')],
            '2026.03.05, 2026.09.05': [day(3, 5), day(9, 5)],
            '2026년 3분기 매출, 2억': [('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.records(text), expected)
        text = '점검 2026.03.05, 09.05'
        self.assertEqual([text[slice(*record['span'])] for record in period.parse(text)], ['2026.03.05', ', 09'])

    def test_a_long_run_of_numbers_after_a_list_separator_is_read_in_linear_time(self):
        """Each number is read once: a digit-comma run once took seconds at 64 KB and minutes near the text limit."""
        q3 = ('quarter', '2026-35', D(2026, 7, 1), D(2026, 10, 1), None)
        day = ('day', '2026-10-07', D(2026, 10, 7), D(2026, 10, 8), None)
        multiple = ('multiple', None, None, None, 'multiple_periods')
        for text, expected in (('2026년 3분기, ' + '120억, ' + '1,' * 32000 + '1억', [q3, multiple]),
                               ('2026.10.07, ' + '10:00, ' + '1,' * 32000 + '1명', [day, multiple]),
                               ('2026년 3분기, ' + '120억, ' + '111,' * 32000 + '111억', [q3]),
                               ('2026년 3분기, ' + '1명, ' * 32000, [q3])):
            with self.subTest(length=len(text)):
                started = time.monotonic()
                records = self.records(text)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(records, expected)

    def test_format_characters_never_defeat_the_abstaining_checks(self):
        """Cf characters are skipped where a check abstains, never where the table resolves a period."""
        qualifier = ('quarter', None, None, None, 'period_qualifier')
        partial = ('range', None, None, None, 'partial_range')
        multiple = ('multiple', None, None, None, 'multiple_periods')
        q1 = ('quarter', '2026-33', D(2026, 1, 1), D(2026, 4, 1), None)
        day = ('day', '2026-10-07', D(2026, 10, 7), D(2026, 10, 8), None)
        for code in (0x200B, 0x2060, 0x00AD, 0xFEFF, 0x200E, 0x202A, 0x2066, 0x180E, 0xE0001):
            cf = chr(code)
            with self.subTest(code=hex(code)):
                for text in ('2026년 3분기' + cf + '말에 완공', '2026년 3분기 ' + cf + '말에', '2026년 3분기' + cf + ' 후반',
                             '2026년 3분기 마지막' + cf + ' 날', '2026년 4분기' + cf + '연속'):
                    self.assertEqual(self.records(text), [qualifier])
                self.assertEqual(self.records('2026.10.07' + cf + '~10.09'), [day, partial])
                self.assertEqual(self.records('2026.10.07~' + cf + '10.09'), [day, partial])
                self.assertEqual(self.records('10.07' + cf + '~2026.10.07'), [partial, day])
                self.assertEqual(self.records('2026Q1' + cf + '·Q2'), [q1, multiple])
                # A format character between a unit and a letter does not end the word, or a period word after it.
                for item in ('10 시' + cf + '행', '3개' + cf + '분기', '3' + cf + '개월', '3만 ' + cf + '년'):
                    self.assertEqual(self.records('2026.10.07, ' + item), [day, multiple])
                for text in ('Q3' + cf + 'FY2026', 'FY2026' + cf + 'Q3'):
                    self.assertEqual(self.records(text, 4), [('fiscal_quarter', None, None, None, 'period_qualifier')])
                # Inside a period form a format character still stops the table: no new period is read.
                self.assertEqual(period.parse('2026' + cf + '.10.07'), [])
                self.assertEqual(self.records('2026년' + cf + ' 3분기'), [('quarter', None, None, None, 'no_year')])
        listed = {code for first, last in period._CF_RANGES for code in range(first, last + 1)}
        self.assertLessEqual({unicodedata.category(chr(code)) for code in listed}, {'Cf', 'Cn'})
        # The list is Unicode 15.0 category Cf, so it holds every Cf character of 15.1 and earlier versions.
        if tuple(int(part) for part in unicodedata.unidata_version.split('.')) <= (15, 1, 0):
            self.assertEqual({code for code in range(0x110000) if unicodedata.category(chr(code)) == 'Cf'} - listed,
                             set())

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
            # 에서, weekday names in round or square brackets, and a near end written as a month or year.
            '2026.10.07에서 10.09까지': [day(10, 7), partial], '2026년 1월 1일에서 3월 31일까지': [day(1, 1), partial],
            '2026.10.07(수요일)~10.09(금요일)': [day(10, 7), partial], '2026.10.07(Wed)~10.09': [day(10, 7), partial],
            '2026.10.07 (wednesday) ~ 10.09': [day(10, 7), partial], '2026.10.07[수]~10.09': [day(10, 7), partial],
            '2026.10.07(水)~10.09': [day(10, 7), partial], '2026.10.07（수）~10.09': [day(10, 7), partial],
            '10.07(수요일)~2026.10.09(금요일)': [partial, day(10, 9)],
            '2026년 10월~2026.12.31': [partial, day(12, 31)], '2026년 10월부터 2026년 12월 31일까지': [partial, day(12, 31)],
            '2026년~2027년 3분기': [partial, ('quarter', '2027-35', D(2027, 7, 1), D(2027, 10, 1), None)],
            '2026년도에서 2027년 3분기': [partial, ('quarter', '2027-35', D(2027, 7, 1), D(2027, 10, 1), None)],
        }
        for mark in ('\N{HYPHEN}', '\N{NON-BREAKING HYPHEN}', '\N{FIGURE DASH}', '\N{HORIZONTAL BAR}', '\N{MINUS SIGN}',
                     '\N{SMALL TILDE}', ' \N{COMBINING TILDE}', '\N{SWUNG DASH}', '\N{WAVY DASH}', '\N{TWO-EM DASH}',
                     '\N{FULLWIDTH HYPHEN-MINUS}', '--', '~~'):
            cases['2026.10.07' + mark + '10.09'] = [day(10, 7), partial]
            cases['10.07' + mark + '2026.10.09'] = [partial, day(10, 9)]
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.records(text), expected)
        # One fiscal year written as a span has the same shape and abstains too.
        self.assertEqual(self.records('FY2026-27', 4),
                         [('fiscal_year', '2026-04/2027-03', D(2026, 4, 1), D(2027, 4, 1), None), partial])
        # Both ends written: two periods and no partial record.
        for text in ('2026.10.07~2026.10.09', '2026.10.07(수) ~ 2026.10.09(금)', '2026-10-07–2026-10-09',
                     '2026년 10월 7일부터 2026년 10월 9일까지', '2026.10.07에서 2026.10.09까지',
                     '2026.10.07(수요일)~2026.10.09(금요일)', '2026.10.07\N{HORIZONTAL BAR}2026.10.09',
                     '2026-10-07\N{MINUS SIGN}2026-10-09', '2026.10.07--2026.10.09'):
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
        # The YYYY Qn forms take the same bound: the quarter after another number has no year.
        for fiscal_start in (None, 4):
            self.assertTable({text: no_year for text in ('매출 1500 Q3 대비', '1234 Q2', '1234Q2', '1234-Q2', '2100 Q1',
                                                         '1899-Q4', '1500 Q3 말')}, fiscal_start=fiscal_start)
        self.assertTable({'1900 Q1': ('quarter', '1900-33', D(1900, 1, 1), D(1900, 4, 1), None),
                          '2099Q4': ('quarter', '2099-36', D(2099, 10, 1), D(2100, 1, 1), None)})

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
                       'partial_range', 'multiple_periods', 'format characters (Unicode 15.0 category Cf'):
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

    def test_lists_ranges_marks_and_bound_years_never_compare_by_one_item_or_the_whole_year(self):
        multiple = {'normalized_match': 'abstain', 'reason': 'multiple_periods'}
        partial = {'normalized_match': 'abstain', 'reason': 'partial_range'}
        qualifier = {'normalized_match': 'abstain', 'reason': 'period_qualifier'}
        equal = {'normalized_match': 'equal', 'via': ['note0']}
        cases = (
            ('2026Q1·Q2 실적', '2026년 1분기 실적', multiple),
            ('2026Q1, Q2 실적', '2026년 1분기 실적', multiple),
            ('점검 2026.03.05, 09.05', '점검은 2026.03.05에 했다', multiple),
            ('점검 2026년 3월 5일과 9월 5일', '점검은 2026.03.05에 했다', multiple),
            ('출장 2026.10.07에서 10.09까지', '2026.10.07 출발', partial),
            ('출장 2026.10.07(수요일)~10.09(금요일)', '2026.10.07 출발', partial),
            ('출장 2026.10.07\N{HORIZONTAL BAR}10.09', '2026.10.07 출발', partial),
            ('계약 2026년 10월~2026.12.31', '계약은 2026.12.31 종료', partial),
            ('계약 2026년 10월부터 2026년 12월 31일까지', '계약은 2026.12.31 종료', partial),
            ('2026년~2027년 3분기 납품', '2027년 3분기 납품', partial),
            ('매출 1500 Q3 대비', '1500 Q3', {'normalized_match': 'abstain', 'reason': 'no_year'}),
            ('2026년 3분기 2주 차 완공', '2026년 3분기 완공 예정', qualifier),
            ('2026년 3분기' + chr(0x200B) + '말에 완공', '2026년 3분기 완공 예정', qualifier),
            # A body keeps the item or end it writes; fully written ranges keep comparing both ends.
            ('2026년 1분기', '2026Q1·Q2 실적', equal),
            ('2026.10.07', '출장 2026.10.07에서 10.09까지', equal),
            ('계약 2026.10.01~2026.12.31', '계약은 2026.12.31 종료', {'normalized_match': 'none'}),
        )
        for value, body, expected in cases:
            with self.subTest(value=value, body=body):
                self.assertEqual(self.classify(value, body), expected)
        for value, body, expected in (
                ('FY2026/Q3 실적', 'FY2026 실적 요약', qualifier), ('Q3/FY2026 실적', 'FY2026 실적 요약', qualifier),
                ('FY2026 Q 3 실적', 'FY2026 실적 요약', qualifier), ('FY2026 Q5', 'FY2026 실적 요약', qualifier),
                ('FY2026 Q3·Q4', 'FY2026 Q3 실적', multiple),
                # A body whose fiscal year carries a mark is not indexed as the whole year.
                ('FY2026', 'FY2026/Q3 실적', {'normalized_match': 'none'}),
                ('FY2026', 'FY2026 Q3 실적', {'normalized_match': 'contained', 'via': ['note0']})):
            with self.subTest(value=value, body=body, fiscal_start=4):
                self.assertEqual(self.classify(value, body, fiscal_start=4), expected)

    def test_a_mark_between_two_fiscal_years_never_indexes_the_later_year_whole(self):
        """A body that names FY2025 and only a quarter or half of FY2026 is never equal to FY2026."""
        bodies = ('비교 기간: FY2025, Q1 FY2026 실적.', 'FY2024, FY2025, Q1 FY2026.', 'FY2025·1H FY2026 매출 추이.',
                  '가이던스 FY2025: Q4 FY2026 대비.', 'FY2025/Q4 FY2026', '2025 회계연도, Q1 2026 회계연도 실적.',
                  '- fy2025 - q1 fy2026')
        none = {'normalized_match': 'none'}
        for fiscal_start in (1, 4, 12):
            for body in bodies:
                with self.subTest(body=body, fiscal_start=fiscal_start):
                    for value in ('FY2026', 'FY26', '2026 회계연도'):
                        self.assertEqual(self.classify(value, body, fiscal_start=fiscal_start), none)
                    self.assertEqual(self.classify('FY2025', body, fiscal_start=fiscal_start),
                                     {'normalized_match': 'equal', 'via': ['note0']})
                    # Written as a ledger value, the same text abstains through its mark.
                    self.assertEqual(self.classify(body, 'FY2025 실적. FY2026 실적.', fiscal_start=fiscal_start),
                                     {'normalized_match': 'abstain', 'reason': 'period_qualifier'})


if __name__ == '__main__':
    unittest.main()
