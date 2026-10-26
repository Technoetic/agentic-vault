"""Linear line parsers that replaced super-linear lazy-suffix regexes.

The former patterns live only here, as oracles. The acceptance gates borrow the
two tiers of VULCANBOOST (Li et al., "VULCANBOOST: Boosting ReDoS Fixes through
Symbolic Representation and Feature Normalization", USENIX Security 2025), not
its DFA machinery: tier A, fixed cases (CommonMark-derived and vault lines)
agree with the oracle; tier B, bounded-exhaustive equivalence with the oracle
over a small alphabet. A generous timing gate on the new code stands in for
"free from ReDoS". The default suite never times the old regexes; set
AGENTIC_VAULT_SLOW_TESTS=1 for the larger exhaustive runs and the old-versus-new
timings.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager, ExitStack
import io
import itertools
import json
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import time
from typing import Callable, NamedTuple
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_egress  # noqa: E402,F401  (lint loads it lazily; keep the run self-contained)
import vault_healthcheck  # noqa: E402
import vault_lint_extensions as lint  # noqa: E402
import vault_provenance  # noqa: E402
import vault_query_expansion  # noqa: E402
import vault_recall  # noqa: E402
import vault_ssot  # noqa: E402
import vault_state  # noqa: E402
import vault_warning_policy  # noqa: E402

SLOW = os.environ.get('AGENTIC_VAULT_SLOW_TESTS') == '1'

# --- Oracles: the v0.31.0 patterns, kept only in this file. ---
OLD_ATX = re.compile(r' {0,3}(#{1,6})\s+(.+?)\s*#*$')                # vault_ssot.py:162, vault_state.py:579
OLD_LINT_HEADING = re.compile(r'^ {0,3}#{1,6}\s+(.+?)\s*$')          # vault_lint_extensions.py:141
OLD_LINT_CLOSING = re.compile(r'\s+#+\s*$')                          # vault_lint_extensions.py:143 (re.sub)
OLD_LINT_DATE = re.compile(r'^(created|updated):\s*(.*?)\s*$')       # vault_lint_extensions.py:194
OLD_LINT_COMMENT = re.compile(r'\s+#')                               # vault_lint_extensions.py:197 (re.split)
OLD_HEADER_FIELD = re.compile(r'^([a-z_]+)\s*:\s*(.*?)\s*$')         # vault_provenance.py:436
OLD_RULE_HEADING = re.compile(r'^#{1,3}\s+(.+?)\s*$')                # vault_healthcheck.py:1561
OLD_RULE_PAREN = re.compile(r'\s*\([^)]*\)')                         # vault_healthcheck.py:1570 (re.sub)
OLD_RULE_DASH = re.compile(r'\s*[—-]\s.*$')                          # vault_healthcheck.py:1571 (re.sub)
OLD_TITLE = re.compile(r'^\s*title\s*:\s*(.*?)\s*$', re.IGNORECASE)  # vault_recall.py:40
OLD_RECALL_HEADING = re.compile(r'^\s*#{1,6}\s+(.+?)\s*#*\s*$')      # vault_recall.py:41, vault_query_expansion.py:478


def oracle_atx(text):
    match = OLD_ATX.match(text)
    return (len(match[1]), match[2]) if match else None


def oracle_lint_heading(line):
    match = OLD_LINT_HEADING.match(line)
    return match[1] if match else None


def oracle_closing(text):
    return OLD_LINT_CLOSING.sub('', text)


def oracle_date_field(line):
    match = OLD_LINT_DATE.match(line)
    return (match[1], match[2]) if match else None


def oracle_before_comment(value):
    return OLD_LINT_COMMENT.split(value, maxsplit=1)[0]


def oracle_header_field(line):
    match = OLD_HEADER_FIELD.match(line)
    return (match[1], match[2]) if match else None


def oracle_rule_heading(line):
    match = OLD_RULE_HEADING.match(line)
    return match.group(1) if match else None


def oracle_paren(text):
    return OLD_RULE_PAREN.sub('', text)


def oracle_dash(text):
    return OLD_RULE_DASH.sub('', text)


def oracle_title_value(line):
    match = OLD_TITLE.match(line)
    return match.group(1) if match else None


def oracle_heading_label(line):
    match = OLD_RECALL_HEADING.match(line)
    return match.group(1) if match else None


# One code point of each kind of whitespace a split line can still hold: ASCII
# space and tab, vertical tab, file separator, NEL, no-break space, line
# separator and ideographic space (bytes.splitlines splits only on CR and LF).
WHITESPACE = ' \t\x0b\x1c\x85\xa0\u2028\u3000'
RECORD = WHITESPACE + '#a가'  # the 11-symbol alphabet of the deep verification


class Pair(NamedTuple):
    name: str
    module: object
    attr: str
    oracle: Callable
    runs: tuple  # ((alphabet, maximum length), ...)
    prefixes: tuple = ('',)
    newline_free: bool = True  # False: equal even when the text holds a line feed
    special: str = '#a가'  # non-whitespace symbols for the slow all-whitespace run

    def new(self):
        return getattr(self.module, self.attr)


PAIRS = (
    Pair('ssot ATX heading', vault_ssot, '_atx_heading', oracle_atx, ((RECORD, 6),)),
    Pair('state ATX heading', vault_state, '_atx_heading', oracle_atx, ((RECORD, 6),)),
    Pair('lint heading text', lint, '_heading_text', oracle_lint_heading, ((RECORD, 6),)),
    Pair('lint closing run', lint, '_without_closing_run', oracle_closing,
         ((RECORD, 6), (RECORD + '\n', 5)), newline_free=False),
    Pair('lint date field', lint, '_date_field', oracle_date_field, ((RECORD + ':', 5),),
         prefixes=('', 'created:', 'updated:', 'created :', 'Updated:'), special='#a:'),
    Pair('lint comment split', lint, '_before_comment', oracle_before_comment,
         ((RECORD, 6), (RECORD + '\n', 5)), newline_free=False),
    Pair('provenance header field', vault_provenance, '_header_field', oracle_header_field,
         ((RECORD + ':', 5),), prefixes=('', 'k', 'key:', 'a_b :', 'Key:'), special='#a_:'),
    Pair('rule heading', vault_healthcheck, '_rule_heading', oracle_rule_heading, ((RECORD, 6),)),
    Pair('rule parenthesized note', vault_healthcheck, '_drop_parenthesized', oracle_paren,
         ((' \t\n\xa0\u3000()a', 6),), newline_free=False, special='()a'),
    Pair('rule dash tail', vault_healthcheck, '_drop_dash_tail', oracle_dash,
         ((' \t\xa0\u2028\u3000—-a', 6),), special='—-a'),
    Pair('recall title value', vault_recall, '_title_value', oracle_title_value, ((RECORD + ':', 5),),
         prefixes=('', 'title', ' TITLE :', 'Title\t:', 't\u0131tle:', 't\u0130tle:'), special='#a:'),
    Pair('recall heading label', vault_recall, '_heading_label', oracle_heading_label, ((RECORD, 6),)),
)
PAIR = {pair.name: pair for pair in PAIRS}


def strings(alphabet, length, prefixes=('',)):
    for prefix in prefixes:
        for size in range(length + 1):
            for chars in itertools.product(alphabet, repeat=size):
                yield prefix + ''.join(chars)


def mismatches(pair, values, limit=5):
    """Return (count, first mismatches) of new helper against oracle."""
    new, found, count = pair.new(), [], 0
    for value in values:
        count += 1
        expected, actual = pair.oracle(value), new(value)
        if expected != actual and len(found) < limit:
            found.append((value, expected, actual))
    return count, found


# --- Tier A: CommonMark 0.31.2, section 4.2 "ATX headings", examples 62-79. ---
# CommonMark Spec by John MacFarlane, CC BY-SA 4.0. Every input below is a
# paraphrase written for this test (other words, widths and counts) that
# exercises the rule of the cited example; no spec text is copied. `expected`
# is what the former regex captured as (level, raw label); `commonmark` is the
# heading a CommonMark parser builds from the same line, as (level, text
# content after inline parsing), or None. `cm_deviates` marks the examples
# where the regex behaviour, kept on purpose, differs from CommonMark.
COMMONMARK_CASES = (
    dict(example=62, cm_deviates=False,
         lines=('# alpha', '## alpha', '### alpha', '#### alpha', '##### alpha', '###### alpha'),
         expected=((1, 'alpha'), (2, 'alpha'), (3, 'alpha'), (4, 'alpha'), (5, 'alpha'), (6, 'alpha')),
         commonmark=((1, 'alpha'), (2, 'alpha'), (3, 'alpha'), (4, 'alpha'), (5, 'alpha'), (6, 'alpha'))),
    dict(example=63, cm_deviates=False, lines=('####### alpha',), expected=(None,), commonmark=(None,)),
    dict(example=64, cm_deviates=False, lines=('#3 nuts', '#tagged'), expected=(None, None),
         commonmark=(None, None)),
    dict(example=65, cm_deviates=False, lines=('\\## alpha',), expected=(None,), commonmark=(None,)),
    # Inline markup and escapes stay raw in the label (no inline parsing).
    dict(example=66, cm_deviates=True, lines=('# alpha *beta* \\*gamma\\*',),
         expected=((1, 'alpha *beta* \\*gamma\\*'),), commonmark=((1, 'alpha beta *gamma*'),)),
    dict(example=67, cm_deviates=False, lines=('#' + ' ' * 9 + 'alpha' + ' ' * 11,),
         expected=((1, 'alpha'),), commonmark=((1, 'alpha'),)),
    dict(example=68, cm_deviates=False, lines=(' ### alpha', '  ## alpha', '   # alpha'),
         expected=((3, 'alpha'), (2, 'alpha'), (1, 'alpha')),
         commonmark=((3, 'alpha'), (2, 'alpha'), (1, 'alpha'))),
    dict(example=69, cm_deviates=False, lines=('    # alpha',), expected=(None,), commonmark=(None,)),
    dict(example=70, cm_deviates=False, lines=('alpha', '    # beta'), expected=(None, None),
         commonmark=(None, None)),
    dict(example=71, cm_deviates=False, lines=('## alpha ##', ' ###  beta   ###'),
         expected=((2, 'alpha'), (3, 'beta')), commonmark=((2, 'alpha'), (3, 'beta'))),
    dict(example=72, cm_deviates=False, lines=('# alpha ' + '#' * 20, '#### alpha #'),
         expected=((1, 'alpha'), (4, 'alpha')), commonmark=((1, 'alpha'), (4, 'alpha'))),
    # Whitespace after the closing run defeats \s*#*$, so the run stays in the label.
    dict(example=73, cm_deviates=True, lines=('### alpha ###' + ' ' * 4,),
         expected=((3, 'alpha ###'),), commonmark=((3, 'alpha'),)),
    dict(example=74, cm_deviates=False, lines=('### alpha ### z',),
         expected=((3, 'alpha ### z'),), commonmark=((3, 'alpha ### z'),)),
    # The regex strips a closing run that no whitespace precedes.
    dict(example=75, cm_deviates=True, lines=('# alpha#',), expected=((1, 'alpha'),),
         commonmark=((1, 'alpha#'),)),
    # Backslash escapes are not modelled: the trailing '#' run is stripped.
    dict(example=76, cm_deviates=True, lines=('### alpha \\###', '## alpha #\\##', '# alpha \\#'),
         expected=((3, 'alpha \\'), (2, 'alpha #\\'), (1, 'alpha \\')),
         commonmark=((3, 'alpha ###'), (2, 'alpha ###'), (1, 'alpha #'))),
    dict(example=77, cm_deviates=False, lines=('***', '## alpha', '***'),
         expected=(None, (2, 'alpha'), None), commonmark=(None, (2, 'alpha'), None)),
    dict(example=78, cm_deviates=False, lines=('Some text', '# alpha', 'More text'),
         expected=(None, (1, 'alpha'), None), commonmark=(None, (1, 'alpha'), None)),
    # Empty headings: no heading at all, and a lone closing run becomes the label '#'.
    dict(example=79, cm_deviates=True, lines=('### ', '##', '## ##'),
         expected=(None, None, (2, '#')), commonmark=((3, ''), (2, ''), (2, ''))),
)
DEVIATING_EXAMPLES = (66, 73, 75, 76, 79)
# Lines from the deep verification and the vault's own section names.
VAULT_CASES = (
    ('####### a', None), ('    # a', None), ('#a', None), ('# ', None), ('#', None),
    ('#  ', (1, ' ')), ('# #', (1, '#')), ('### ###', (3, '#')), ('# foo#', (1, 'foo')),
    ('# a# #', (1, 'a#')), ('# a #b', (1, 'a #b')), ('#\u3000foo', (1, 'foo')),
    ('## 확정 ###' + ' ' * 3, (2, '확정 ###')), ('## 확정 ###', (2, '확정')),
    ('### Blocked ###', (3, 'Blocked')), ('#\u3000확정', (1, '확정')), ('#' * 7 + ' 확정', None),
    ('## ✅ 확정 — 2026', (2, '✅ 확정 — 2026')), ('## 보류\t', (2, '보류')),
)

# Lines that carry every line boundary str.splitlines knows, mid-line.
BOUNDARIES = '\r', '\r\n', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'


def separated(line):
    """Insert each boundary into a copy of line, after its first character."""
    return [line[:1] + mark + line[1:] for mark in BOUNDARIES]


def generated_lines(count=500, seed=20261027):
    """Deterministic heading-like lines for the default-unchanged comparisons."""
    rng = random.Random(seed)
    pieces = (' ', ' ', '\t', '\xa0', '\u3000', '\x0b', '\x85', '#', '##', 'a', 'b', '확정',
              'Blocked', '차단', '보류', 'confirmed', '✅', '—', '-', ':', '(note)', '(', ')',
              'title:', 'created:', '2026-10-27', '\\')
    lines = []
    for _ in range(count):
        head = rng.choice(('', ' ', '  ', '   ', '    ')) + '#' * rng.randint(0, 7)
        body = ''.join(rng.choice(pieces) for _ in range(rng.randint(0, 7)))
        lines.append(head + rng.choice(('', ' ', '\t', '\u3000')) + body)
    return lines


LINES = tuple(line for case in COMMONMARK_CASES for line in case['lines']) + tuple(
    line for line, _ in VAULT_CASES) + tuple(generated_lines())


@contextmanager
def recorded(test, pair):
    """Record what a caller passes to the new helper and hold it to the oracle."""
    real, calls = pair.new(), []

    def record(value):
        calls.append(value)
        return real(value)

    with mock.patch.object(pair.module, pair.attr, record):
        yield calls
    test.assertTrue(calls, f'{pair.name}: the caller never reached the helper')
    for value in calls:
        if pair.newline_free:
            test.assertNotIn('\n', value, pair.name)
        test.assertEqual(real(value), pair.oracle(value), (pair.name, value))


@contextmanager
def oracles(*names):
    """Run the callers with the former regexes in place of the new helpers."""
    with ExitStack() as stack:
        for name in names:
            pair = PAIR[name]
            stack.enter_context(mock.patch.object(pair.module, pair.attr, pair.oracle))
        yield


def ledger_note(heading):
    return (heading + '\n\n| subject | relation | value | source | status |\n'
            '| --- | --- | --- | --- | --- |\n| Atlas | retention | 12 |  |  |\n')


def tasks_note(headings):
    return ''.join(f'{heading}\n- [ ] Item {number}\n' for number, heading in enumerate(headings)).encode('utf-8')


class TierACasesTests(unittest.TestCase):
    """Tier A: fixed cases agree with the oracle and keep the regex behaviour."""

    def test_commonmark_examples_keep_the_former_regex_results(self):
        self.assertEqual([case['example'] for case in COMMONMARK_CASES], list(range(62, 80)))
        for case in COMMONMARK_CASES:
            for line, expected, commonmark in zip(case['lines'], case['expected'], case['commonmark'],
                                                  strict=True):
                with self.subTest(example=case['example'], line=line):
                    self.assertEqual(oracle_atx(line), expected)
                    self.assertEqual(vault_ssot._atx_heading(line), expected)
                    self.assertEqual(vault_state._atx_heading(line), expected)

    def test_deviations_from_commonmark_are_exactly_the_documented_examples(self):
        def stripped(result):
            return None if result is None else (result[0], result[1].strip())

        deviating = []
        for case in COMMONMARK_CASES:
            differs = any(stripped(expected) != commonmark
                          for expected, commonmark in zip(case['expected'], case['commonmark'], strict=True))
            with self.subTest(example=case['example']):
                self.assertEqual(differs, case['cm_deviates'])
            if differs:
                deviating.append(case['example'])
        self.assertEqual(tuple(deviating), DEVIATING_EXAMPLES)

    def test_vault_cases_keep_the_former_regex_results(self):
        for line, expected in VAULT_CASES:
            with self.subTest(line=line):
                self.assertEqual(oracle_atx(line), expected)
                self.assertEqual(vault_ssot._atx_heading(line), expected)
                self.assertEqual(vault_state._atx_heading(line), expected)

    def test_section_labels_keep_their_ledger_and_task_meaning(self):
        def status(heading):
            return vault_ssot.parse_ledger(ledger_note(heading))['tables'][0]['section_status']

        # A closing run followed by spaces stays in the label, so it is not '확정'.
        self.assertEqual(status('## 확정 ###   '), '')
        self.assertEqual(status('## 확정 ###'), 'confirmed')
        self.assertEqual(status('#\u3000확정'), 'confirmed')
        self.assertEqual(status('####### 확정'), '')
        items = vault_state._items(tasks_note(['### Blocked ###', '## 보류 ##  ', '# 차단#']), 'tasks')
        self.assertEqual([item['state'] for item in items], ['blocked', 'pending', 'blocked'])

    def test_other_line_parsers_keep_their_fixed_cases(self):
        cases = {
            'lint heading text': ('# a', '#  ', '# a ##', '    # a', '#\ta\t'),
            'lint closing run': ('a ##', 'a#', 'a ## ', 'a ## b', ' #', '#'),
            'lint date field': ('created: 2026-10-27', 'updated:', 'created:  x # c  ', 'Created: x'),
            'lint comment split': ('2026-10-27 # note', 'a#b', 'a #b #c', '# a', ' #'),
            'provenance header field': ('key: value', 'a_b :  "x" ', 'Key: v', 'k:', 'k v'),
            'rule heading': ('## Rule', '#### Rule', '#  ', '# Rule (note) — tail', '#Rule'),
            'rule parenthesized note': ('a (b) c', 'a  (b', 'a) (b)', '(x)(y)'),
            'rule dash tail': ('a — b', 'a-b', 'a - b - c', '- a', 'a —'),
            'recall title value': ('title: x', ' TITLE : x ', 'title:', 't\u0131tle: x', 'titles: x'),
            'recall heading label': ('  # a #', '# a', '#  ', '\t## a ## ', '####### a'),
        }
        for name, values in cases.items():
            pair = PAIR[name]
            for value in values:
                with self.subTest(pair=name, value=value):
                    self.assertEqual(pair.new()(value), pair.oracle(value))


class TierBEquivalenceTests(unittest.TestCase):
    """Tier B: bounded-exhaustive equality with the former regexes."""

    def test_regex_whitespace_is_the_strip_predicate_on_every_code_point(self):
        # The helpers replace \s+ and \s* with lstrip()/rstrip()/strip(). For str
        # patterns re documents \s as str.isspace; this checks it, and that the
        # argument-free strips use the same test, over all 0x110000 code points.
        everything = ''.join(map(chr, range(0x110000)))
        by_regex = set(re.findall(r'\s', everything))
        self.assertEqual(by_regex, {c for c in everything if c.isspace()})
        self.assertEqual(by_regex, {c for c in everything if not c.strip()})
        self.assertEqual(by_regex, {c for c in everything if not c.lstrip()})
        self.assertEqual(by_regex, {c for c in everything if not c.rstrip()})
        self.assertTrue(set(WHITESPACE) <= by_regex)
        self.assertFalse({'#', 'a', '가', ':', '(', ')', '-', '—'} & by_regex)

    def test_every_helper_equals_its_oracle_on_all_short_strings(self):
        for pair in PAIRS:
            for alphabet, length in pair.runs:
                with self.subTest(pair=pair.name, alphabet=alphabet, length=length):
                    count, found = mismatches(pair, strings(alphabet, length, pair.prefixes))
                    expected_count = len(pair.prefixes) * sum(len(alphabet) ** n for n in range(length + 1))
                    self.assertEqual(count, expected_count)
                    self.assertEqual(found, [])

    def test_heading_forms_differ_once_a_line_feed_is_inside(self):
        # The heading forms differ from their oracles once a line feed is inside
        # ('.' stops there and '$' matches before it), so their callers must never
        # pass one; the tests below prove they cannot.
        for name in ('ssot ATX heading', 'lint heading text', 'rule heading', 'recall heading label',
                     'rule dash tail'):
            pair = PAIR[name]
            with self.subTest(pair=name):
                _, found = mismatches(pair, strings(' \n#a—-', 4), limit=1)
                self.assertTrue(found, name)


class CallerPreconditionTests(unittest.TestCase):
    """Callers pass only text without a line feed, and the helper equals the oracle there."""

    def notes(self):
        lines = [variant for line in LINES[:60] for variant in [line, *separated(line)]]
        return '\n'.join(lines) + '\n'

    def test_ledger_and_task_callers_split_bytes_into_line_feed_free_lines(self):
        text = self.notes()
        with recorded(self, PAIR['ssot ATX heading']):
            vault_ssot.parse_ledger(text)
        with recorded(self, PAIR['state ATX heading']):
            vault_state._items(text.encode('utf-8'), 'tasks')
        with recorded(self, PAIR['state ATX heading']):
            vault_state._items(tasks_note(['## Blocked\r', '# a\x85b', '## 보류\u2028x']), 'tasks')

    def test_lint_callers_pass_str_splitlines_lines(self):
        body = self.notes()
        front = '---\n' + ''.join(f'created: {line}\n' for line in LINES[:40]) + ''.join(
            f'updated:{mark}2026-10-27 # c\n' for mark in BOUNDARIES) + '---\n'
        notes = {'a.md': front + body + '[[b#alpha]]\n', 'b.md': body}
        for name in ('lint heading text', 'lint closing run', 'lint date field', 'lint comment split'):
            with self.subTest(pair=name), recorded(self, PAIR[name]):
                lint.analyze_notes(notes, {})

    def test_provenance_header_lines_come_from_str_splitlines(self):
        text = '---\n' + ''.join(f'key_{n}: {line}\n' for n, line in enumerate(LINES[:30])) + ''.join(
            f'k{mark}: v\n' for mark in BOUNDARIES) + '---\nbody\n'
        with recorded(self, PAIR['provenance header field']):
            vault_provenance._header_fields(text)
        with recorded(self, PAIR['provenance header field']):
            vault_provenance.analyze_notes({'20-knowledge/a.md': text}, {})

    def test_rule_callers_pass_str_splitlines_lines(self):
        body = self.notes() + '## Rule (note) — tail\n### Other - part (x)\n'
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            (vault / '.claude' / 'rules').mkdir(parents=True)
            (vault / 'CLAUDE.md').write_bytes(body.encode('utf-8'))
            (vault / '.claude' / 'rules' / 'r.md').write_bytes(body.encode('utf-8'))
            for name in ('rule heading', 'rule parenthesized note', 'rule dash tail'):
                with self.subTest(pair=name), recorded(self, PAIR[name]):
                    vault_healthcheck.scan_rules_integrity(vault, vault / '.claude' / 'rules',
                                                           vault / 'CLAUDE.md', 200)

    def test_recall_callers_pass_str_splitlines_lines(self):
        body = self.notes()
        front = '---\n' + ''.join(f'title{mark}: x\n' for mark in BOUNDARIES) + 'title: a  b\n---\n'
        # Lines that are not headings come first, so the title search visits them.
        plain = ''.join(f'#{mark} alpha\n#tag{mark}\n' for mark in BOUNDARIES)
        with recorded(self, PAIR['recall title value']) as calls:
            vault_recall._rank_document('n.md', front + body, 'alpha', ['alpha'])
        self.assertGreater(len(calls), len(BOUNDARIES))
        with recorded(self, PAIR['recall heading label']) as calls:
            vault_recall._rank_document('n.md', plain + body, 'alpha', ['alpha'])
        self.assertGreater(len(calls), len(BOUNDARIES))
        with recorded(self, PAIR['recall heading label']):
            vault_query_expansion.rank_expanded('n.md', plain + body + 'alpha body\n', 'alpha', ['alpha'])


class DefaultOutputUnchangedTests(unittest.TestCase):
    """Whole caller outputs equal the same runs with the former regexes patched in."""

    def same_with_oracles(self, names, run):
        current = run()
        with oracles(*names):
            former = run()
        self.assertEqual(current, former)
        return current

    def test_ledger_parse_and_check_are_unchanged(self):
        notes = [ledger_note(line) for line in LINES] + [ledger_note('# a') + ledger_note(line) for line in LINES[:80]]
        result = self.same_with_oracles(('ssot ATX heading',), lambda: [
            (vault_ssot.parse_ledger(text)['tables'][0]['section_status'],
             vault_ssot.check_ledger(text, path='notes/facts.md')) for text in notes])
        confirmed = sum(status == 'confirmed' for status, _ in result)
        self.assertTrue(0 < confirmed < len(result))

    def test_handoff_projection_dry_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            files = {
                '00-meta/vault-config.json': json.dumps({'handoff_note': 'notes/handoff.md'}).encode(),
                'notes/handoff.md': b'Narrative\n',
                'notes/log.md': b'- 2026-10-27 | Codex | [ops] fixture\n',
                'notes/tasks.md': tasks_note([*LINES[:120], '### Blocked ###', '## 차단', '# Other']),
            }
            for relative, data in files.items():
                (vault / relative).parent.mkdir(parents=True, exist_ok=True)
                (vault / relative).write_bytes(data)
            result = self.same_with_oracles(('state ATX heading',), lambda: vault_state.project_handoff(
                vault, 'notes/log.md', 'notes/tasks.md', 'notes/handoff.md'))
        states = {item['state'] for item in result['projection']['items']}
        self.assertEqual(states, {'blocked', 'pending'})

    def test_lint_warnings_are_unchanged(self):
        # Chunks keep each run under the 200-warning cap, so no finding is cut.
        headings = '\n'.join(LINES) + '\n'
        chunks = [LINES[start:start + 80] for start in range(0, len(LINES), 80)]
        runs = [{'a.md': '---\n' + ''.join(f'{key}: {line}\n' for line in chunk for key in ('created', 'updated'))
                 + '---\n' + ''.join(f'[[b#{line.strip(" #")}]]\n' for line in chunk), 'b.md': headings}
                for chunk in chunks]
        names = ('lint heading text', 'lint closing run', 'lint date field', 'lint comment split')
        result = self.same_with_oracles(names, lambda: [lint.analyze_notes(notes, {}) for notes in runs])
        codes = {item['code'] for findings in result for item in findings}
        self.assertTrue({'missing-anchor', 'invalid-date'} <= codes)
        self.assertNotIn('lint-incomplete', codes)
        self.assertTrue(any(item['code'] == 'invalid-date' and item['details']['field'] == 'updated'
                            for findings in result for item in findings))

    def test_provenance_header_fields_are_unchanged(self):
        texts = ['---\n' + ''.join(f'{key}: {line}\n' for key in ('provenance_manifest', 'provenance_sources', 'x_y'))
                 + '---\nbody\n' for line in LINES]
        result = self.same_with_oracles(('provenance header field',),
                                        lambda: [vault_provenance._header_fields(text) for text in texts])
        self.assertTrue(any(result))

    def test_rule_duplicate_headings_are_unchanged(self):
        claude = ''.join(f'{line}\n' for line in LINES) + '## Rule (note) — tail\n'
        rules = ''.join(f'{line}\n' for line in LINES[::2]) + '## rule — other\n'
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            (vault / '.claude' / 'rules').mkdir(parents=True)
            (vault / 'CLAUDE.md').write_text(claude, encoding='utf-8')
            (vault / '.claude' / 'rules' / 'r.md').write_text(rules, encoding='utf-8')
            result = self.same_with_oracles(
                ('rule heading', 'rule parenthesized note', 'rule dash tail'),
                lambda: vault_healthcheck.scan_rules_integrity(vault, vault / '.claude' / 'rules',
                                                               vault / 'CLAUDE.md', 10_000))
        self.assertTrue(result[1])

    def test_recall_titles_and_expanded_ranking_are_unchanged(self):
        documents = ([f'{line}\nalpha body\n' for line in LINES]
                     + [f'---\ntitle: {line}\n---\nalpha body\n' for line in LINES[:150]])
        names = ('recall title value', 'recall heading label')
        result = self.same_with_oracles(names, lambda: [
            (vault_recall._rank_document('n.md', text, 'alpha', ['alpha']),
             vault_query_expansion.rank_expanded('n.md', text, 'alpha', ['alpha'])) for text in documents])
        self.assertTrue(all(ranked is not None for ranked, _ in result))

    def test_warning_promotion_replay_is_unchanged(self):
        # The certificate binds the bundled benign corpus results; only the
        # checker file hash may change with this work.
        names = ('ssot ATX heading', 'lint heading text', 'lint closing run', 'lint date field',
                 'lint comment split', 'provenance header field')
        result = self.same_with_oracles(names, vault_warning_policy.build_promotion_certificate)
        self.assertEqual(result['false_positives'], 0)


TIMING_SIZES = (8_000, 16_000, 32_000, 64_000)
TIMING_BUDGET = 0.25  # seconds for every shape and size of one helper (measured: under 2 ms)
SHAPES = {
    'ssot ATX heading': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# ' + ' ' * n,
                         lambda n: '# a' + ' #' * n, lambda n: '#' + '\u3000' * n + 'x'),
    'lint heading text': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# ' + ' ' * n,
                          lambda n: '#' + '\u3000' * n + 'x'),
    'lint closing run': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + ' ' * n + '#b',
                         lambda n: 'a' + ' #' * n),
    'lint date field': (lambda n: 'created: a' + ' ' * n + 'b', lambda n: 'updated:' + ' ' * n,
                        lambda n: 'created: a' + '\u3000' * n + 'b'),
    'lint comment split': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + '\t' * n + 'x#'),
    'provenance header field': (lambda n: 'key: a' + ' ' * n + 'b', lambda n: 'key:' + ' ' * n,
                                lambda n: 'k' * n + ' ' * n + 'x'),
    'rule heading': (lambda n: '# a' + ' ' * n + 'b', lambda n: '### ' + ' ' * n,
                     lambda n: '#' + '\u3000' * n + 'x'),
    'rule parenthesized note': (lambda n: 'a' + ' ' * n + 'b', lambda n: '(' * n,
                                lambda n: 'a' + ' ' * n + '(b'),
    'rule dash tail': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + '\u3000' * n + '-x',
                       lambda n: '-' * n),
    'recall title value': (lambda n: 'title: a' + ' ' * n + 'b', lambda n: 'title:' + ' ' * n,
                           lambda n: ' ' * n + 'title: x'),
    'recall heading label': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# a' + ' #' * n,
                             lambda n: '#' + '\u3000' * n + 'x', lambda n: ' ' * n + '# x'),
}
SHAPES['state ATX heading'] = SHAPES['ssot ATX heading']


class TimingGateTests(unittest.TestCase):
    """Free from the former super-linear cost; only the new helpers are timed."""

    def elapsed(self, function, shapes):
        inputs = [shape(size) for size in TIMING_SIZES for shape in shapes]
        started = time.perf_counter()
        for value in inputs:
            function(value)
            if time.perf_counter() - started > TIMING_BUDGET:
                break  # an old, super-linear form fails here instead of running on
        return time.perf_counter() - started

    def test_every_helper_stays_under_the_budget_up_to_64k(self):
        self.assertEqual(set(SHAPES), set(PAIR))
        for pair in PAIRS:
            with self.subTest(pair=pair.name):
                self.assertLess(self.elapsed(pair.new(), SHAPES[pair.name]), TIMING_BUDGET)


class NoLazySuffixLeftTests(unittest.TestCase):
    def test_no_shipped_regex_keeps_a_lazy_group_before_an_optional_whitespace_tail(self):
        # Docstrings and comments may quote the former patterns; code may not.
        shape = re.compile(r'\?\)(?:#\*)*\\s\*(?:\\s\*|#\*)*\$')
        found = []
        for directory in (SCRIPTS, ROOT / 'hooks'):
            for path in sorted(directory.glob('*.py')):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                              if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                              and node.body and isinstance(node.body[0], ast.Expr)
                              and isinstance(node.body[0].value, ast.Constant)}
                found.extend(f'{path.name}:{node.lineno}' for node in ast.walk(tree)
                             if isinstance(node, ast.Constant) and isinstance(node.value, str)
                             and id(node) not in docstrings and shape.search(node.value))
        self.assertEqual(found, [])


@unittest.skipUnless(SLOW, 'set AGENTIC_VAULT_SLOW_TESTS=1 for the larger runs')
class SlowEquivalenceTests(unittest.TestCase):
    def test_every_whitespace_code_point_at_a_shorter_length(self):
        spaces = ''.join(c for c in map(chr, range(0x110000)) if c.isspace())
        for pair in PAIRS:
            alphabet = (spaces if not pair.newline_free else spaces.replace('\n', '')) + pair.special
            length = 4 if pair.prefixes == ('',) else 3
            with self.subTest(pair=pair.name):
                _, found = mismatches(pair, strings(alphabet, length, pair.prefixes))
                self.assertEqual(found, [])

    def test_each_default_alphabet_one_symbol_longer(self):
        for pair in PAIRS:
            alphabet, length = pair.runs[0]
            with self.subTest(pair=pair.name):
                _, found = mismatches(pair, strings(alphabet, length + 1, pair.prefixes))
                self.assertEqual(found, [])

    def test_report_former_and_new_times(self):
        report = io.StringIO()
        sizes = {'recall heading label': (1_000, 2_000)}  # the former form was cubic
        for pair in PAIRS:
            if pair.name == 'state ATX heading':
                continue
            shape = SHAPES[pair.name][0]
            for size in sizes.get(pair.name, (8_000, 16_000, 32_000, 64_000) if 'ATX' in pair.name
                                  else (8_000, 16_000, 32_000)):
                value = shape(size)
                started = time.perf_counter()
                pair.oracle(value)
                former = time.perf_counter() - started
                started = time.perf_counter()
                pair.new()(value)
                new = time.perf_counter() - started
                print(f'{pair.name:26} N={size:>6}  former {former:9.4f} s  new {new:.6f} s', file=report)
                self.assertLess(new, TIMING_BUDGET)
        sys.stderr.write('\n' + report.getvalue())


if __name__ == '__main__':
    unittest.main()
