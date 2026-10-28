#!/usr/bin/env python3
"""Bounded temporal Markdown ledgers and explicit revision-bound v2 migration.

Intervals are half-open dates [valid_from, valid_until); blank upper bounds are
open, but missing/invalid lower bounds are unknown rather than evidence of a
contradiction. No dates, source authority, truth or supersession are inferred.
Legacy headers are aliases only; original fact cells and unrelated bytes remain
intact during migration. JSON proposals/receipts are accounting, not signatures
or evidence of approval. Only an explicit approve=True applies a migration.
check_support (CLI check-support) is a separate, warning-only observation: a
row value absent from its cited note body, or a citation that does not resolve
to exactly one allowed note, is reported. Literal presence is not truth.
A value found only inside other cited ledgers' fact tables is listed as
ledger_table_only; it counts as unsupported only with --primary-text-only,
which also names cited notes whose fact tables could not be separated.
Opt-in --normalize-periods [--fiscal-start MM] adds a separate per-row
normalized_match from date/quarter/half/fiscal-year intervals (vault_period);
it never changes the literal result. Validity cells stay strict YYYY-MM-DD.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import unicodedata

import vault_state as state
from vault_evidence import EvidenceError, decode, regular, stable_read, stamp
from vault_paths import relative_parts, resolve_note_path, unsafe_path_segment, zone_matches

MAX_BYTES = 256 * 1024
MAX_PROPOSAL_BYTES = 1024 * 1024
MAX_TABLES = 8
MAX_ROWS = 256
MAX_COLUMNS = 32
MAX_CELL = 4096
FIELDS = ('subject', 'relation', 'value', 'valid_from', 'valid_until', 'recorded', 'source', 'superseded_by')
IDENTITY = frozenset(('subject', 'relation', 'value'))
ALIASES = {
    'subject': ('subject', 'entity', '주체', '대상', '주어'),
    'relation': ('relation', 'predicate', 'property', '항목', '관계', '속성'),
    'value': ('value', 'fact', '값', '사실', '내용'),
    'valid_from': ('valid_from', 'from', 'effective_from', '시작일', '유효 시작', '유효시작', '유효시작일'),
    'valid_until': ('valid_until', 'valid_to', 'until', 'effective_until', '종료일', '유효 종료', '유효종료', '유효종료일'),
    'recorded': ('recorded', 'recorded_at', 'observed_at', '기록일', '기록시각', '관측일'),
    'source': ('source', 'evidence', '출처', '근거'),
    'superseded_by': ('superseded_by', 'superseded', '대체 항목', '대체항목', '후속항목', '대체됨'),
    'status': ('status', 'state', '상태', '확정여부'),
}
_ALIAS = {alias.casefold(): field for field, aliases in ALIASES.items() for alias in aliases}
CONFIRMED = frozenset(('confirmed', '확정', '✅확정'))
REJECTED = frozenset(('rejected', 'excluded', '기각', '배제', '🚫배제'))
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
DIVIDER_RE = re.compile(r':?-{3,}:?')
# check_support bounds. A cited note keeps the ledger byte limit (MAX_BYTES).
MAX_SUPPORT_NOTES = 128
MAX_SUPPORT_BYTES = 8 * 1024 * 1024
MAX_REFERENCES = 8
MAX_INVENTORY_ENTRIES = 20_000
MAX_CANDIDATES = 16
MAX_REFERENCE_CHARS = 256
WIKI_REF_RE = re.compile(r'!?\[\[([^\[\]\n]{0,512})\]\]')
ATTACHMENT_RE = re.compile(r'\.[A-Za-z][A-Za-z0-9]{0,7}$')
_ASCII_ALNUM = frozenset('0123456789abcdefghijklmnopqrstuvwxyz')


class SSOTError(Exception):
    """A content-free operational error code."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _cells(line):
    """Split real delimiters, preserving cell spans and escaped/wiki/code pipes."""
    delimiters, escaped, wiki, ticks = [], False, 0, 0
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == '\\':
            escaped = True
            index += 1
            continue
        if char == '`':
            end = index
            while end < len(line) and line[end] == '`':
                end += 1
            count = end - index
            if not ticks:
                ticks = count
            elif ticks == count:
                ticks = 0
            index = end
            continue
        if not ticks and line.startswith('[[', index):
            wiki += 1
            index += 2
            continue
        if not ticks and wiki and line.startswith(']]', index):
            wiki -= 1
            index += 2
            continue
        if char == '|' and not ticks and not wiki:
            delimiters.append(index)
        index += 1
    if not delimiters:
        return None
    left = delimiters[0] if not line[:delimiters[0]].strip() else None
    right = delimiters[-1] if not line[delimiters[-1] + 1:].strip() else None
    boundaries = [-1, *delimiters, len(line)]
    spans = [(a + 1, b) for a, b in zip(boundaries, boundaries[1:])]
    if left is not None:
        spans = spans[1:]
    if right is not None:
        spans = spans[:-1]
    if len(spans) > MAX_COLUMNS:
        raise SSOTError('column_limit')
    raw = [line[start:end] for start, end in spans]
    if any(len(cell.encode('utf-8')) > MAX_CELL for cell in raw):
        raise SSOTError('cell_limit')
    values = [re.sub(r'\\([\\|])', r'\1', cell.strip()) for cell in raw]
    return dict(values=values, spans=spans, raw=raw, right=right)


# vault_state.py carries a byte-identical copy of the block below: this module
# imports vault_state, so importing back would be a cycle, and a helper kept only
# there would sit outside the checker_sha256-bound files.
# tests/test_rule_sources.py fails if the two copies drift apart.
# --- BEGIN ATX HEADING (verbatim copy) ---
_ATX_OPEN_RE = re.compile(r' {0,3}(#{1,6})(?=\s)')


def _atx_heading(text):
    """Return (level, raw label) of one ATX heading line, or None, in linear time.

    Precondition: text holds no line feed (callers split bytes with splitlines
    and strip CR/LF). The label keeps the earlier regex semantics, not
    CommonMark; callers strip and casefold it themselves.
    """
    # Same result as the former re.match(r' {0,3}(#{1,6})\s+(.+?)\s*#*$', text),
    # whose lazy label retried the optional closing run at every position
    # (quadratic on '# a' + many spaces + 'b'). For str patterns \s is
    # str.isspace, the test that argument-free lstrip() and rstrip() use.
    match = _ATX_OPEN_RE.match(text)
    if not match:
        return None
    rest = text[match.end():]
    body = rest.lstrip()
    if not body:
        # Only whitespace follows: \s+ gave its last character to the label.
        return (len(match[1]), rest[-1]) if len(rest) >= 2 else None
    # The shortest label after which only whitespace and a '#' run remain.
    return len(match[1]), body.rstrip('#').rstrip() or body[0]
# --- END ATX HEADING (verbatim copy) ---


