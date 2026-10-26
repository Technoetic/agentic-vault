"""Metamorphic tests of vault_period: CheckList's INV and DIR test types over generated cases.

CheckList (Ribeiro, Wu, Guestrin and Singh, ACL 2020, arXiv 2005.04118, sections 2.2 and 2.3) fills
templates from small lexicons, takes their Cartesian product and counts failures. An invariance test (INV) applies
a perturbation that keeps the label and expects the same output; a directional expectation test
(DIR) applies one whose effect is known and expects the output to move only that way. Both compare
outputs with each other, so no gold labels are needed. vault_period's comparisons are ordered
none < contained < equal and abstaining is always safe, so here:

- DIR: a narrowing word after a period (listed or not), an English prefix before it, or a list item
  in a period unit after it never leaves the whole period to compare, and a mark between two fiscal
  years with only whitespace beside it leaves neither year whole. Day forms are left out of the
  narrowing relations: a time inside a day does not narrow it at day granularity (a declared
  convention, not a proven property).
- INV: a neutral follower, or a time or a count after a list separator, keeps the records of the
  period alone; a joiner between a fiscal year and its Q or H mark gives the interval of the
  canonical form (FY2026 Q3) or abstains, never the whole year; and swapping two fiscal years across
  an explicit separator keeps their intervals.

Known failures are a ratchet, not unittest.expectedFailure: KNOWN_FAILURES names the lexicon
entries whose cases fail, a relation's failing cases must equal the cases those entries generate,
and KNOWN_FAILURE_COUNTS pins their number. A new failure fails the test, and so does a fix until
its entry is removed. The lexicons are hand-written, so the counts measure them, not a
false-positive rate on a real vault (measured 2026-10-27). PeriodDifferentialTests parses the same
cases and the existing test strings with vault_period as released in v0.31.0 and checks that no
comparison got stronger, apart from the listed over-abstentions that now read the period whole.
"""
from __future__ import annotations

import ast
from datetime import date
import importlib
import importlib.util
import itertools
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    period = importlib.import_module('vault_period')
except ModuleNotFoundError:
    period = None
try:
    from tests import test_vault_period as base
except ImportError:
    import test_vault_period as base

D = date
# Non-day period forms with the fiscal_start they need. DAY_FORMS join them in the list and INV relations.
FORMS = (('2026년 3분기', None), ('2026년 하반기', None), ('3Q26', None), ('2026Q3', None), ('2026 Q3', None),
         ('2026년 3Q', None), ('FY2026', 4), ('2026 회계연도', 4), ('FY2026 Q3', 4), ('FY2026 3분기', 4),
         ('2026 회계연도 하반기', 4))
DAY_FORMS = (('2026-10-07', None), ('2026.10.07', None), ('2026년 10월 7일', None))

# DIR: narrowing words after a period, spaced, attached or after the period's 의.
SUFFIX_JOINS = (' ', '', '의 ')
NARROWING_LISTED = base.PeriodParsingTests.NARROWING
# Narrowing words outside that list. v0.31.0 compared every one as the whole period. A narrowing word
# after 중, 내 or 동안, a list of months, 3주째 and 1개월 차 are read now; the open words are not.
OPEN_NARROWING = ('늦게', '일찍', '종반', '후기', '막달', '마무리 단계', '첫머리', '이른 시기')
NARROWING_UNLISTED = OPEN_NARROWING + ('중 마지막 달', '중 첫 주', '동안 마지막 주', '내 마지막 주', '중 7월', '7·8월',
                                      '7~8월', '1개월 차', '3주째')
# DIR: English words before a period.
PREFIXES = ('late ', 'Early ', 'mid-', 'mid ', 'end of ', 'the end of ', 'start of ')

# INV: followers that do not narrow a period. v0.31.0 abstained on RECOVERED_NEUTRAL: 초과 is not 초
# (start of period), and 2주 차질, 차량 and 차례 are not week 2.
RECOVERED_NEUTRAL = ('초과', '초과 달성', '초과분', '2주 차질', '2주 차량', '3주 차례')
NEUTRAL = ('중', '중에', '내', '이내', '동안', '이후', '이전', '까지', '부터', '매출', '실적', '매출 120억', '전체', '대비',
           '목표', '계획') + RECOVERED_NEUTRAL
NEUTRAL_JOINS = (' ', '')

