#!/usr/bin/env python3
"""Bounded temporal Markdown ledgers and explicit revision-bound v2 migration.

Intervals are half-open dates [valid_from, valid_until); blank upper bounds are
open, but missing/invalid lower bounds are unknown rather than evidence of a
contradiction. No dates, source authority, truth or supersession are inferred.
Legacy headers are aliases only; original fact cells and unrelated bytes remain
intact during migration. JSON proposals/receipts are accounting, not signatures
or evidence of approval. Only an explicit approve=True applies a migration.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys

import vault_state as state
from vault_evidence import EvidenceError, decode

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
            heading = re.match(r' {0,3}(#{1,6})\s+(.+?)\s*#*$', text)
            if heading:
                level, label = len(heading[1]), heading[2].strip().casefold()
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