def _line_rows(raw):
    fence, offset = '', 0
    rows, headings = [], []
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        start, offset = offset, offset + len(line)
        decoded = line.decode('utf-8')
        text = decoded.rstrip('\r\n')
        prefix = text.lstrip(' ')
        indent = len(text) - len(prefix)
        marker = re.match(r'(`{3,}|~{3,})(.*)$', prefix) if indent <= 3 else None
        eligible = not fence and indent < 4
        if fence:
            eligible = False
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ''
        elif marker:
            fence = marker[1]
            eligible = False
        if eligible:
            heading = _atx_heading(text)
            if heading:
                level, label = heading[0], heading[1].strip().casefold()
                headings = [(depth, status) for depth, status in headings if depth < level]
                explicit = 'confirmed' if re.match(r'^(?:✅\s*확정|확정|confirmed)(?:\s*$|\s*[—:：-])', label) else ''
                headings.append((level, explicit))
        section_status = next((status for _, status in reversed(headings) if status), '')
        rows.append(dict(line=number, start=start, end=offset, text=text,
                         newline=decoded[len(text):], eligible=eligible, section_status=section_status))
    return rows


def _canonical(value):
    return _ALIAS.get(value.strip().casefold())


def parse_ledger(text):
    """Parse up to eight explicitly recognizable fact tables, without I/O.

    Recognizable tables contain subject/relation/value (documented aliases are
    accepted). Non-ledger tables/examples are ignored. Invalid ledger rows and
    duplicate aliases remain visible in diagnostics; no absent values are filled.
    """
    if not isinstance(text, str) or '\x00' in text:
        raise SSOTError('invalid_text')
    try:
        raw = text.encode('utf-8')
    except UnicodeError as exc:
        raise SSOTError('invalid_utf8') from exc
    if len(raw) > MAX_BYTES:
        raise SSOTError('input_byte_limit')
    lines = _line_rows(raw)
    tables, diagnostics, total_rows, index = [], [], 0, 0
    while index + 1 < len(lines):
        header, divider = lines[index], lines[index + 1]
        if not header['eligible'] or not divider['eligible']:
            index += 1
            continue
        cells = _cells(header['text'])
        if not cells:
            index += 1
            continue
        columns = [_canonical(value) for value in cells['values']]
        if not IDENTITY.issubset(columns):
            index += 1
            continue
        separator = _cells(divider['text'])
        if not separator or not all(DIVIDER_RE.fullmatch(value) for value in separator['values']):
            index += 1
            continue
        if len(tables) >= MAX_TABLES:
            raise SSOTError('table_limit')
        duplicate = sorted({field for field in columns if field and columns.count(field) > 1})
        if duplicate:
            diagnostics.append(dict(code='ssot-duplicate-column', line=header['line'], details=dict(fields=duplicate)))
        if len(separator['values']) != len(columns):
            diagnostics.append(dict(code='ssot-invalid-row', line=divider['line'], details=dict(expected=len(columns), actual=len(separator['values']))))
        table = dict(schema_version=2 if set(FIELDS).issubset(columns) else 1,
                     line=header['line'], columns=columns, headers=cells['values'], rows=[],
                     start=header['start'], end=divider['end'], header=header, divider=divider,
                     missing_fields=[field for field in FIELDS if field not in columns],
                     duplicate_fields=duplicate, section_status=header['section_status'])
        index += 2
        while index < len(lines) and lines[index]['eligible']:
            row = lines[index]
            cell_row = _cells(row['text'])
            if not cell_row:
                break
            total_rows += 1
            if total_rows > MAX_ROWS:
                raise SSOTError('row_limit')
            if len(cell_row['values']) != len(columns):
                diagnostics.append(dict(code='ssot-invalid-row', line=row['line'], details=dict(expected=len(columns), actual=len(cell_row['values']))))
            else:
                fields = {field: cell_row['values'][col] for col, field in enumerate(columns) if field}
                table['rows'].append(dict(line=row['line'], fields=fields, cells=cell_row['values'], raw=row))
            table['end'] = row['end']
            index += 1
        tables.append(table)
    return dict(version=1, tables=tables, diagnostics=diagnostics, source_sha256=_sha(raw), row_count=total_rows)


def _warning(code, line, path, details):
    identity = dict(code=code, line=line, path=path, details=details)
    return dict(issue_id='ssot-' + _sha(_encode(identity))[:20], severity='warning',
                message=code.replace('ssot-', '').replace('-', ' '), **identity)


def _date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError()
    return date.fromisoformat(value)


def _validate_parsed(parsed):
    try:
        if len(_encode(parsed)) > 2 * 1024 * 1024:
            raise SSOTError('parsed_byte_limit')
        if (not isinstance(parsed, dict) or type(parsed.get('version')) is not int or parsed['version'] != 1
                or not isinstance(parsed.get('tables'), list) or len(parsed['tables']) > MAX_TABLES
                or not isinstance(parsed.get('diagnostics'), list) or len(parsed['diagnostics']) > 1024):
            raise SSOTError('invalid_parsed_ledger')
        rows = 0
        for diagnostic in parsed['diagnostics']:
            if (not isinstance(diagnostic, dict) or not isinstance(diagnostic.get('code'), str)
                    or len(diagnostic['code']) > 80 or type(diagnostic.get('line')) is not int
                    or diagnostic['line'] < 1 or not isinstance(diagnostic.get('details'), dict)):
                raise SSOTError('invalid_parsed_ledger')
        for table in parsed['tables']:
            if (not isinstance(table, dict) or type(table.get('schema_version')) is not int
                    or table['schema_version'] not in (1, 2) or type(table.get('line')) is not int
                    or table['line'] < 1 or not isinstance(table.get('missing_fields'), list)
                    or len(table['missing_fields']) > MAX_COLUMNS
                    or any(field not in FIELDS for field in table['missing_fields'])
                    or not isinstance(table.get('duplicate_fields'), list)
                    or len(table['duplicate_fields']) > MAX_COLUMNS
                    or any(field not in ALIASES for field in table['duplicate_fields'])
                    or not isinstance(table.get('section_status', ''), str)
                    or table.get('section_status', '') not in ('', 'confirmed')
                    or not isinstance(table.get('rows'), list)):
                raise SSOTError('invalid_parsed_ledger')
            rows += len(table['rows'])
            if rows > MAX_ROWS:
                raise SSOTError('row_limit')
            for row in table['rows']:
                if (not isinstance(row, dict) or type(row.get('line')) is not int or row['line'] < 1
                        or not isinstance(row.get('fields'), dict) or len(row['fields']) > MAX_COLUMNS
                        or not IDENTITY.issubset(row['fields'])
                        or any(field not in ALIASES or not isinstance(value, str)
                               or len(value.encode('utf-8')) > MAX_CELL for field, value in row['fields'].items())):
                    raise SSOTError('invalid_parsed_ledger')
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise SSOTError('invalid_parsed_ledger') from exc


