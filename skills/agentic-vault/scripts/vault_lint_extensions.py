#!/usr/bin/env python3
"""Warning-only, bounded Markdown checks over caller-approved note originals.

Public interface: analyze_notes(path_to_text, config) -> list of warning mappings.
Every mapping has issue_id, code, severity, path, line (1-based), message, details.
make_warning is also public for provenance/ledger/egress extensions. No function
in this module reads files, resolves aliases, invents destinations or grants trust.
Callers must apply source policy before reading; this module filters supplied
denied/excluded paths again. Diagnostic strings and candidate lists are bounded.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import posixpath
import re
import unicodedata

WARNING_CODES = frozenset({'missing-anchor', 'ambiguous-link', 'missing-link-path',
                           'invalid-date', 'lint-incomplete'})
PROMOTABLE_CODES = WARNING_CODES - {'lint-incomplete'}
MAX_MESSAGE = 512
MAX_DETAIL_STRING = 256
MAX_DETAIL_ITEMS = 16
MAX_NOTE_CHARS = 1_000_000
MAX_TOTAL_CHARS = 8_000_000
MAX_NOTES = 10_000
MAX_WARNINGS = 200


def _bounded(value, depth=0):
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING]
    if depth >= 3:
        return str(value)[:MAX_DETAIL_STRING]
    if isinstance(value, dict):
        return {str(k)[:80]: _bounded(v, depth + 1)
                for k, v in list(value.items())[:MAX_DETAIL_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [_bounded(v, depth + 1) for v in value[:MAX_DETAIL_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_DETAIL_STRING]


def make_warning(code: str, path: str, line: int, message: str, *, details=None) -> dict:
    """Build a reusable bounded diagnostic; identity binds the full input facts."""
    facts = {'code': code, 'path': path, 'line': line, 'details': details or {}}
    digest = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'issue_id': 'lint-' + digest[:20], 'code': code, 'severity': 'warning',
            'path': path[:MAX_DETAIL_STRING], 'line': line,
            'message': message[:MAX_MESSAGE], 'details': _bounded(details or {})}


def _limit(config, key, default):
    value = config.get(key, default)
    return min(value, default) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _denied(path, config):
    folded = path.casefold()
    runtime = '00-meta/.agentic-vault/runtime'
    if folded == runtime or folded.startswith(runtime + '/'):
        return True
    parts = folded.split('/')
    for zone in config.get('deny_zones', []) or []:
        zone = str(zone).replace('\\', '/').strip('/').casefold()
        if zone and ((('/' in zone) and (folded == zone or folded.startswith(zone + '/')))
                     or ('/' not in zone and zone in parts)):
            return True
    return any(str(x).casefold() in parts for x in config.get('exclude_dirs', []) or [])


def _valid_path(path):
    return isinstance(path, str) and path and not path.startswith(('/', '\\')) and ':' not in path and '\\' not in path and all(
        p not in ('', '.', '..') for p in path.split('/'))


def _clean_lines(text):
    """Remove fenced/comments while retaining source line numbers and heading text."""
    result = []
    fence = None
    comment = False
    for raw in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', raw)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            result.append('')
            continue
        if marker and not (marker[1][0] == '`' and '`' in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            result.append('')
            continue
        visible = ''
        offset = 0
        while offset < len(raw):
            token = '-->' if comment else '<!--'
            index = raw.find(token, offset)
            if index < 0:
                if not comment:
                    visible += raw[offset:]
                break
            if not comment:
                visible += raw[offset:index]
            comment = not comment
            offset = index + len(token)
        result.append(visible)
    return result


def _heading_key(text):
    text = re.sub(r'\\([\\|#*_`])', r'\1', text)
    text = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', text)
    text = text.replace('`', '')
    text = re.sub(r'(\*\*|__|\*|_)(.*?)\1', r'\2', text)
    return ' '.join(unicodedata.normalize('NFC', text).split()).casefold()


def _frontmatter_end(lines):
    if not lines or lines[0].lstrip('\ufeff').strip() != '---':
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() in ('---', '...'):
            return i + 1
    return 0


def _anchors(lines, start):
    headings, blocks = set(), set()
    for i in range(start, len(lines)):
        line = lines[i]
        match = re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*$', line)
        if match:
            headings.add(_heading_key(re.sub(r'\s+#+\s*$', '', match[1])))
        elif i > start and re.match(r'^ {0,3}(?:=+|-+)\s*$', line) and lines[i - 1].strip():
            headings.add(_heading_key(lines[i - 1].strip()))
        block = re.search(r'(?:^|\s)\^([^\s^]+)\s*$', line)
        if block:
            blocks.add(unicodedata.normalize('NFC', block[1]))
    return headings, blocks


def _link_parts(raw, table_context=False):
    # Split only unescaped display pipes, including table-escaped wiki links.
    # In Markdown table rows the display separator itself must be escaped.
    separator = r'\\?\|' if table_context else r'(?<!\\)\|'
    raw = re.split(separator, raw, maxsplit=1)[0].replace('\\|', '|').strip()
    target, separator, anchor = raw.partition('#')
    return target.strip(), anchor.strip() if separator else None


def _destination_path(target, source):
    """Use one root/source-relative normalization for policy and identity lookup."""
    if target.startswith(('./', '../')):
        return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    return posixpath.normpath(target)


def _resolve(target, source, notes, stems, qualified):
    if not target:
        return [source]
    name = target[:-3] if target.lower().endswith('.md') else target
    if '/' not in name and not name.startswith('.'):
        return stems.get(name, [])
    if name.startswith('/') or ':' in name or '\\' in name:
        return []
    path = _destination_path(target, source)
    if not _valid_path(path):
        return []
    if path in notes:
        return [path]  # Preserve exact existing extension identity when explicit.
    base = path[:-3] if path.lower().endswith('.md') else path
    return qualified.get(base, [])


def _date_warnings(path, lines, end, config):
    exemptions = config.get('frontmatter_exempt_paths', config.get('fm_exempt_zones', [])) or []
    if any(path == zone or path.startswith(str(zone).rstrip('/') + '/') for zone in exemptions):
        return []
    roots = config.get('frontmatter_roots')
    if roots is not None and not any(path == zone or path.startswith(zone.rstrip('/') + '/') for zone in roots):
        return []
    warnings = []
    for i in range(1, end - 1):
        match = re.match(r'^(created|updated):\s*(.*?)\s*$', lines[i])
        if not match:
            continue
        scalar = re.split(r'\s+#', match[2], maxsplit=1)[0].strip()
        if len(scalar) >= 2 and scalar[0] in ('"', "'") and scalar[-1] == scalar[0]:
            scalar = scalar[1:-1]
        valid = bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', scalar))
        if valid:
            try:
                date.fromisoformat(scalar)
            except ValueError:
                valid = False
        if not valid:
            warnings.append(make_warning('invalid-date', path, i + 1,
                            f'{match[1]} must be a nonempty calendar date (YYYY-MM-DD)',
                            details={'field': match[1], 'value': scalar}))
    return warnings


def analyze_notes(notes: dict[str, str], config: dict, *, consumer_paths: set[str] | None = None) -> list[dict]:
    """Analyze bounded approved originals; all new findings remain warnings.

    Bare duplicate stems abstain with candidates. Qualified paths match exactly;
    ./ and ../ are relative to the referring note, never a basename fallback.
    Missing bare names remain the responsibility of the legacy dead-link check.
    Denied target paths are not resolved or described. Limits emit lint-incomplete
    and incomplete analysis can never serve as promotion evidence.
    """
    if not isinstance(notes, dict) or not isinstance(config, dict):
        raise ValueError('notes and config must be mappings')
    approved = {}
    incomplete = []
    total = 0
    for path in sorted(notes):
        if not _valid_path(path) or _denied(path, config):
            continue
        text = notes[path]
        if not isinstance(text, str):
            incomplete.append('non-text note')
            continue
        if len(approved) >= MAX_NOTES or len(text) > MAX_NOTE_CHARS or total + len(text) > MAX_TOTAL_CHARS:
            incomplete.append('note input limit')
            continue
        approved[path] = text
        total += len(text)
    stems, qualified = {}, {}
    cleaned, anchors, fm_ends = {}, {}, {}
    for path, text in approved.items():
        stem = path.rsplit('/', 1)[-1]
        stem = stem[:-3] if stem.lower().endswith('.md') else stem
        stems.setdefault(stem, []).append(path)
        qualified.setdefault(path[:-3] if path.lower().endswith('.md') else path, []).append(path)
        lines = _clean_lines(text)
        cleaned[path] = lines
        fm_ends[path] = _frontmatter_end(lines)
        anchors[path] = _anchors(lines, fm_ends[path])
    results = []
    cap = _limit(config, 'lint_max_warnings', MAX_WARNINGS)
    for path, lines in cleaned.items():
        if consumer_paths is not None and path not in consumer_paths:
            continue
        results.extend(_date_warnings(path, lines, fm_ends[path], config))
        for line_number, line in enumerate(lines, 1):
            line = re.sub(r'(`+).*?\1', '', line)
            for match in re.finditer(r'(?<!!)\[\[([^]\n]+)\]\]|!\[\[([^]\n]+)\]\]', line):
                raw = match[1] if match[1] is not None else match[2]
                target, anchor = _link_parts(raw, line.lstrip().startswith('|'))
                if re.search(r'\.[^./]+$', target) and not target.lower().endswith('.md'):
                    continue
                policy_target = _destination_path(target, path)
                if _denied(policy_target, config):
                    continue
                destinations = _resolve(target, path, approved, stems, qualified)
                if len(destinations) > 1:
                    results.append(make_warning('ambiguous-link', path, line_number,
                                   'Unqualified wiki link has multiple allowed destinations; no destination selected',
                                   details={'target': target, 'candidates': destinations,
                                            'candidate_count': len(destinations)}))
                elif not destinations:
                    if '/' in target and not incomplete:
                        results.append(make_warning('missing-link-path', path, line_number,
                                       'Qualified wiki link does not name an allowed relative note path',
                                       details={'target': target}))
                elif anchor is not None:
                    destination = destinations[0]
                    headings, blocks = anchors[destination]
                    valid = (unicodedata.normalize('NFC', anchor[1:]) in blocks) if anchor.startswith('^') else (_heading_key(anchor) in headings)
                    if not valid:
                        results.append(make_warning('missing-anchor', path, line_number,
                                       'Wiki link heading or block anchor is absent from the resolved note',
                                       details={'target': destination, 'anchor': anchor,
                                                'anchor_kind': 'block' if anchor.startswith('^') else 'heading'}))
            if len(results) > cap:
                incomplete.append('warning output limit')
                break
        if len(results) > cap:
            break
    results.sort(key=lambda x: (x['path'], x['line'], x['code'], x['issue_id']))
    if len(results) > cap:
        incomplete.append('warning output limit')
    results = results[:cap]
    if incomplete:
        results.append(make_warning('lint-incomplete', '', 0,
                       'Warning analysis is incomplete; omitted input or diagnostics cannot support promotion',
                       details={'reasons': sorted(set(incomplete))}))
    return results
