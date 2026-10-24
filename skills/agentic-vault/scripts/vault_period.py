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
  fiscal-year code. A quarter or half written after a fiscal year (FY2026 Q3,
  FY2026 3분기) counts from that month. Undeclared, it abstains with
  fiscal_start_undeclared.
- With a first month other than January, a quarter or half of a calendar year
  abstains with quarter_basis_ambiguous: the text may mean either basis.
- Two-digit years (3Q26, FY26) are 2000-2099, and a four-digit year never
  starts with 0. A year written without 년 before a quarter or half
  (2026 3분기) is read only for 1900-2099.
- Qualifiers are recognised from fixed lists only. 말 right after a period,
  attached or spaced, is its last day when no letter or digit follows it or
  what follows starts with a listed particle (에, 까지, 부터, 의, 로, 경,
  쯤, 께, 일, 기준, 현재, 은, 는, 이, 가, 도, 엔). These abstain with
  period_qualifier instead of falling back to the whole period: 초 (start of
  period, so also 초반 and 초순), any other 말 word (말레이시아), a narrowing
  word (후반, 중후반, 중반, 전반 but not 전반적, 끝, 마지막, 막바지, 막판,
  상순, 중순, 하순; 시작 as a word of its own; 종료, 마감, 결산, 개시 or 시작
  before 일, 날, 시 or 월; an ordinal before a day, week or month unit, as in
  첫 달, 첫날, 첫 2주, 셋째 주, 두 번째 달; a month or week number such as
  8월 or 2주차), any of these after the period's 의 (2026년 3분기의
  마지막 날, 2026년 3분기의 말), a quarter count (4분기 연속, 3분기째,
  3분기 만에) and a Q or H mark before a fiscal year (Q3 FY2026, H1 FY2026)
  or after it in any form but Qn (FY2026 H1, FY2026 3Q); FY2026 Q3 is read.
  A word outside these lists is not recognised, so the value is still
  compared as the whole period (2026년 3분기 늦게, 2026년 3분기 중 마지막
  달, late 2026Q3); 중, 내 and 동안 keep it whole as well.
- Relative expressions (당기, 전기, 전년 동기, 올해, 이번 분기, ...) always
  abstain with relative_needs_anchor. A quarter or half without a year
  abstains with no_year; no anchor year is filled in, because a text such as
  2025년 1분기와 3분기 elides the year of the second quarter.
- Durations (12개월, 1년, P1Y) are not periods and are never merged, and
  minutes (3분 지연) never become a quarter. A range is not merged into one
  interval: each end is its own period, and a list or range of quarters or
  halves (1~3분기, 상·하반기) abstains with multiple_periods. When a resolved
  period and a number that no row reads are joined by a range mark (~ 〜 ∼ ～
  - – — or 부터), as in 2026.10.07~10.09 or 10.07~2026.10.09, a partial_range
  record is added beside it, so the value is never compared by one end; no
  year is copied from the other end.
- The table reads one space or tab where it allows a gap, while the checks
  that make a record abstain (qualifiers, counts, range marks) take any
  whitespace run; a Q or H mark beside a fiscal year is read across one space
  or tab only. vault_ssot passes forms with every whitespace run collapsed to
  one space.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta
import re

MAX_TEXT_CHARS = 1024 * 1024
# Relations from strongest to weakest; match() reports the weakest one.
MATCHES = ('equal', 'contained', 'none')
SCOPE = ('ledger value periods against cited note body periods as half-open date intervals: equal (the same '
         'interval appears somewhere in a cited body), contained (an inner one does) or none, and abstain when '
         'the value has an unresolved period, a listed qualifier or a partial range; text outside the fixed '
         'lists, such as an unlisted qualifier, is not read, so a period can still be compared whole; modifiers '
         'and the rest of the value are not compared; calendar quarters and halves abstain unless fiscal_start '
         'is 1 or absent; fiscal years need fiscal_start; relative and yearless expressions abstain; durations '
         'and ranges are never merged; advisory only, never literal support, counts or warnings')


class PeriodError(ValueError):
    """A content-free operational error code."""


_SP = r'[ \t]?'
# Checks that make a record abstain take any whitespace run: spacing never widens a value.
_GAP = r'\s*'
_EDGE = r'(?<![0-9A-Za-z])'
# 2026년, 2026년도, or a year of 1900-2099 and one space (1500 3분기 is a number).
_YEAR = (_EDGE + r'(?P<year>[1-9][0-9]{3})(?:' + _SP + r'년도?' + _SP
         + r'|(?<=(?:19|20)[0-9]{2})[ \t])')
# 3분 기다렸다 or 3분 기록 written without the space are minutes, not 3분기.
_MINUTES = r'(?!다[리려렸릴림]|록)'
# 4분기 연속, 3분기째 and 3분기 만에 count quarters: a qualifier, not quarter N.
_QUARTER = (r'(?:제' + _SP + r')?(?P<quarter>[1-4])(?:' + _SP + r'/' + _SP + r'4)?' + _SP + r'분기' + _MINUTES
            + r'(?:' + _GAP + r'(?P<count>연속|째|만에))?')