def check_ledger(text_or_parsed, *, path=''):
    """Warn about explicit validity/source issues; never select a true value."""
    parsed = parse_ledger(text_or_parsed) if isinstance(text_or_parsed, str) else text_or_parsed
    _validate_parsed(parsed)
    if not isinstance(path, str) or len(path) > 1024 or '\x00' in path:
        raise SSOTError('invalid_path')
    warnings = [_warning(row['code'], row['line'], path, row['details']) for row in parsed['diagnostics']]
    comparable, open_unknown = [], []
    for table in parsed['tables']:
        if table['schema_version'] != 2:
            warnings.append(_warning('ssot-legacy-schema', table['line'], path, dict(missing=table['missing_fields'])))
        if table['duplicate_fields']:
            continue
        for row in table['rows']:
            fields, line = row['fields'], row['line']
            if any(not fields.get(field, '').strip() for field in IDENTITY):
                warnings.append(_warning('ssot-empty-fact', line, path, {}))
                continue
            state_value = fields.get('status', '').strip().casefold() or table.get('section_status', '')
            if state_value in CONFIRMED and not fields.get('source', '').strip():
                warnings.append(_warning('ssot-confirmed-source-missing', line, path, {}))
            parsed_dates, invalid = {}, False
            for field in ('valid_from', 'valid_until', 'recorded'):
                value = fields.get(field, '')
                if not value:
                    continue
                try:
                    parsed_dates[field] = _date(value)
                except ValueError:
                    warnings.append(_warning('ssot-invalid-date', line, path, dict(field=field)))
                    if field != 'recorded':
                        invalid = True
            if 'valid_from' not in parsed_dates and not fields.get('valid_from', ''):
                warnings.append(_warning('ssot-unknown-validity', line, path, {}))
            if not fields.get('recorded', ''):
                warnings.append(_warning('ssot-unknown-recorded', line, path, {}))
            lower, upper = parsed_dates.get('valid_from'), parsed_dates.get('valid_until')
            if lower and upper and lower >= upper:
                warnings.append(_warning('ssot-invalid-date', line, path, dict(field='valid_interval')))
                invalid = True
            if lower and not invalid and state_value not in REJECTED:
                comparable.append((row, lower, upper))
            if not fields.get('valid_until', '') and not invalid and state_value not in REJECTED:
                open_unknown.append((row, lower))
    for index, (left, left_from, left_until) in enumerate(comparable):
        for right, right_from, right_until in comparable[index + 1:]:
            a, b = left['fields'], right['fields']
            if (a['subject'], a['relation']) != (b['subject'], b['relation']) or a['value'] == b['value']:
                continue
            if (left_until is None or right_from < left_until) and (right_until is None or left_from < right_until):
                warnings.append(_warning('ssot-temporal-conflict', left['line'], path,
                                         dict(other_line=right['line'], subject=a['subject'], relation=a['relation'])))
    # Distinct uncapped claims are a visible conflict even when their effective
    # start is unknown. This finding does not assert an inferred overlap date.
    for index, (left, left_from) in enumerate(open_unknown):
        for right, right_from in open_unknown[index + 1:]:
            a, b = left['fields'], right['fields']
            if left_from and right_from:
                continue
            if (a['subject'], a['relation']) == (b['subject'], b['relation']) and a['value'] != b['value']:
                warnings.append(_warning('ssot-open-ended-conflict', left['line'], path,
                                         dict(other_line=right['line'], subject=a['subject'], relation=a['relation'], validity='unknown')))
    # O(rows^2) is capped at 256 rows. Keep individual issue count bounded too.
    if len(warnings) > 1024:
        raise SSOTError('warning_limit')
    return warnings


def _nfc(text):
    return unicodedata.normalize('NFC', text)


def _literal(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _render(text, alias):
    """Drop code/bold markup and render wiki links as their target (or display alias)."""
    def link(match):
        target, separator, display = match[1].partition('|')
        return display if alias and separator and display.strip() else target.split('#', 1)[0]
    return WIKI_REF_RE.sub(link, text).replace('`', '').replace('**', '').replace('__', '')


def _forms(text, *, aliases=False):
    """Literal forms compared by check_support: as written and with markup rendered."""
    candidates = (text, _render(text, False), *((_render(text, True),) if aliases else ()))
    return tuple(dict.fromkeys(form for form in map(_literal, candidates) if form))


def _split_lines(text):
    """text split into lines where _line_rows splits a note: at LF, CR and CRLF only.

    str.splitlines also breaks at U+000B, U+000C, U+001C-U+001E, U+0085,
    U+2028 and U+2029, which parse_ledger keeps inside a line.
    """
    return [line.decode('utf-8') for line in text.encode('utf-8').splitlines()]


def _note_body(text, marked=None):
    """The text below a leading frontmatter block, else the whole text.

    The block runs from a first line '---' to the next '---' line within 64
    lines, on _split_lines lines. marked is text with cut marks inside its
    lines (_without_tables, same lines): the block is decided on text and the
    body is taken from marked, so a mark never moves the boundary.
    """
    lines = _split_lines(text)
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:65], 1) if line.strip() == '---'), None)
        if end is not None:
            return '\n'.join((lines if marked is None else _split_lines(marked))[end + 1:])
    return text if marked is None else marked


def _body_forms(text, marked=None):
    # Bodies add a display-alias form; ledger values never match by alias alone.
    return _forms(re.sub(r'\\([\\|])', r'\1', _note_body(text, marked)), aliases=True)


def _support_pattern(literal):
    """An ASCII letter/digit next to an alphanumeric edge, or a longer number, is another literal."""
    prefix = suffix = ''
    if literal[0] in _ASCII_ALNUM:
        prefix = r'(?<![0-9a-z])' + (r'(?<![0-9][.,])' if literal[0].isdigit() else '')
    if literal[-1] in _ASCII_ALNUM:
        suffix = r'(?![0-9a-z])' + (r'(?![.,][0-9])' if literal[-1].isdigit() else '')
    return re.compile(prefix + re.escape(literal) + suffix)


_BACKTICK_RUN_RE = re.compile(r'`+')


def _code_spans(cell):
    """(start, end, group 2) of each former re.finditer(r'(`+)(.+?)\\1', cell) match.

    The same matches, line feeds included, in linear time; the regex
    (CODE_SPAN_RE) was cubic on a cell of backticks and stays in
    tests/test_linear_line_parsers.py as an oracle.
    """
    # From each backtick the regex tried the opening lengths from the rest of
    # its run down to one, each with the nearest later run of exactly that many
    # backticks after at least one character ('.+?' stops at a line feed). An
    # opening of at least half the rest can close only at the start of a later
    # run on the same line that is at least that long; failing that, a rest of
    # three or more closes on itself around one backtick.
    runs = [match.span() for match in _BACKTICK_RUN_RE.finditer(cell)]
    later, longest = [0] * len(runs), 0  # the longest later run before a line feed
    for j in range(len(runs) - 1, -1, -1):
        if j + 1 < len(runs) and cell.find('\n', runs[j][1], runs[j + 1][0]) >= 0:
            longest = 0
        later[j], longest = longest, max(longest, runs[j][1] - runs[j][0])
    spans, cursor, j = [], 0, 0
    while j < len(runs):
        start = max(cursor, runs[j][0])
        size = runs[j][1] - start  # backticks left in this run
        close = min(size, later[j])
        if 2 * close >= size:
            j += 1
            while runs[j][1] - runs[j][0] < close:
                j += 1
            spans.append((start, runs[j][0] + close, cell[start + close:runs[j][0]]))
        elif size >= 3:
            half = (size - 1) // 2
            spans.append((start, start + 2 * half + 1, '`'))
        else:
            j += 1  # no later run on its line and too short to close on itself
            continue
        cursor = spans[-1][1]
        if cursor == runs[j][1]:
            j += 1
    return spans


