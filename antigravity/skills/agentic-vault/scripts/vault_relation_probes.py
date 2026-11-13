#!/usr/bin/env python3
"""Bounded, authored body-record preservation observations (standard library only).

An anchor is a literal locator, never a regular expression or semantic claim.
The caller owns scope selection. No records or anchors are copied into results.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_PROBES = 64
MAX_ANCHOR_CHARS = 256
MAX_TEXT_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
YAML_KEY_RE = re.compile(r'[\w-]+:')
RELATION_SCOPE = ('authored literal anchors select exactly one body line or blank-line-delimited paragraph; '
                  'compare whole records after NFC and CRLF/CR-to-LF normalization; preserve all other '
                  'whitespace and case; frontmatter and leading BOM excluded; body-relative 1-based line '
                  'locators; scoped lexical observation only, not semantic truth or edit permission')


class RelationProbeError(ValueError):
    """Safe validation code, never containing a supplied anchor or record."""


def validate_relation_probes(probes, *, with_paths=False):
    """Validate an explicit nonempty list without reading any files."""
    if not isinstance(probes, list) or not 1 <= len(probes) <= MAX_PROBES:
        raise RelationProbeError('invalid_relation_probes')
    fields = {'id', 'anchor', 'scope'} | ({'source_path', 'target_path'} if with_paths else set())
    seen = set()
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) != fields:
            raise RelationProbeError('invalid_relation_probe')
        identifier, anchor, scope = probe['id'], probe['anchor'], probe['scope']
        if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier) or identifier in seen:
            raise RelationProbeError('invalid_relation_probe_id')
        seen.add(identifier)
        if (not isinstance(anchor, str) or not anchor.strip() or len(anchor) > MAX_ANCHOR_CHARS
                or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp') for char in anchor)):
            raise RelationProbeError('invalid_relation_anchor')
        if not isinstance(scope, str) or scope not in ('line', 'paragraph'):
            raise RelationProbeError('invalid_relation_scope')
        if with_paths and any(not isinstance(probe[key], str) or not probe[key]
                              for key in ('source_path', 'target_path')):
            raise RelationProbeError('invalid_relation_path')
    return probes


def parse_relation_manifest(value):
    """Decode the schema1 object after a caller's bounded, policy-safe JSON read."""
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'probes'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise RelationProbeError('invalid_relation_manifest')
    return validate_relation_probes(value['probes'])


def _body(text):
    # Match the existing preservation check's frontmatter boundary, including
    # the schema-accepted malformed closing line. Keep body whitespace intact.
    text = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    if lines and lines[0].strip() == '---':
        for end, line in enumerate(lines[1:65], 1):
            if line.strip() == '---':
                return '\n'.join(lines[end + 1:])
            if line.startswith('---') and YAML_KEY_RE.match(next(filter(str.strip, lines[1:end]), '')):
                return '\n'.join([line[3:], *lines[end + 1:]])
    return text


def _records(body, scope):
    lines = body.split('\n')
    if scope == 'line':
        return [(line, {'start_line': index, 'end_line': index})
                for index, line in enumerate(lines, 1) if line.strip()]
    records, pending, start = [], [], 0
    for index, line in enumerate(lines, 1):
        if line.strip():
            if not pending:
                start = index
            pending.append(line)
        elif pending:
            records.append(('\n'.join(pending), {'start_line': start, 'end_line': index - 1}))
            pending = []
    if pending:
        records.append(('\n'.join(pending), {'start_line': start, 'end_line': len(lines)}))
    return records


def _digest(record):
    return hashlib.sha256(record.encode('utf-8')).hexdigest()


def check_relation_records(original: str, candidate: str, probes: list[dict]) -> dict:
    """Compare source-bound selected records; an invalid source raises safely.

    Candidate absence, ambiguity and any canonical record change require review.
    Repeated occurrences of an anchor inside one record still select one record.
    Empty opt-in lists are invalid; callers preserve legacy behavior with None.
    """
    validate_relation_probes(probes)
    for text in (original, candidate):
        if not isinstance(text, str):
            raise RelationProbeError('invalid_text')
        try:
            size = len(text.encode('utf-8'))
        except UnicodeError:
            raise RelationProbeError('invalid_text') from None
        if size > MAX_TEXT_BYTES:
            raise RelationProbeError('input_byte_limit')
    bodies = [unicodedata.normalize('NFC', _body(text)) for text in (original, candidate)]
    records = {(side, scope): _records(body, scope) for side, body in enumerate(bodies)
               for scope in {probe['scope'] for probe in probes}}
    rows = []
    for probe in probes:
        anchor, scope = unicodedata.normalize('NFC', probe['anchor']), probe['scope']
        before, after = [[record for record in records[(side, scope)] if anchor in record[0]]
                         for side in (0, 1)]
        if len(before) != 1:
            raise RelationProbeError('relation_source_missing' if not before else 'relation_source_ambiguous')
        reason = ('candidate_missing' if not after else 'candidate_ambiguous' if len(after) != 1
                  else 'record_changed' if before[0][0] != after[0][0] else None)
        rows.append({'id': probe['id'], 'scope': scope, 'original_count': len(before),
                     'candidate_count': len(after), 'original_sha256': _digest(before[0][0]),
                     'candidate_sha256': _digest(after[0][0]) if len(after) == 1 else None,
                     'original_locator': before[0][1], 'candidate_locator': after[0][1] if len(after) == 1 else None,
                     'preserved': reason is None, 'reason': reason})
    return {'status': 'needs_review' if any(not row['preserved'] for row in rows) else 'complete',
            'relation_rows': rows, 'relation_scope': RELATION_SCOPE}