# INV: a time or a count after a list separator is not another item (v0.31.0: multiple_periods).
LIST_SEPARATORS = (', ', ' / ', '·', ' 및 ')
NOT_ITEMS = ('10:00', '14:30 회의', '120억', '3만 개', '5천 원', '3개 신규 고객', '12명', '7건', '1차 납품', '10시', '15%',
             '2공장 가동')
# DIR: an item in a period unit keeps the value abstaining, so it is never compared by its first item,
# also when it comes after a time or a count that is let through.
PERIOD_ITEMS = ('10.09', '9월 5일', '12월', '2주', '3일', '4분기', 'Q2', 'H2', '2027년', '3개월', '2차 연도')
ITEMS_BEFORE = ('', '10:00, ', '3개 신규 고객, ')

# INV: joiners between a fiscal year and a Q or H mark, in both orders, at fiscal_start 4.
FISCAL_YEARS = ('FY2026', '2026 회계연도', 'FY26')
FY_Q3 = (D(2026, 10, 1), D(2027, 1, 1))
FY_H2 = (D(2026, 10, 1), D(2027, 4, 1))
# Each mark with the interval of its canonical form (FY2026 Q3, FY2026 하반기).
JOIN_MARKS = (('Q3', FY_Q3), ('3분기', FY_Q3), ('3Q', FY_Q3), ('하반기', FY_H2), ('H2', FY_H2), ('2H', FY_H2))
WORD_CONNECTORS = (' 중 ', ' 기준 ', ', 그리고 ')
CONNECTORS = (' ', '\t', '', '  ', '\n', chr(0xA0), chr(0x3000), chr(0x200B), '/', ' / ', ', ', '.', '. ', ': ', '_',
              '-', ' - ', '(', ' (', '·', '의 ', ' 의 ', '&', ' & ', '[', ' [', '+', ' + ', '|', ' | ', ';', '; ', '>',
              ' > ', '\N{EM DASH}', ' \N{EM DASH} ', '~', ' ~ ', '#', '*', ' 및 ', '과 ', '와 ') + WORD_CONNECTORS

# DIR: two fiscal years with a mark between them and only whitespace or a format character beside
# it. The mark may be either year's, so neither year may be compared whole.
GAPS = ('', ' ', '\t', '  ', chr(0x200B))
AMBIGUOUS_MARKS = ('Q1', 'Q4', 'H1', 'H2', '1Q', '4Q', '1H', '2H', '3분기', '하반기', '1·2분기', '1~2분기', '상·하반기')
# A mark joined after each of two fiscal years (FY2025/Q3 FY2026/Q1): the hand-off reads Q3 as the
# later year's mark and the earlier year whole.
PAIR_JOINS = ('/', '-', '_', '·')
PAIR_MARKS = ('Q3', 'H1', '3분기', '하반기')
FY2025 = (D(2025, 4, 1), D(2026, 4, 1))
FY2026 = (D(2026, 4, 1), D(2027, 4, 1))

# INV: swapping two fiscal years across an explicit separator keeps their intervals.
PERMUTATION_MARKS = ('Q4', 'H1', '3분기', '하반기', '1Q')
PERMUTATION_SEPARATORS = (', ', '; ', ' / ', ' 및 ', ' & ', ' + ', ' | ', ' > ')


def resolved(records):
    return {(record['start'], record['end']) for record in records if record['reason'] is None}


def signature(records):
    return [(record['kind'], record['start'], record['end'], record['reason']) for record in records]


def _attached_digit(form, join, word):
    """A digit written onto a form that ends in an ASCII letter or digit (3Q262주) is another number."""
    return join == '' and word[0].isdigit() and form[-1].isascii() and form[-1].isalnum()


def _suffixes(words):
    return [(dict(form=form, word=word, join=join), form + join + word, fiscal_start)
            for (form, fiscal_start), word, join in itertools.product(FORMS, words, SUFFIX_JOINS)]


def _prefixes():
    return [(dict(form=form, prefix=prefix), prefix + form, fiscal_start)
            for (form, fiscal_start), prefix in itertools.product(FORMS, PREFIXES)]


def _list_items(items, before=('',)):
    return [(dict(form=form, separator=separator, before=first, item=item), form + separator + first + item, fiscal_start)
            for (form, fiscal_start), separator, first, item
            in itertools.product(FORMS + DAY_FORMS, LIST_SEPARATORS, before, items)]


def _neutral():
    return [(dict(form=form, word=word, join=join), form + join + word, fiscal_start)
            for (form, fiscal_start), word, join in itertools.product(FORMS + DAY_FORMS, NEUTRAL, NEUTRAL_JOINS)
            if not _attached_digit(form, join, word)]