def _references(cell):
    """Return (target, strict) note references in one source cell, in order.

    Wiki links are strict; an anchor-only link ('[[#heading]]') targets the
    ledger itself and an empty link is ignored. A Markdown path in a code span,
    or a whole cell that is one whitespace-free .md path, is a plain reference;
    an invalid plain path is ignored rather than reported.
    """
    found, outside, cursor = [], [], 0
    for start, end, content in _code_spans(cell):
        content = content.strip()
        if content.casefold().endswith('.md'):
            found.append((content, False))
        outside.append(cell[cursor:start])
        cursor = end
    # Each code span becomes one space, as the former CODE_SPAN_RE.sub(' ', cell).
    text = ' '.join([*outside, cell[cursor:]])
    for match in WIKI_REF_RE.finditer(text):
        link = match[1].split('|', 1)[0].strip()
        target = link.split('#', 1)[0].strip()
        if target or link.startswith('#'):
            found.append((target, True))
    whole = cell.strip()
    if (not found and whole.casefold().endswith('.md') and not any(char.isspace() for char in whole)
            and not any(char in whole for char in '`[]')):
        found.append((whole, False))
    unique = {}
    for target, strict in found:
        unique[target] = unique.get(target, False) or strict
    return list(unique.items())


def _inventory(policy):
    """Allowed Markdown names and paths; denied/excluded zones are never listed or described."""
    stems, paths, unsafe_stems, unsafe_paths = {}, {}, set(), set()
    pending, entries, complete = [()], 0, True
    while pending:
        parts = pending.pop()
        try:
            directory = resolve_note_path(policy.vault, '/'.join(parts), policy.rules) if parts else policy.root
            with os.scandir(directory) as iterator:
                listed = []
                for entry in iterator:
                    entries += 1
                    if entries > MAX_INVENTORY_ENTRIES:
                        return dict(stems=stems, paths=paths, unsafe_stems=unsafe_stems,
                                    unsafe_paths=unsafe_paths, complete=False)
                    listed.append((entry.name, entry.is_dir(follow_symlinks=False)))
        except (OSError, ValueError):
            complete = False
            continue
        children = []
        for name, is_directory in sorted(listed):
            child = (*parts, name)
            note = name.casefold().endswith('.md')
            if (not is_directory and not note) or any(zone_matches(child, rule) for rule in policy.rules):
                continue
            relative = '/'.join(child)
            try:
                mode = resolve_note_path(policy.vault, relative, policy.rules).lstat().st_mode
            except ValueError:
                if note:
                    unsafe_stems.add(_nfc(name[:-3]))
                    unsafe_paths.add(_nfc(relative))
                continue
            except OSError:
                complete = False
                continue
            if stat.S_ISDIR(mode):
                children.append(child)
            elif note:
                stems.setdefault(_nfc(name[:-3]), []).append(relative)
                paths[_nfc(relative)] = relative
        pending.extend(reversed(children))
    return dict(stems=stems, paths=paths, unsafe_stems=unsafe_stems, unsafe_paths=unsafe_paths, complete=complete)


def _inventory_of(policy, run):
    if run['inventory'] is None:
        run['inventory'] = _inventory(policy)
        if not run['inventory']['complete']:
            run['incomplete'].add('inventory_incomplete')
    return run['inventory']


def _locate(policy, target, strict, ledger_path, run):
    """Resolve one reference to ('note', path), ('self', path), ('skip', None) or ('unresolved', details).

    A path (contains '/') must match one allowed note exactly; './' and '../'
    are relative to the ledger. A bare name must be the exact (NFC) filename
    stem of exactly one allowed note. Policy zones are checked on the reference
    text before any lookup. An unmatched attachment-like name is skipped.
    """
    if not target:
        return 'self', ledger_path
    markdown = target.casefold().endswith('.md')
    attachment = not markdown and ATTACHMENT_RE.search(target) is not None
    name = target if markdown else target + '.md'
    invalid = ('unresolved', dict(reason='invalid_reference')) if strict and not attachment else ('skip', None)
    if '/' in name or '\\' in name:
        candidate = name.replace('\\', '/')
        if candidate.startswith(('./', '../')):
            candidate = posixpath.join(posixpath.dirname(ledger_path), candidate)
        try:
            parts = relative_parts(posixpath.normpath(candidate), 'reference')
        except ValueError:
            return invalid
        key = _nfc('/'.join(parts))
        if key == _nfc(ledger_path):
            return 'self', ledger_path
        if any(zone_matches(parts, rule) for rule in policy.rules):
            return ('skip', None) if attachment else ('unresolved', dict(reason='policy'))
        inventory = _inventory_of(policy, run)
        if key in inventory['paths']:
            return 'note', inventory['paths'][key]
        matches, unsafe = [], key in inventory['unsafe_paths']
    else:
        if unsafe_path_segment(name):
            return invalid
        stem = _nfc(name[:-3])
        inventory = _inventory_of(policy, run)
        matches = sorted(inventory['stems'].get(stem, []))
        if len(matches) > 1:
            return 'unresolved', dict(reason='ambiguous', candidate_count=len(matches),
                                      candidates=matches[:MAX_CANDIDATES])
        if matches and inventory['complete']:
            return ('self', ledger_path) if matches[0] == ledger_path else ('note', matches[0])
        unsafe = stem in inventory['unsafe_stems']
    if not matches and attachment:
        return 'skip', None
    if not matches and unsafe:
        return 'unresolved', dict(reason='policy')
    # One unseen duplicate could exist when the inventory is incomplete.
    return 'unresolved', dict(reason='not_found' if inventory['complete'] else 'inventory_incomplete')