_HALF = r'(?P<half>[상하])' + _SP + r'반기'
# After a period, attached or spaced: 말 is the last day when no letter or
# digit follows it or a listed particle does. 초 (start of period, not
# computed), another 말 word (말레이시아) and a narrowing word are qualifiers,
# and so are all of them after the period's 의 (3분기의 말, 3분기의 첫 달).
_PARTICLE = r'(?:에|까지|부터|의|로|경|쯤|께|일|기준|현재|은|는|이|가|도|엔)'
# A word of its own: no letter or digit follows it, or a particle does.
_WORD_END = r'(?:(?![^\W_])|(?=' + _PARTICLE + r'|과|와|을|를|으로|만))'
# The n-th day, week or month of a period (첫 달, 첫날, 첫째 주, 첫 2주, 셋째
# 주, 두 번째 달, 첫 영업일); 첫 흑자 or 첫 출하 names an event, not a part.
_ORDINAL = (r'(?:첫째|첫|[둘셋넷]째|[첫두세네]' + _GAP + r'번째)' + _GAP
            + r'(?:(?:[0-9]+|[한두세네](?=' + _GAP + r'[달주]))' + _GAP + r')?'
            r'(?:[날달주일]|개월|영업일|거래일|근무일|평일|이틀|사흘|나흘|열흘|보름|[월화수목금토일]요일)(?:간|차|말)?'
            + _WORD_END)
# Fixed words that narrow a period to a part or a day; what follows them is
# not read (후반에, 끝까지, 마지막 날). A narrower form keeps common neutral uses
# whole: 시작 only as a word of its own (not 시작하는, 시작된), 종료/마감/결산/
# 개시/시작 only before 일, 날, 시 or 월 (종료일, 마감 시한, 결산월; not 결산
# 실적), an ordinal only before a unit and 전반 not as 전반적. A month (8월)
# or week number (2주차) inside the period narrows it too. 초반 and 초순 are 초.
# 중, 내 and 동안 do not narrow a period, and 이후 or 까지 are modifiers: none
# is listed.
_NARROW = (r'(?:중?후반|중반|전반(?!적)|끝|마지막|막바지|막판|[상중하]순'
           r'|(?:종료|마감|결산|개시|시작)' + _GAP + r'[일날시월]|시작' + _WORD_END + r'|' + _ORDINAL
           + r'|(?:1[0-2]|0?[1-9])' + _GAP + r'월|[0-9]+' + _GAP + r'주차)')
_TAIL = (r'(?:' + _GAP + r'(?:(?P<end>말)(?:(?![^\W_])|(?=' + _PARTICLE + r'))|(?P<qualifier>(?:의' + _GAP
         + r')?(?:[말초]|' + _NARROW + r'))))?')
_SEPARATORS = (r'~\N{TILDE OPERATOR}\N{WAVE DASH}\-,\N{MIDDLE DOT}\N{HANGUL LETTER ARAEA}'
               r'\N{HANGUL JUNGSEONG ARAEA}\N{KATAKANA MIDDLE DOT}')
_NUMBER = r'(?:제' + _SP + r')?[1-4](?:' + _SP + r'/' + _SP + r'4)?'
_FISCAL = (r'(?:' + _EDGE + r"[Ff][Yy]" + _SP + r"['\N{RIGHT SINGLE QUOTATION MARK}]?"
           r'(?P<short>[1-9][0-9]{3}|[0-9]{2})(?![0-9])'
           r'|' + _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'(?:년' + _SP + r')?(?:회계|사업)' + _SP + r'[연년]도)')