def _joiners():
    return [(dict(year=year, mark=mark, connector=connector, order=order, canonical=canonical), text, 4)
            for year, (mark, canonical), connector in itertools.product(FISCAL_YEARS, JOIN_MARKS, CONNECTORS)
            for order, text in (('after', year + connector + mark), ('before', mark + connector + year))]


def _between_fiscal_years():
    cases = [(dict(template='gap', gap=gap, mark=mark), f'FY2025{gap}{mark} FY2026', 4)
             for gap, mark in itertools.product(GAPS, AMBIGUOUS_MARKS) if gap or not mark[0].isdigit()]
    return cases + [(dict(template='pair', join=join, mark=mark), f'FY2025{join}{mark} FY2026{join}Q1', 4)
                    for join, mark in itertools.product(PAIR_JOINS, PAIR_MARKS)]


def _fiscal_year_orders():
    return [(dict(mark=mark, separator=separator, swapped=f'FY2026{separator}FY2025 {mark}'),
             f'FY2025 {mark}{separator}FY2026', 4)
            for mark, separator in itertools.product(PERMUTATION_MARKS, PERMUTATION_SEPARATORS)]


def _whole(module, form, fiscal_start):
    (interval,) = resolved(module.parse(form, fiscal_start=fiscal_start))
    return interval


def _narrowed(module, fields, records, fiscal_start):
    return _whole(module, fields['form'], fiscal_start) not in resolved(records)


def _abstains(module, fields, records, fiscal_start):
    return any(record['reason'] for record in records)


def _unchanged(module, fields, records, fiscal_start):
    return signature(records) == signature(module.parse(fields['form'], fiscal_start=fiscal_start))


def _canonical_or_abstain(module, fields, records, fiscal_start):
    return resolved(records) <= {fields['canonical']}


def _neither_year_whole(module, fields, records, fiscal_start):
    return not resolved(records) & {FY2025, FY2026}


def _same_intervals(module, fields, records, fiscal_start):
    return resolved(records) == resolved(module.parse(fields['swapped'], fiscal_start=fiscal_start))


# relation: (case generator, the check that a case holds)
RELATIONS = {
    'dir_listed_narrowing': (lambda: _suffixes(NARROWING_LISTED), _narrowed),
    'dir_unlisted_narrowing': (lambda: _suffixes(NARROWING_UNLISTED), _narrowed),
    'dir_english_prefix': (_prefixes, _narrowed),
    'dir_period_list_item': (lambda: _list_items(PERIOD_ITEMS, ITEMS_BEFORE), _abstains),
    'dir_mark_between_fiscal_years': (_between_fiscal_years, _neither_year_whole),
    'inv_neutral_follower': (_neutral, _unchanged),
    'inv_not_a_list_item': (lambda: _list_items(NOT_ITEMS), _unchanged),
    'inv_fiscal_joiner': (_joiners, _canonical_or_abstain),
    'inv_fiscal_year_order': (_fiscal_year_orders, _same_intervals),
}
# The lexicon entries whose every case fails today, by relation (no entry: no known failure).
KNOWN_FAILURES = {
    # Open narrowing words the fixed lists do not hold.
    'dir_unlisted_narrowing': {'word': OPEN_NARROWING},
    # Word connectors: FY2026 중 Q3 and FY2026 기준 3분기 compare the whole fiscal year.
    'inv_fiscal_joiner': {'connector': WORD_CONNECTORS},
    # FY2025/Q3 FY2026/Q1: Q3 goes with FY2026, and FY2025 is compared whole.
    'dir_mark_between_fiscal_years': {'template': ('pair',)},
}
# Generated and failing cases per relation, measured 2026-10-27 (v0.31.0 failed the number in each comment).
CASE_COUNTS = {
    'dir_listed_narrowing': 1419, 'dir_unlisted_narrowing': 561, 'dir_english_prefix': 77, 'dir_period_list_item': 1848,
    'dir_mark_between_fiscal_years': 74, 'inv_neutral_follower': 592, 'inv_not_a_list_item': 672,
    'inv_fiscal_joiner': 1656, 'inv_fiscal_year_order': 40,
}
KNOWN_FAILURE_COUNTS = {
    'dir_listed_narrowing': 0,  # 0
    'dir_unlisted_narrowing': 264,  # 537
    'dir_english_prefix': 0,  # 77
    'dir_period_list_item': 0,  # 0
    'dir_mark_between_fiscal_years': 16,  # 74
    'inv_neutral_follower': 0,  # 114
    'inv_not_a_list_item': 0,  # 672
    'inv_fiscal_joiner': 108,  # 864
    'inv_fiscal_year_order': 0,  # 0
}