def _cited_forms(policy, relative, run):
    """Read one allowed cited note once under the note policy and bounded budgets.

    Success is ((body, outside_tables), None): the literal forms of the whole
    body and, when the body holds fact tables, of the body without them (else
    None). Both come from the same cached read, budgets and stable-read mark.
    A note whose body parse_ledger refuses is recorded in run['unseparated'].
    """
    if relative in run['cache']:
        return run['cache'][relative]
    if run['notes'] >= MAX_SUPPORT_NOTES or run['bytes'] >= MAX_SUPPORT_BYTES:
        run['incomplete'].add('source_limit')
        outcome = (None, 'source_limit')
    else:
        try:
            info, mark, raw = stable_read(lambda: policy.note(relative), MAX_BYTES, contents=True)
            text = raw.decode('utf-8-sig')
        except EvidenceError as exc:
            outcome = (None, {'input_too_large': 'size_limit', 'unsafe_file': 'unsafe_file'}.get(str(exc), 'unreadable'))
        except state.StateError:
            outcome = (None, 'policy')
        except FileNotFoundError:
            outcome = (None, 'not_found')
        except (OSError, ValueError, RuntimeError):
            outcome = (None, 'unreadable')
        else:
            run['notes'] += 1
            run['bytes'] += info['size']
            if run['bytes'] > MAX_SUPPORT_BYTES:
                run['incomplete'].add('source_limit')
                outcome = (None, 'source_limit')
            else:
                run['marks'][relative] = mark
                run['cited'][relative] = dict(path=relative, sha256=info['sha256'], bytes=info['size'])
                if 'binding_raw' in run:
                    # Only the opt-in diagnostic retains the already permitted,
                    # stable read; never race a second read for line evidence.
                    run['binding_raw'][relative] = text
                outside, refusal = _separate_tables(text)
                if refusal:
                    run['unseparated'][relative] = refusal
                outcome = ((_body_forms(text), outside), None)
    run['cache'][relative] = outcome
    return outcome


# Literal forms fold every whitespace run to one space (_literal), so a line
# break alone let the text above a removed fact table and the text below it
# read as one value. The text before a cut ends with this mark instead: NUL is
# not whitespace, NFKC and casefold keep it, no ledger value holds it
# (parse_ledger refuses NUL) and vault_period never reads across it.
_CUT = '\x00'


def _without_tables(raw, parsed):
    """raw without parsed's fact tables, decoded, with _CUT where a table stood between text.

    A table starts and ends on line boundaries, so the text before it ends
    with a line break; the mark goes just before that break, which keeps every
    line of the joined text in place (_self_forms relies on it).
    """
    pieces, cursor = [], 0
    for table in parsed['tables']:
        pieces.append(raw[cursor:table['start']].decode('utf-8'))
        cursor = table['end']
    pieces.append(raw[cursor:].decode('utf-8'))
    text = pieces[0]
    for piece in pieces[1:]:
        if text and piece:
            cut = len(text) - (2 if text.endswith('\r\n') else 1 if text.endswith(('\r', '\n')) else 0)
            text = text[:cut] + _CUT + text[cut:]
        text += piece
    return text


def _self_forms(raw, parsed):
    """Forms of the ledger's own body outside its fact tables, for a self-citation.

    The v0.30.0 order stays: the tables are cut first and the frontmatter is
    decided on what remains, without the cut marks (the ledger holds no NUL,
    so removing _CUT gives that text back). A leading BOM is dropped first, as
    the utf-8-sig read of a cited note drops it.
    """
    marked = _without_tables(raw, parsed).removeprefix('\ufeff')
    return _body_forms(marked.replace(_CUT, ''), marked)


def _separate_tables(text):
    """(outside, refusal): a cited note's body forms without its fact tables.

    The frontmatter boundary is decided on the whole note first, as for the
    whole-body forms, and fact tables are then removed only inside that body:
    a '---' line left on top once a table is cut never opens a frontmatter
    block. outside is None when the body has no table parse_ledger recognises
    (v1 or v2). A body parse_ledger refuses (column, cell, table or row limit,
    NUL) also counts as having no tables, and refusal is that fixed error
    code. The offsets are for body.encode('utf-8'): the read decoded
    utf-8-sig, so slicing the raw bytes of a BOM-prefixed note would cut three
    bytes too early. The self-citation path keeps its v0.30.0 order (_self_forms).
    """
    body = _note_body(text)
    try:
        parsed = parse_ledger(body)
    except SSOTError as exc:
        return None, str(exc)
    if not parsed['tables']:
        return None, None
    # The same forms as _body_forms, without deciding the frontmatter again.
    return _forms(re.sub(r'\\([\\|])', r'\1', _without_tables(body.encode('utf-8'), parsed)), aliases=True), None


def _matches(patterns, forms):
    return any(pattern.search(body) for pattern in patterns for body in forms)


def _binding_lines(text):
    """Eligible original physical lines, excluding frontmatter and fact tables.

    Parse refusals/malformed tables are unknown, rather than permission to
    treat an unseparated table as primary evidence. Line numbers refer to the
    original source (including a BOM/frontmatter), never a joined body.
    """
    text = text.removeprefix('\ufeff')
    lines = _split_lines(text)
    start = 0
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:65], 1) if line.strip() == '---'), None)
        if end is None:
            return (), 'frontmatter_unclosed'
        start = end + 1
    body = '\n'.join(lines[start:])
    try:
        parsed = parse_ledger(body)
    except SSOTError:
        return (), 'source_parse_limit'
    if parsed['diagnostics']:
        return (), 'source_malformed'
    tables = parsed['tables']
    physical = _line_rows(body.encode('utf-8'))
    return tuple((line['line'] + start,
                  _forms(re.sub(r'\\([\\|])', r'\1', line['text']), aliases=True))
                 for line in physical
                 if not any(table['start'] <= line['start'] < table['end'] for table in tables)), None


def _binding_observation(row, path, status, bodies, unresolved, run, reference_limited):
    """Content-free lexical co-occurrence; no semantic or truth inference."""
    entry = dict(path=path, line=row['line'], status='unassessed', reason='literal_not_supported', via=[])
    subject = row['fields'].get('subject', '')
    # Rendering is deliberately small, not a complete Markdown parser. Its
    # leftover punctuation (for example the '*' in '***') is not a subject.
    # An explicit display alias can supply substance, but must not enable a
    # marker-only target candidate to bind against formatting in the source.
    if not any(char.isalnum() for alias in (False, True) for char in _literal(_render(subject, alias))):
        entry['reason'] = 'subject_missing'
        return entry
    subjects = tuple(form for form in _forms(subject, aliases=True) if any(char.isalnum() for char in form))
    if status != 'supported':
        return entry
    patterns = [_support_pattern(form) for form in _forms(row['fields'].get('value', '').strip())]
    subject_patterns = [_support_pattern(form) for form in subjects]
    values, bound, incomplete = [], [], bool(unresolved or reference_limited)
    for cited in sorted(bodies):
        if cited not in run['binding_lines']:
            text = run['binding_raw'].get(cited)
            run['binding_lines'][cited] = _binding_lines(text) if text is not None else ((), 'source_unavailable')
        lines, refusal = run['binding_lines'][cited]
        incomplete = incomplete or bool(refusal)
        for number, forms in lines:
            if _matches(patterns, forms):
                reference = dict(path=cited, line=number)
                # Keep output bounded even for a note repeating a value on
                # thousands of lines. Scan every line before deciding status.
                if len(values) < 32:
                    values.append(reference)
                if _matches(subject_patterns, forms) and len(bound) < 32:
                    bound.append(reference)
    if bound:
        entry.update(status='bound', reason='same_physical_line', via=bound)
    elif incomplete:
        entry.update(reason='source_incomplete', via=values)
    elif values:
        entry.update(status='subject_absent', reason='subject_not_on_value_line', via=values)
    else:
        entry['reason'] = 'value_not_on_physical_line'
    return entry