# A range mark between a resolved period and a number that no row reads; a
# day may carry a weekday in parentheses (2026.10.07(수)~10.09(금)).
_RANGE = r'(?:[~\-\N{TILDE OPERATOR}\N{WAVE DASH}\N{FULLWIDTH TILDE}\N{EN DASH}\N{EM DASH}]|부터)'
_WEEKDAY = r'(?:' + _GAP + r'\([월화수목금토일]\))?'
_RANGE_AFTER = re.compile(r'\.?' + _WEEKDAY + _GAP + _RANGE + _GAP + r'(?:[0-9]+|[QqHh][0-9])')
_RANGE_BEFORE = re.compile(r'[0-9][.일]?' + _WEEKDAY + _GAP + _RANGE + _GAP + r'\Z')

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
    # Q3 FY2026, 1H FY2026: a mark before the fiscal year is not computed.
    ('fiscal', _EDGE + r'(?P<marker>[QqHh][1-4]|[1-4][QqHh])' + _SP + _FISCAL),
    ('fiscal', _FISCAL + r'(?:' + _SP + r'(?:' + _QUARTER + r'|' + _HALF + r'|[Qq](?P<q>[1-4])(?![0-9A-Za-z])'
     r'|(?P<marker>[Hh][1-4]|[1-4][QqHh])(?![0-9A-Za-z])))?' + _TAIL),
    ('quarter', _YEAR + _QUARTER + _TAIL),
    ('quarter', _YEAR + r'(?P<quarter>[1-4])[Qq](?![0-9A-Za-z])' + _TAIL),
    ('quarter', _EDGE + r'(?P<quarter>[1-4])[Qq](?P<short>[1-9][0-9]{3}|[0-9]{2})?(?![0-9A-Za-z])' + _TAIL),
    ('quarter', _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'-?' + _SP + r'[Qq](?P<quarter>[1-4])(?![0-9A-Za-z])'
     + _TAIL),
    ('half', _YEAR + _HALF + _TAIL),
    ('day', _EDGE + r'(?P<year>[1-9][0-9]{3})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})(?![0-9])'),
    ('day', r'(?<![0-9A-Za-z.])(?P<year>[1-9][0-9]{3})\.' + _SP + r'(?P<month>[0-9]{1,2})\.' + _SP
     + r'(?P<day>[0-9]{1,2})(?![0-9])(?!\.[0-9])'),
    ('day', _EDGE + r'(?P<year>[1-9][0-9]{3})' + _SP + r'년' + _SP + r'(?P<month>[0-9]{1,2})' + _SP + r'월'
     + _SP + r'(?P<day>[0-9]{1,2})' + _SP + r'일'),
    ('quarter', _EDGE + _QUARTER + _TAIL),
    ('half', r'(?<![가-힣])' + _HALF + _TAIL),
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
    marker = (groups.get('marker') or '').casefold()
    if groups.get('quarter') or groups.get('q') or 'q' in marker:
        return 'fiscal_quarter'
    return 'fiscal_half' if groups.get('half') or 'h' in marker else 'fiscal_year'


def _resolve(kind, groups, fiscal_start):
    """Return (kind, reason, label, start, end) for one match of a _kind() kind."""
    if kind == 'multiple':
        return kind, 'multiple_periods', None, None, None
    if kind == 'relative':
        return kind, 'relative_needs_anchor', None, None, None
    if groups.get('qualifier') or groups.get('count') or groups.get('marker'):
        return kind, 'period_qualifier', None, None, None
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


def _partial(span):
    return dict(span=span, kind='range', label_edtf=None, start=None, end=None, reason='partial_range')


def _ranges(text, records):
    """Add a partial_range record beside a resolved record joined by a range mark to a number no row read.

    The number is outside every record span: a full range (2026.10.07~2026.10.09)
    has a record at both ends and gets none.
    """
    result, cursor = [], 0
    for position, record in enumerate(records):
        start, end = record['span']
        resolved = record['reason'] is None
        before = _RANGE_BEFORE.search(text, cursor, start) if resolved else None
        if before:
            first = before.start()
            while first > cursor and '0' <= text[first - 1] <= '9':
                first -= 1
            result.append(_partial((first, start)))
        result.append(record)
        cursor = end
        limit = records[position + 1]['span'][0] if position + 1 < len(records) else len(text)
        after = _RANGE_AFTER.match(text, end, limit) if resolved else None
        if after:
            result.append(_partial(after.span()))
            cursor = after.end()
    return result


def parse(text, *, fiscal_start=None):
    """Return the period records of text in order (see the module docstring).

    Each record is {span: (start, end) character offsets, kind, label_edtf,
    start, end, reason}. Kinds: day, quarter, half, period_end, fiscal_year,
    fiscal_quarter, fiscal_half, relative, multiple, range. Reasons: no_year,
    fiscal_start_undeclared, quarter_basis_ambiguous, relative_needs_anchor,
    multiple_periods, period_qualifier, partial_range, invalid_date. A range
    record spans the range mark and the number beside it. Digits are ASCII;
    check-support passes NFKC/casefold forms, so full-width digits and FY/fy
    also match there.
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
    return _ranges(text, records)


def index(records):
    """Sorted distinct resolved intervals of records, for match()."""
    return tuple(sorted({(record['start'], record['end']) for record in records if record['reason'] is None}))


def index_of(texts, *, fiscal_start=None):
    """index() of the records of every text, or None when one is longer than MAX_TEXT_CHARS.

    vault_ssot then leaves that cited body out of the comparison instead of
    failing: NFKC can expand a 256 KiB note past the limit.
    """
    if any(isinstance(text, str) and len(text) > MAX_TEXT_CHARS for text in texts):
        return None
    return index([record for text in texts for record in parse(text, fiscal_start=fiscal_start)])


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


def reaches(value_records, indexed, normalized_match):
    """True when a resolved value interval has normalized_match or a stronger relation to indexed.

    For the equal or contained result of match(), a key is in via exactly
    when this holds for its index, so a narrower text of the same body (a
    cited ledger without its fact tables) tells whether that key still is.
    """
    limit = MATCHES.index(normalized_match)
    return any(_relation((record['start'], record['end']), indexed) <= limit
               for record in value_records if record['reason'] is None)