def cases(name):
    return RELATIONS[name][0]()


def failures(module, name):
    """The texts of the relation's cases that do not hold under module."""
    holds = RELATIONS[name][1]
    return {text for fields, text, fiscal_start in cases(name)
            if not holds(module, fields, module.parse(text, fiscal_start=fiscal_start), fiscal_start)}


def known_failures(name):
    """The texts of the relation's cases that KNOWN_FAILURES names."""
    spec = KNOWN_FAILURES.get(name)
    return {text for fields, text, _ in cases(name)
            if spec and all(fields[key] in values for key, values in spec.items())}


class PeriodMetamorphicTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(period, 'period normalizer is missing')

    def assertRelation(self, name):
        generated = cases(name)
        self.assertEqual(len({text for _, text, _ in generated}), len(generated), 'generated texts must be distinct')
        self.assertEqual(len(generated), CASE_COUNTS[name])
        failing, known = failures(period, name), known_failures(name)
        self.assertEqual(sorted(failing - known)[:20], [], 'new failures')
        self.assertEqual(sorted(known - failing)[:20], [], 'fixed: remove the entry from KNOWN_FAILURES')
        self.assertEqual(len(failing), KNOWN_FAILURE_COUNTS[name])

    def test_listed_narrowing_words_never_leave_the_whole_period(self):
        self.assertRelation('dir_listed_narrowing')

    def test_unlisted_narrowing_words_fail_only_for_the_open_words(self):
        self.assertRelation('dir_unlisted_narrowing')

    def test_english_prefixes_never_leave_the_whole_period(self):
        self.assertRelation('dir_english_prefix')

    def test_a_list_item_in_a_period_unit_keeps_the_value_abstaining(self):
        self.assertRelation('dir_period_list_item')

    def test_a_mark_between_two_fiscal_years_leaves_neither_year_whole(self):
        self.assertRelation('dir_mark_between_fiscal_years')

    def test_neutral_followers_keep_the_records_of_the_period(self):
        self.assertRelation('inv_neutral_follower')

    def test_a_time_or_count_after_a_list_separator_keeps_the_records_of_the_period(self):
        self.assertRelation('inv_not_a_list_item')

    def test_a_joiner_between_a_fiscal_year_and_its_mark_never_gives_the_whole_year(self):
        self.assertRelation('inv_fiscal_joiner')

    def test_swapping_two_fiscal_years_across_a_separator_keeps_their_intervals(self):
        self.assertRelation('inv_fiscal_year_order')

    def test_every_relation_is_pinned_and_known_failures_name_generated_fields(self):
        self.assertEqual(set(CASE_COUNTS), set(RELATIONS))
        self.assertEqual(set(KNOWN_FAILURE_COUNTS), set(RELATIONS))
        self.assertLessEqual(set(KNOWN_FAILURES), set(RELATIONS))
        for name, spec in KNOWN_FAILURES.items():
            fields = set(cases(name)[0][0])
            with self.subTest(relation=name):
                self.assertLessEqual(set(spec), fields)
                self.assertTrue(known_failures(name))
        # The unlisted words are outside the listed tuple, and the open words are a part of them.
        self.assertFalse(set(NARROWING_UNLISTED) & set(NARROWING_LISTED))
        self.assertLessEqual(set(OPEN_NARROWING), set(NARROWING_UNLISTED))

    def test_the_suite_loads_vault_period_and_never_vault_ssot(self):
        """The relations call vault_period.parse only: the default check-support path is not involved."""
        script = ('import sys\nsys.path.insert(0, sys.argv[1])\nimport tests.test_vault_period_metamorphic\n'
                  "print('vault_period' in sys.modules, 'vault_ssot' in sys.modules)\n")
        completed = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(ROOT)], capture_output=True,
                                   text=True, encoding='utf-8', cwd=ROOT, timeout=120)
        self.assertEqual((completed.returncode, completed.stdout.split()), (0, ['True', 'False']), completed.stderr)