def check_support(vault, path, *, primary_text_only=False, normalize_periods=False, fiscal_start=None,
                  row_references=False, bind_subject=False):
    """Warn when a row value is absent from its cited note or the citation is unresolved.

    Warning-only observation, never a truth or authority decision. A value is
    supported when one of its normalized literal forms (NFKC, casefold,
    collapsed whitespace; optionally without wiki-link/code/bold markup) occurs
    in the body of at least one cited note. Rows without a value, a source or
    a note reference are skipped and keep their existing check treatment.

    A supported value found only inside fact tables of other cited ledgers is
    listed in ledger_table_only with those ledgers' paths. It still counts as
    supported unless primary_text_only=True, which counts it as unsupported
    with reason 'ledger_table_only'. The rule is structural, not provenance:
    a ledger's prose or a plain note that copies its values still counts.
    The text above and below a left-out fact table never reads as one value
    or period (_CUT), and bodies break lines at CR and LF only, as
    parse_ledger does.
    primary_text_only=True also adds ledger_tables_unseparated: the cited
    notes whose body parse_ledger refuses (path and fixed reason only), whose
    fact tables, if any, could not be left out and so still count.

    normalize_periods=True adds normalized_periods: for each compared row whose
    value names a period, normalized_match (equal, contained, none or abstain)
    against the periods of the same cited bodies (vault_period), with via or
    reason, plus period_convention. Both sides are read with wiki-link, code
    and bold markup rendered. Without primary_text_only an entry also names
    in via_ledger_table_only each via path of another ledger that leaves via,
    or with which the result weakens, once the fact tables of every cited
    ledger (the primary_text_only comparison) or of that ledger alone are
    left out; a cited ledger whose body parse_ledger refuses has no separable
    tables and is never named. With or without primary_text_only, a cited
    body too long for vault_period is left out of the comparison with
    incomplete ['period_text_limit']. fiscal_start (1-12, needs
    normalize_periods) declares the first month of a fiscal year. A
    vault_period that cannot be imported raises
    SSOTError('period_normalizer_unavailable'). Literal support, counts and
    warnings stay exactly as without it.

    row_references=True exports every parsed row as content-free references
    and a generic audit_population. bind_subject=True separately observes
    subject/value co-occurrence on original physical source lines outside
    frontmatter and fact tables, using only cached permitted stable reads.
    Its binding_warnings never enter warnings or change literal support.
    Co-occurrence is not semantic attribution or truth verification.
    """
    if any(type(option) is not bool for option in (primary_text_only, normalize_periods, row_references, bind_subject)):
        raise SSOTError('invalid_opt_in')
    if fiscal_start is not None and (not normalize_periods or type(fiscal_start) is not int
                                     or not 1 <= fiscal_start <= 12):
        raise SSOTError('invalid_opt_in')
    if normalize_periods:
        # Loaded only on request: lint and default check-support never import it.
        # A damaged module (a syntax error, a failing import) is the same
        # content-free error as a missing one, never a traceback.
        try:
            import vault_period as period
        except Exception as exc:
            raise SSOTError('period_normalizer_unavailable') from exc
    try:
        policy = state._Policy(vault)
        info, _, raw = policy.read_note(path)
        parsed = parse_ledger(raw.decode('utf-8'))
        ledger_path = info['path']
        run = dict(cache={}, notes=0, bytes=0, marks={}, cited={}, incomplete=set(), inventory=None, unseparated={})
        if bind_subject:
            run.update(binding_raw={}, binding_lines={})
        references_rows, binding_rows, binding_warnings = [], [], []
        warnings = []
        counts = dict(rows_checked=0, rows_supported=0, rows_unsupported=0, rows_unresolved=0, rows_skipped=0,
                      rows_supported_ledger_table_only=0)
        table_only = []
        self_forms = None
        period_rows, period_index = [], {}
        for table in parsed['tables']:
            for row in table['rows']:
                value_forms = _forms(row['fields'].get('value', '').strip())
                source = row['fields'].get('source', '').strip()
                references = _references(source) if value_forms and source and not table['duplicate_fields'] else []
                reference_limited = len(references) > MAX_REFERENCES
                if len(references) > MAX_REFERENCES:
                    references = references[:MAX_REFERENCES]
                    run['incomplete'].add('reference_limit')
                bodies, outside, unresolved = {}, {}, []
                for target, strict in references:
                    kind, detail = _locate(policy, target, strict, ledger_path, run)
                    if kind == 'skip':
                        continue
                    if kind == 'unresolved':
                        unresolved.append((target, detail))
                        continue
                    if kind == 'self':
                        if self_forms is None:
                            # A row cannot support itself: compare outside the fact tables.
                            self_forms = _self_forms(raw, parsed)
                            run['cited'][ledger_path] = dict(path=ledger_path, sha256=info['sha256'], bytes=info['size'])
                            if bind_subject:
                                run['binding_raw'][ledger_path] = raw.decode('utf-8-sig')
                        bodies[ledger_path] = self_forms
                        continue
                    forms, reason = _cited_forms(policy, detail, run)
                    if forms is None:
                        unresolved.append((target, dict(reason=reason)))
                    else:
                        bodies[detail], stripped = forms
                        if stripped is not None:
                            outside[detail] = stripped
                if not bodies and not unresolved:
                    counts['rows_skipped'] += 1
                    if row_references:
                        reason = ('duplicate_fields' if table['duplicate_fields'] else 'value_missing' if not value_forms
                                  else 'source_missing' if not source else 'no_note_reference')
                        references_rows.append(dict(path=ledger_path, line=row['line'], status='skipped', reason=reason, via=[]))
                    if bind_subject:
                        binding_rows.append(_binding_observation(row, ledger_path, 'skipped', bodies, unresolved, run,
                                                                 reference_limited))
                    continue
                counts['rows_checked'] += 1
                for target, detail in unresolved:
                    warnings.append(_warning('ssot-source-unresolved', row['line'], ledger_path,
                                             dict(reference=target[:MAX_REFERENCE_CHARS], **detail)))
                if not bodies:
                    counts['rows_unresolved'] += 1
                    if row_references:
                        references_rows.append(dict(path=ledger_path, line=row['line'], status='unresolved',
                                                    reason='source_unresolved', via=[]))
                    if bind_subject:
                        binding_rows.append(_binding_observation(row, ledger_path, 'unresolved', bodies, unresolved,
                                                                 run, reference_limited))
                    continue
                patterns = [_support_pattern(form) for form in value_forms]
                supported, extra = any(_matches(patterns, forms) for forms in bodies.values()), {}
                # Another ledger's fact tables are secondary material, as the
                # ledger's own tables are on a self-citation (self_forms).
                if supported and outside and not any(_matches(patterns, outside.get(cited, forms))
                                                     for cited, forms in bodies.items()):
                    counts['rows_supported_ledger_table_only'] += 1
                    table_only.append(dict(line=row['line'], via=sorted(
                        cited for cited in outside if _matches(patterns, bodies[cited]))))
                    if primary_text_only:
                        supported, extra = False, dict(reason='ledger_table_only')
                if supported:
                    counts['rows_supported'] += 1
                else:
                    counts['rows_unsupported'] += 1
                    warnings.append(_warning('ssot-source-unsupported', row['line'], ledger_path,
                                             dict(cited=sorted(bodies), **extra)))
                status = 'supported' if supported else 'unsupported'
                if row_references:
                    compared = {cited: outside.get(cited, forms) if primary_text_only else forms
                                for cited, forms in bodies.items()}
                    references_rows.append(dict(path=ledger_path, line=row['line'], status=status,
                                                reason='literal_present' if supported else extra.get('reason', 'literal_absent'),
                                                via=sorted(cited for cited, forms in compared.items() if _matches(patterns, forms))))
                if bind_subject:
                    observation = _binding_observation(row, ledger_path, status, bodies, unresolved, run, reference_limited)
                    binding_rows.append(observation)
                    if observation['status'] == 'subject_absent':
                        binding_warnings.append(_warning('ssot-subject-unbound', row['line'], ledger_path,
                                                         dict(reason=observation['reason'])))
                # Period intervals are a separate observation after the literal
                # result; they never change it. value_forms[-1] has markup
                # rendered, and the body indexes read only rendered forms too.
                records = period.parse(value_forms[-1], fiscal_start=fiscal_start) if normalize_periods else None
                if records:
                    for cited, forms in bodies.items():
                        if cited not in period_index:
                            # [index of the compared text, index outside its fact tables (on demand)].
                            # An index is None when a form is longer than vault_period reads;
                            # that body is left out of the comparison, never failing the run.
                            compared = outside.get(cited, forms) if primary_text_only else forms
                            period_index[cited] = [_period_index(period, compared, fiscal_start), None]
                    indexes = {cited: period_index[cited][0] for cited in bodies if period_index[cited][0] is not None}
                    entry = dict(line=row['line'], **period.match(records, indexes))
                    # As for ledger_table_only: a via path of another ledger holds
                    # only through fact tables when leaving out every cited
                    # ledger's tables (the --primary-text-only comparison) or only
                    # its own drops it from via or weakens the result.
                    tables = []
                    ledgers = [] if primary_text_only else [cited for cited in entry.get('via', []) if cited in outside]
                    if ledgers:
                        for cited in indexes:
                            if cited in outside and period_index[cited][1] is None:
                                period_index[cited][1] = _period_index(period, outside[cited], fiscal_start) or ()
                        joint = {cited: period_index[cited][1] if cited in outside else index
                                 for cited, index in indexes.items()}
                        without_all = period.match(records, joint)
                        tables = [cited for cited in ledgers if not (
                            _period_kept(period, entry, without_all, cited)
                            and _period_kept(period, entry, period.match(records, {**indexes, cited: joint[cited]}), cited))]
                    if tables:
                        entry['via_ledger_table_only'] = tables
                    if any(period_index[cited][0] is None for cited in bodies):
                        entry['incomplete'] = ['period_text_limit']
                    period_rows.append(entry)
        if len(warnings) > 1024:
            raise SSOTError('warning_limit')
        # Point-in-time recheck like check: same policy, ledger and cited revisions.
        policy = policy.fresh()
        latest, _, _ = policy.read_note(ledger_path)
        if latest['sha256'] != info['sha256']:
            raise SSOTError('stale_base')
        for relative, mark in run['marks'].items():
            metadata = policy.note(relative).lstat()
            regular(metadata)
            if stamp(metadata) != mark:
                raise SSOTError('stale_source')
        policy.fresh()
        result = dict(path=ledger_path, source_sha256=info['sha256'], warnings=warnings, **counts,
                      ledger_table_only=table_only,
                      cited_notes=[run['cited'][key] for key in sorted(run['cited'])],
                      incomplete=sorted(run['incomplete']),
                      method='normalized_literal_presence_in_cited_note_body',
                      authority='warning_only_not_fact_verification')
        if primary_text_only:
            # The opt-in rule cannot leave out tables it could not find: name
            # those notes instead of failing open silently. No content.
            result['ledger_tables_unseparated'] = [dict(path=key, reason=run['unseparated'][key])
                                                   for key in sorted(run['unseparated'])]
        if normalize_periods:
            result.update(normalized_periods=period_rows,
                          period_convention=dict(fiscal_start=fiscal_start, scope=period.SCOPE))
        if row_references:
            result.update(row_references=references_rows,
                          audit_population=dict(population_id='ssot-support-rows', source_sha256=info['sha256'],
                                                items=[dict(id='%s:%d' % (ledger_path, row['line']), path=ledger_path,
                                                            line=row['line']) for row in references_rows]))
        if bind_subject:
            result.update(binding_observations=binding_rows, binding_warnings=binding_warnings)
        return result
    except SSOTError:
        raise
    except (state.StateError, EvidenceError, OSError, ValueError, UnicodeError, RuntimeError) as exc:
        raise SSOTError(str(exc) if isinstance(exc, state.StateError) else 'support_check_unavailable') from exc


