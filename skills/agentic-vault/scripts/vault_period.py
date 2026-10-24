#!/usr/bin/env python3
"""Opt-in Korean period expressions as half-open date intervals [start, end).

parse(text, fiscal_start=None) reads a fixed expression table and returns one
record per recognised span, in text order: {span, kind, label_edtf, start, end,
reason}. start and end are datetime.date values on a half-open interval, the
semantics vault_ssot.check_ledger and vault_assembly._period use for validity.
A record with a reason abstains and has no label, start or end. This module
reads no files and never feeds ledger validity cells, which stay strict
YYYY-MM-DD; vault_ssot check-support --normalize-periods is its caller.

Conventions declared here, not taken from a standard:
- Quarters and halves follow the calendar year: 1분기 = [YYYY-01-01,
  YYYY-04-01), 하반기 = [YYYY-07-01, YYYY+1-01-01), and N분기말/반기말 is the
  last day. The Library of Congress EDTF page (Level 2 sub-year groupings)
  lists codes 33-36 (quarters) and 40-41 (semestrals) with their durations
  only, not their months. EDTF strings are report labels (label_edtf);
  comparisons use the date intervals.
- A fiscal year (2026 회계연도, FY2026) is an interval only with a declared
  first month MM: [YYYY-MM-01, YYYY+1-MM-01), named after the year it starts
  in and labelled as a month interval (2026-04/2027-03), since EDTF has no
  fiscal-year code. A quarter or half written after a fiscal year counts from
  that month. Undeclared, it abstains with fiscal_start_undeclared.
- With a first month other than January, a quarter or half of a calendar year
  abstains with quarter_basis_ambiguous: the text may mean either basis.
- Two-digit years (3Q26, FY26) are 2000-2099.
- Relative expressions (당기, 전기, 전년 동기, 올해, 이번 분기, ...) always
  abstain with relative_needs_anchor. A quarter or half without a year
  abstains with no_year; no anchor year is filled in, because a text such as
  2025년 1분기와 3분기 elides the year of the second quarter.
- Durations (12개월, 1년, P1Y) are not periods and are never merged, and
  minutes (3분 지연) never become a quarter. A range is not merged into one
  interval: each end is its own period, and a list or range of quarters or
  halves (1~3분기, 상·하반기) abstains with multiple_periods.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta
import re

MAX_TEXT_CHARS = 1024 * 1024
# Relations from strongest to weakest; match() reports the weakest one.
MATCHES = ('equal', 'contained', 'none')
SCOPE = ('ledger value periods against cited note body periods as half-open date intervals: equal, '
         'contained (a body period inside the value period) or none, and abstain when the value has an '
         'unresolved period; quarters and halves follow the calendar year and abstain unless fiscal_start '
         'is 1 or absent; fiscal years need fiscal_start; relative and yearless expressions abstain; '
         'durations and ranges are never merged; advisory only, never literal support, counts or warnings')


class PeriodError(ValueError):
    """A content-free operational error code."""


_SP = r'[ \t]?'
_EDGE = r'(?<![0-9A-Za-z])'
_YEAR = _EDGE + r'(?P<year>[1-9][0-9]{3})(?:' + _SP + r'년도?' + _SP + r'|[ \t])'
_QUARTER = r'(?:제' + _SP + r')?(?P<quarter>[1-4])(?:' + _SP + r'/' + _SP + r'4)?' + _SP + r'분기'
_HALF = r'(?P<half>[상하])' + _SP + r'반기'
# 3분 기다렸다 or 3분 기록 written without the space are minutes, not 3분기.
_MINUTES = r'(?!다[리려렸릴림]|록)'
# 분기말/반기말, or a spaced 말 that ends the word, is the last day.
_END = r'(?P<end>말|[ \t]말(?![가-힣]))?'
_SEPARATORS = (r'~\N{TILDE OPERATOR}\N{WAVE DASH}\-,\N{MIDDLE DOT}\N{HANGUL LETTER ARAEA}'
               r'\N{HANGUL JUNGSEONG ARAEA}\N{KATAKANA MIDDLE DOT}')
_NUMBER = r'(?:제' + _SP + r')?[1-4](?:' + _SP + r'/' + _SP + r'4)?'
_FISCAL = (r'(?:' + _EDGE + r"[Ff][Yy]" + _SP + r"['\N{RIGHT SINGLE QUOTATION MARK}]?"
           r'(?P<short>[0-9]{2}(?:[0-9]{2})?)(?![0-9])'
           r'|' + _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'(?:년' + _SP + r')?(?:회계|사업)' + _SP + r'[연년]도)')

# Fixed table. Scanning takes the leftmost match; at one position the earlier
# row wins, so a list, a relative word or a fiscal year is read before the
# quarter or half inside it.
_TABLE = (
    ('multiple', r'(?:' + _YEAR + r'|' + _EDGE + r')' + _NUMBER + _SP + r'[' + _SEPARATORS + r']' + _SP
     + _NUMBER + _SP + r'분기' + _MINUTES),
    ('multiple', r'(?:' + _YEAR + r'|(?<![가-힣]))상' + _SP + r'(?:[/' + _SEPARATORS + r']' + _SP + r')?하'
     + _SP + r'반기'),
    ('relative', r'(?<![가-힣])(?:전년' + _SP + r'동기|전년도?|금년도?|올해|작년|내년|당해' + _SP + r'[연년]도'
     r'|(?:이번|지난|다음|직전)' + _SP + r'(?:분기|반기|연도|해)|(?:전|당|금)' + _SP + r'분기'
     r'|(?:당기|전기)(?![가-힣]))'),
    ('fiscal', _FISCAL + r'(?:' + _SP + r'(?:(?:' + _QUARTER + _MINUTES + r'|' + _HALF + r')' + _END
     + r'|[Qq](?P<q>[1-4])(?![0-9A-Za-z])))?'),
    ('quarter', _YEAR + _QUARTER + _MINUTES + _END),
    ('quarter', _YEAR + r'(?P<quarter>[1-4])[Qq](?![0-9A-Za-z])'),
    ('quarter', _EDGE + r'(?P<quarter>[1-4])[Qq](?P<short>[0-9]{2}(?:[0-9]{2})?)?(?![0-9A-Za-z])'),
    ('quarter', _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'-?' + _SP + r'[Qq](?P<quarter>[1-4])(?![0-9A-Za-z])'),
    ('half', _YEAR + _HALF + _END),
    ('day', _EDGE + r'(?P<year>[1-9][0-9]{3})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})(?![0-9])'),
    ('day', r'(?<![0-9A-Za-z.])(?P<year>[1-9][0-9]{3})\.' + _SP + r'(?P<month>[0-9]{1,2})\.' + _SP
     + r'(?P<day>[0-9]{1,2})(?![0-9])(?!\.[0-9])'),
    ('day', _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'년' + _SP + r'(?P<month>[0-9]{1,2})' + _SP + r'월'
     + _SP + r'(?P<day>[0-9]{1,2})' + _SP + r'일'),
    ('quarter', _EDGE + _QUARTER + _MINUTES + _END),
    ('half', r'(?<![가-힣])' + _HALF + _END),
)
_PATTERNS = tuple((kind, re.compile(pattern)) for kind, pattern in _TABLE)


def _scan(text):
    """Leftmost non-overlapping matches; a pattern searches again only once its pending match is passed."""
    pending, position = [None] * len(_PATTERNS), 0
    while True:
        best = None
        for index, (kind, pattern) in enumerate(_PATTERNS):
            found = pending[index]
            if found is not False and (found is None or found.start() < position):
                found = pending[index] = pattern.search(text, position) or False
            if found and (best is None or found.start() < best[1].start()):
                best = (kind, found)
        if best is None:
            return
        yield best
        position = best[1].end()


def _month(first, months):
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _kind(kind, groups):
    if kind != 'fiscal':
        return kind
    return 'fiscal_quarter' if groups.get('quarter') or groups.get('q') else (
        'fiscal_half' if groups.get('half') else 'fiscal_year')


def _resolve(kind, groups, fiscal_start):
    """Return (kind, reason, label, start, end) for one match of a _kind() kind."""
    if kind == 'multiple':
        return kind, 'multiple_periods', None, None, None
    if kind == 'relative':
        return kind, 'relative_needs_anchor', None, None, None
    short = groups.get('short')
    year = int(groups['year']) if groups.get('year') else (
        None if not short else int(short) if len(short) == 4 else 2000 + int(short))
    if kind == 'day':
        start = date(year, int(groups['month']), int(groups['day']))
        return kind, None, start.isoformat(), start, start + timedelta(days=1)
    quarter, half = groups.get('quarter') or groups.get('q'), groups.get('half')
    months = 3 if quarter else 6 if half else 12
    number = int(quarter) if quarter else (1 if half == '상' else 2) if half else 1
    if kind.startswith('fiscal_'):
        if fiscal_start is None:
            return kind, 'fiscal_start_undeclared', None, None, None
        start = _month(date(year, fiscal_start, 1), months * (number - 1))
        end = _month(start, months)
        last = _month(start, months - 1)
        label = f'{start.year:04d}-{start.month:02d}/{last.year:04d}-{last.month:02d}'
    else:
        if year is None:
            return kind, 'no_year', None, None, None
        if fiscal_start not in (None, 1):
            return kind, 'quarter_basis_ambiguous', None, None, None
        start = _month(date(year, 1, 1), months * (number - 1))
        end = _month(start, months)
        label = f'{year:04d}-{(32 if quarter else 39) + number}'
    if groups.get('end'):
        start = end - timedelta(days=1)
        return 'period_end', None, start.isoformat(), start, end
    return kind, None, label, start, end


def parse(text, *, fiscal_start=None):
    """Return the period records of text in order (see the module docstring).

    Each record is {span: (start, end) character offsets, kind, label_edtf,
    start, end, reason}. Kinds: day, quarter, half, period_end, fiscal_year,
    fiscal_quarter, fiscal_half, relative, multiple. Reasons: no_year,
    fiscal_start_undeclared, quarter_basis_ambiguous, relative_needs_anchor,
    multiple_periods, invalid_date. Digits are ASCII; check-support passes
    NFKC/casefold forms, so full-width digits and FY/fy also match there.
    """
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise PeriodError('invalid_text')
    if fiscal_start is not None and (type(fiscal_start) is not int or not 1 <= fiscal_start <= 12):
        raise PeriodError('invalid_fiscal_start')
    records = []
    for kind, found in _scan(text):
        groups = found.groupdict()
        kind = _kind(kind, groups)
        try:
            kind, reason, label, start, end = _resolve(kind, groups, fiscal_start)
        except (ValueError, OverflowError):
            reason, label, start, end = 'invalid_date', None, None, None
        records.append(dict(span=found.span(), kind=kind, label_edtf=label, start=start, end=end, reason=reason))
    return records


def index(records):
    """Sorted distinct resolved intervals of records, for match()."""
    return tuple(sorted({(record['start'], record['end']) for record in records if record['reason'] is None}))


def _relation(interval, indexed):
    """0: the same interval is indexed, 1: an indexed interval lies inside it, 2: neither."""
    start, end = interval
    position = bisect_left(indexed, interval)
    if position < len(indexed) and indexed[position] == interval:
        return 0
    position = bisect_left(indexed, (start,))
    while position < len(indexed) and indexed[position][0] < end:
        if indexed[position][1] <= end:
            return 1
        position += 1
    return 2


def match(value_records, indexes):
    """Classify one value's periods against {key: index(records)} of cited bodies.

    None when the value names no period. A value with an abstaining record
    abstains with the first reason. Otherwise each value interval takes its
    strongest relation over all bodies, and the value takes the weakest of
    those: equal (a body names the same interval), contained (a body names an
    interval inside it) or none (a wider or overlapping body interval is none).
    via lists the keys with an equal relation, plus contained ones when the
    result is contained.
    """
    if not value_records:
        return None
    for record in value_records:
        if record['reason'] is not None:
            return dict(normalized_match='abstain', reason=record['reason'])
    worst, keys = 0, (set(), set())
    for interval in sorted({(record['start'], record['end']) for record in value_records}):
        best = 2
        for key, indexed in indexes.items():
            relation = _relation(interval, indexed)
            if relation < 2:
                keys[relation].add(key)
            best = min(best, relation)
        worst = max(worst, best)
    result = dict(normalized_match=MATCHES[worst])
    if worst < 2:
        result['via'] = sorted(keys[0] | keys[1] if worst else keys[0])
    return result