RELEASED = 'v0.31.0'
FISCAL_STARTS = (None, 1, 4, 12)
# Strings of the existing period tests, parsed by both versions as well as the generated cases.
EXISTING_TESTS = ('test_vault_period.py', 'test_vault_ssot.py')
# Over-abstentions that now read the period whole: v0.31.0 abstained on these followers, and each
# text must now compare exactly as v0.31.0 compares its form alone.
RECOVERED = {
    'inv_neutral_follower': {'word': RECOVERED_NEUTRAL},
    'inv_not_a_list_item': {'item': NOT_ITEMS},
}
# Existing test strings recovered the same way that the lexicons do not generate, with their forms.
RECOVERED_EXISTING = {'2026.10.07 / 14:00': '2026.10.07', '2026.10.07(수), 09:30': '2026.10.07(수)',
                      '2026년 3분기, 1,200억 원': '2026년 3분기', '2026년 3분기와 12명': '2026년 3분기',
                      '2026년 3분기, 120억, 3개 신규 고객': '2026년 3분기',
                      '2026.10.07, 10:00, 2026.10.09': '2026.10.07, 2026.10.09'}


def released_period():
    """(vault_period as released in RELEASED, None) or (None, the reason it cannot be loaded)."""
    git = shutil.which('git')
    if git is None:
        return None, 'git is not installed'
    try:
        completed = subprocess.run([git, 'show', f'{RELEASED}:skills/agentic-vault/scripts/vault_period.py'],
                                   cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f'git show failed ({type(exc).__name__})'
    if completed.returncode:
        return None, f'the {RELEASED} tag is not in this checkout (a shallow clone has no tags)'
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('vault_period_released', loader=None))
    exec(compile(completed.stdout.decode('utf-8'), f'{RELEASED}:vault_period.py', 'exec'), module.__dict__)
    return module, None


def compared(records):
    """The intervals a value compares, or None when it abstains or names no period."""
    if not records or any(record['reason'] for record in records):
        return None
    return {(record['start'], record['end']) for record in records}


def stronger(old_records, new_records):
    """Whether new_records let a value or a cited body compare more strongly than old_records.

    As a body, a newly indexed interval can make some value equal or
    contained. As a value, comparing where it abstained, or no longer
    comparing an interval it compared, can make the weakest relation stronger;
    comparing more intervals can only make it weaker.
    """
    if resolved(new_records) - resolved(old_records):
        return True
    new, old = compared(new_records), compared(old_records)
    return new is not None and (old is None or not old <= new)


def existing_strings():
    texts = set()
    for name in EXISTING_TESTS:
        tree = ast.parse((ROOT / 'tests' / name).read_text(encoding='utf-8'))
        texts.update(node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str))
    return texts


class PeriodDifferentialTests(unittest.TestCase):
    """No change since v0.31.0 lets a comparison get stronger (none < contained < equal) beyond the recoveries."""

    @classmethod
    def setUpClass(cls):
        cls.released, cls.unavailable = released_period()

    def setUp(self):
        self.assertIsNotNone(period, 'period normalizer is missing')
        if self.released is None:
            self.skipTest(f'cannot compare with {RELEASED}: {self.unavailable}')

    def test_no_comparison_gets_stronger_than_in_the_released_version(self):
        generated = {text for name in RELATIONS for _, text, _ in cases(name)}
        generated |= {fields['swapped'] for fields, _, _ in cases('inv_fiscal_year_order')}
        recovered = {text: fields['form'] for name, spec in RECOVERED.items() for fields, text, _ in cases(name)
                     if all(fields[key] in values for key, values in spec.items())}
        recovered.update(RECOVERED_EXISTING)
        stronger_texts, reads = set(), 0
        for text in sorted(generated | existing_strings()):
            for fiscal_start in FISCAL_STARTS:
                old = self.released.parse(text, fiscal_start=fiscal_start)
                new = period.parse(text, fiscal_start=fiscal_start)
                reads += 1
                if not stronger(old, new):
                    continue
                stronger_texts.add(text)
                with self.subTest(text=text, fiscal_start=fiscal_start):
                    self.assertTrue(text in recovered, f'compares more strongly than in {RELEASED}')
                    # A recovery compares exactly as the released version compares the form alone.
                    form = self.released.parse(recovered[text], fiscal_start=fiscal_start)
                    self.assertEqual((compared(new), resolved(new)), (compared(form), resolved(form)))
        self.assertGreater(reads, 20000)
        # Every listed existing string is recovered, so the list cannot go stale.
        self.assertLessEqual(set(RECOVERED_EXISTING), stronger_texts)


if __name__ == '__main__':
    unittest.main()