def _period_forms(forms):
    """The markup-rendered forms among _forms(text, ...), which lists the form as written first.

    One form is also the rendered one, unless rendering left nothing (a body
    of markup only); then nothing is indexed. Markup between a period and its
    qualifier ('**2026년 3분기** 말에', '[[2026-Q3]] 초') never brings the
    whole period back through the form as written.
    """
    if len(forms) > 1:
        return forms[1:]
    return tuple(form for form in forms if _render(form, True).strip())


def _period_index(period, forms, fiscal_start):
    """vault_period index of the rendered forms; None when any form is longer than vault_period reads."""
    if any(len(form) > period.MAX_TEXT_CHARS for form in forms):
        return None
    return period.index_of(_period_forms(forms), fiscal_start=fiscal_start)


def _period_kept(period, entry, result, cited):
    """True when cited stays in via of result and result is not weaker than entry's normalized_match."""
    return (cited in result.get('via', ())
            and period.MATCHES.index(result['normalized_match']) <= period.MATCHES.index(entry['normalized_match']))


def _append(line, values):
    cells = _cells(line)
    position = cells['right'] if cells['right'] is not None else len(line)
    addition = ''.join('| ' + value + ' ' for value in values)
    return line[:position] + addition + line[position:] + ('|' if cells['right'] is None else '')


def _header(line, columns):
    cells = _cells(line)
    result, cursor = [], 0
    for (start, end), raw, field in zip(cells['spans'], cells['raw'], columns):
        result.append(line[cursor:start])
        if field in FIELDS:
            leading = raw[:len(raw) - len(raw.lstrip())]
            trailing = raw[len(raw.rstrip()):]
            result.append(leading + field + trailing)
        else:
            result.append(raw)
        cursor = end
    result.append(line[cursor:])
    return ''.join(result)


def _migration(raw, parsed):
    if not parsed['tables']:
        raise SSOTError('ledger_not_found')
    if parsed['diagnostics']:
        raise SSOTError('malformed_ledger')
    edits = []
    for table in parsed['tables']:
        missing = table['missing_fields']
        header = _append(_header(table['header']['text'], table['columns']), missing) if missing else _header(table['header']['text'], table['columns'])
        divider = _append(table['divider']['text'], ['---'] * len(missing)) if missing else table['divider']['text']
        replacement = header + table['header']['newline'] + divider + table['divider']['newline']
        for row in table['rows']:
            line = _append(row['raw']['text'], [''] * len(missing)) if missing else row['raw']['text']
            replacement += line + row['raw']['newline']
        if replacement.encode('utf-8') != raw[table['start']:table['end']]:
            edits.append(dict(start=table['start'], end=table['end'], replacement=replacement))
    candidate = raw
    for edit in reversed(edits):
        candidate = candidate[:edit['start']] + edit['replacement'].encode('utf-8') + candidate[edit['end']:]
    if len(candidate) > MAX_BYTES:
        raise SSOTError('output_byte_limit')
    converted = parse_ledger(candidate.decode('utf-8'))
    preserved = []
    for before, after in zip(parsed['tables'], converted['tables']):
        if len(before['rows']) != len(after['rows']) or after['schema_version'] != 2:
            raise SSOTError('migration_preservation_failed')
        for original, current in zip(before['rows'], after['rows']):
            if current['cells'][:len(original['cells'])] != original['cells']:
                raise SSOTError('migration_preservation_failed')
            preserved.append(dict(line=original['line'], fields=current['fields']))
    return candidate, edits, preserved


def prepare_migration(vault, path):
    """Return a deterministic read-only proposal; add blanks, never fact values."""
    try:
        policy = state._Policy(vault)
        info, _, raw = policy.read_note(path)
        parsed = parse_ledger(raw.decode('utf-8'))
        candidate, edits, preserved = _migration(raw, parsed)
        policy.fresh()
        latest, _, _ = policy.read_note(info['path'])
        if latest['sha256'] != info['sha256']:
            raise SSOTError('stale_base')
        proposal = dict(version=1, kind='ssot-v2-migration', status='prepared' if edits else 'unchanged',
                        path=info['path'], base_sha256=info['sha256'], config_sha256=policy.config_sha256,
                        candidate_sha256=_sha(candidate), edits=edits, preserved_rows=preserved,
                        warnings=check_ledger(candidate.decode('utf-8'), path=info['path']),
                        authority='proposal_only_not_approval_or_truth')
        proposal['proposal_sha256'] = _sha(_encode(proposal))
        if len(_encode(proposal)) > MAX_PROPOSAL_BYTES:
            raise SSOTError('proposal_byte_limit')
        # A final source read can race a policy rewrite. Close the generation
        # observation after that read without reading the newly denied source
        # again. This is a point-in-time check, not a filesystem transaction.
        policy.fresh()
        return proposal
    except SSOTError:
        raise
    except (state.StateError, EvidenceError, OSError, ValueError, UnicodeError, RuntimeError) as exc:
        raise SSOTError(str(exc) if isinstance(exc, state.StateError) else 'migration_unavailable') from exc


def apply_migration(vault, proposal, *, approve=False):
    """Apply only the recomputed migration to its exact current source revision."""
    if approve is not True:
        raise SSOTError('approval_required')
    if not isinstance(proposal, dict):
        raise SSOTError('invalid_proposal')
    try:
        encoded = _encode(proposal)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise SSOTError('invalid_proposal') from exc
    if len(encoded) > MAX_PROPOSAL_BYTES:
        raise SSOTError('proposal_byte_limit')
    path = proposal.get('path')
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise SSOTError('invalid_proposal')
    current = prepare_migration(vault, path)
    if proposal.get('base_sha256') != current['base_sha256']:
        raise SSOTError('stale_base')
    if proposal.get('config_sha256') != current['config_sha256']:
        raise SSOTError('configuration_changed')
    if encoded != _encode(current):
        raise SSOTError('proposal_changed')
    try:
        result = state.patch_note(vault, current['path'], current['base_sha256'], current['edits'],
                                  expected_config_sha256=current['config_sha256'])
        if result['after_sha256'] != current['candidate_sha256']:
            raise SSOTError('migration_preservation_failed')
        return dict(**result, proposal_sha256=current['proposal_sha256'],
                    authority='migration_accounting_not_fact_verification', warnings=current['warnings'])
    except state.StateError as exc:
        raise SSOTError(str(exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--path', required=True)
    check = commands.add_parser('check')
    check.add_argument('--path', required=True)
    support = commands.add_parser('check-support')
    support.add_argument('--path', required=True)
    support.add_argument('--primary-text-only', action='store_true',
                         help="count a value found only in other ledgers' fact tables as unsupported")
    support.add_argument('--row-references', action='store_true',
                         help='export content-free references and an audit population for every parsed row')
    support.add_argument('--bind-subject', action='store_true',
                         help='also diagnose subject/value presence on the same original physical source line')
    support.add_argument('--normalize-periods', action='store_true',
                         help='also report normalized_match of date/quarter/half/fiscal-year periods per row')
    support.add_argument('--fiscal-start', metavar='MM',
                         help='first month (1-12 or 01-12) of the fiscal year; requires --normalize-periods')
    apply = commands.add_parser('apply')
    apply.add_argument('--approve', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare_migration(args.vault, args.path)
        elif args.command == 'check':
            policy = state._Policy(args.vault)
            info, _, raw = policy.read_note(args.path)
            result = dict(path=info['path'], source_sha256=info['sha256'],
                          warnings=check_ledger(raw.decode('utf-8'), path=info['path']),
                          authority='warning_only_not_fact_verification')
            policy.fresh()
            latest, _, _ = policy.read_note(info['path'])
            if latest['sha256'] != info['sha256']:
                raise SSOTError('stale_base')
            policy.fresh()
        elif args.command == 'check-support':
            fiscal_start = args.fiscal_start
            if fiscal_start is not None:
                if not re.fullmatch(r'0?[1-9]|1[0-2]', fiscal_start):
                    raise SSOTError('invalid_opt_in')
                fiscal_start = int(fiscal_start)
            result = check_support(args.vault, args.path, primary_text_only=args.primary_text_only,
                                   normalize_periods=args.normalize_periods, fiscal_start=fiscal_start,
                                   row_references=args.row_references, bind_subject=args.bind_subject)
        else:
            raw = sys.stdin.buffer.read(MAX_PROPOSAL_BYTES + 1)
            if len(raw) > MAX_PROPOSAL_BYTES:
                raise SSOTError('proposal_byte_limit')
            result = apply_migration(args.vault, decode(raw), approve=args.approve)
        print(_encode(result).decode())
        return 0
    except (SSOTError, state.StateError, EvidenceError, ValueError, OSError, UnicodeError):
        print('{"error":"ssot_operation_unavailable"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
