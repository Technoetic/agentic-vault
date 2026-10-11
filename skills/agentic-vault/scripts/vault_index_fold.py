#!/usr/bin/env python3
"""Preview literal index-row relocation from an explicit host-authored manifest.

No apply operation, note writes, directory scans, completion inference, or network
calls exist. Completion is a caller declaration, not verified task success.
Source/destination/reference hashes are required. Row quotes cover whole 1-based
LF-delimited lines, including CRLF and trailing spaces. Projected documents retain
their original bytes outside selected rows; the existing destination is never
created, overwritten or moved. Literal filename wiki links are checked only for
explicit row references, not anchors, rendered Markdown or the transitive graph.
Bindings are point-in-time observations, not an atomic filesystem snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from vault_evidence import EvidenceError, decode, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import relative_parts, resolve_note_path


CONFIG = '00-meta/vault-config.json'
MAX_JSON_BYTES = 256 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 64
MAX_READS = 128
MAX_ITEMS = 64
MAX_REFERENCES = 8
MAX_QUOTE_CHARS = 8192
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
BASE_DENY = ('.git', '.obsidian', '90-assets', '_archive', '_processed', '_completed',
             '.claude', '.codex', 'node_modules')
SECRET_NAMES = frozenset(('secrets', 'secrets.json', 'credentials.json', 'tokens.json',
                          'token.json', 'private-key.pem', 'service-account.json',
                          'settings.json', 'settings.local.json', 'memoryhub-mcp'))
SECRET_SEGMENTS = frozenset(('.ssh', '.aws', '.azure', '.gnupg', 'secrets', 'credentials'))
SECRET_SUFFIXES = frozenset(('.key', '.pem', '.p12', '.pfx', '.jks', '.kdbx'))
SECRET_BASENAMES = frozenset(('.npmrc', '.pypirc', '.netrc', '.dockercfg', '.git-credentials'))
SECRET_STEMS = frozenset(('secret', 'secrets', 'credential', 'credentials', 'token', 'tokens',
                         'service-account', 'service_account', 'api-key', 'api-keys',
                         'api_key', 'api_keys', 'auth-config', 'auth_config',
                         'secret-config', 'secret_config', 'secrets-config', 'secrets_config',
                         'credentials-config', 'credentials_config', 'token-config', 'token_config',
                         'private-key', 'private_key'))
HASH = re.compile(r'[0-9a-f]{64}')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
WIKI = re.compile(r'\[\[([^\[\]\r\n]+)\]\]')


def _require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def _secret(parts):
    """Reject known credential paths in every role before handing them to a reader."""
    return any(part.casefold() in SECRET_NAMES or part.casefold() in SECRET_SEGMENTS
               or part.casefold().startswith('.env') or part.casefold() in SECRET_BASENAMES
               or Path(part).stem.casefold() in SECRET_STEMS
               or Path(part).suffix.casefold() in SECRET_SUFFIXES for part in parts)


def _blank():
    return {'type': 'index_fold_preview', 'authority': 'preview_data_only',
            'status': 'incomplete', 'complete': False, 'apply_supported': False,
            'projected_index': None, 'archive_target': None, 'moved': [], 'retained': [],
            'reachability': [], 'bindings': [], 'issues': [],
            'limitations': ['host_declared_completion_unverified', 'literal_file_links_only',
                            'anchors_and_transitive_graph_unverified', 'point_in_time_bindings',
                            'no_apply_operation'],
            'diagnostics': {'reads': 0, 'bytes_read': 0,
                            'limits': {'manifest_bytes': MAX_JSON_BYTES, 'file_bytes': MAX_FILE_BYTES,
                                       'files': MAX_FILES, 'reads': MAX_READS, 'total_bytes': MAX_TOTAL_BYTES,
                                       'items': MAX_ITEMS, 'references_per_item': MAX_REFERENCES}}}


def _fail(result, reason):
    result.update(status='incomplete', complete=False, projected_index=None,
                  archive_target=None, moved=[], retained=[], reachability=[], bindings=[])
    result['issues'] = [{'reason': reason}]
    return result


def _bounded(manifest):
    pending = [(manifest, 0)];nodes = chars = 0
    while pending:
        value, depth = pending.pop();nodes += 1
        _require(nodes <= 8192 and depth <= 8, 'invalid_manifest')
        if isinstance(value, str):
            chars += len(value)
            _require(chars <= MAX_JSON_BYTES, 'manifest_too_large')
        elif isinstance(value, dict):
            _require(len(value) <= 16, 'invalid_manifest')
            pending.extend((part, depth+1) for pair in value.items() for part in pair)
        elif isinstance(value, list):
            _require(len(value) <= MAX_ITEMS, 'invalid_manifest')
            pending.extend((part, depth+1) for part in value)
        else:
            _require(value is None or type(value) in (bool, int, float), 'invalid_manifest')
    try:
        raw = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, OverflowError) as error:
        raise EvidenceError('invalid_manifest') from error
    _require(len(raw) <= MAX_JSON_BYTES, 'manifest_too_large')


def _fields(value, fields):
    _require(isinstance(value, dict) and set(value) == set(fields), 'invalid_manifest')


def _binding(value):
    _fields(value, ('path', 'expected_sha256'))
    _require(isinstance(value['path'], str) and 0 < len(value['path']) <= 1024, 'invalid_manifest')
    _require(isinstance(value['expected_sha256'], str) and HASH.fullmatch(value['expected_sha256']), 'invalid_manifest')


def _selection(value):
    _fields(value, ('start_line', 'end_line', 'quote'))
    _require(type(value['start_line']) is int and type(value['end_line']) is int
             and 1 <= value['start_line'] <= value['end_line'] <= MAX_FILE_BYTES, 'invalid_manifest')
    _require(isinstance(value['quote'], str) and 0 < len(value['quote']) <= MAX_QUOTE_CHARS
             and '\0' not in value['quote'], 'invalid_manifest')


def _validate(manifest):
    _bounded(manifest)
    _fields(manifest, ('version', 'source', 'destination', 'destination_link', 'items'))
    _require(type(manifest['version']) is int and manifest['version'] == 1, 'invalid_manifest')
    _binding(manifest['source']);_binding(manifest['destination'])
    _selection(manifest['destination_link'])
    _require(isinstance(manifest['items'], list) and len(manifest['items']) <= MAX_ITEMS, 'invalid_manifest')
    ids = set();ranges = []
    for item in manifest['items']:
        _fields(item, ('id', 'state', 'source', 'references'))
        _require(isinstance(item['id'], str) and ID.fullmatch(item['id']), 'invalid_manifest')
        _require(item['id'] not in ids, 'duplicate_item_id');ids.add(item['id'])
        _require(item['state'] in ('completed', 'active', 'pending'), 'invalid_manifest')
        _selection(item['source'])
        lower, upper = item['source']['start_line'], item['source']['end_line']
        _require(not any(lower <= right and left <= upper for left, right in ranges), 'overlapping_rows')
        ranges.append((lower, upper))
        _require(isinstance(item['references'], list) and len(item['references']) <= MAX_REFERENCES, 'invalid_manifest')
        for reference in item['references']:
            _binding(reference)


class _Reader:
    """Explicit bounded files, descriptor-safe reads and final hash/identity sweep."""
    def __init__(self, vault, result):
        self.vault = Path(vault);self.root = self.vault.resolve(strict=True)
        self.result = result;self.rules = BASE_DENY;self.records = {}
        config = self.capture(CONFIG, '.json', limit=MAX_JSON_BYTES)
        settings = validate_config(decode(config[2]))
        self.rules = (*BASE_DENY, *settings['deny_zones'], *settings['exclude_dirs'])
        self.path(CONFIG, '.json')

    def path(self, relative, suffix):
        try:
            parts = relative_parts(relative, 'fold path')
            _require(not _secret(parts), 'unsafe_path')
            path = resolve_note_path(self.vault, relative, self.rules)
            _require(path.suffix.casefold() == suffix, 'unsafe_path')
            return path
        except ValueError as error:
            raise EvidenceError('unsafe_path') from error

    def _read(self, relative, suffix, limit, contents):
        diagnostics = self.result['diagnostics']
        _require(diagnostics['reads'] < MAX_READS, 'read_count_limit')
        remaining = MAX_TOTAL_BYTES-diagnostics['bytes_read']
        _require(remaining > 0, 'total_byte_limit')
        diagnostics['reads'] += 1
        info, mark, raw = stable_read(lambda: self.path(relative, suffix),
                                      min(limit, remaining), contents=contents)
        diagnostics['bytes_read'] += info['size']
        return info, mark, raw

    def capture(self, relative, suffix='.md', *, limit=None, role=None):
        path = self.path(relative, suffix)
        canonical = path.relative_to(self.root).as_posix();key = canonical.casefold()
        if key in self.records:
            return self.records[key][1:]
        _require(len(self.records) < MAX_FILES, 'file_count_limit')
        try:
            info, mark, raw = self._read(canonical, suffix, MAX_FILE_BYTES if limit is None else limit, True)
        except FileNotFoundError as error:
            raise EvidenceError('missing_'+role if role else 'missing_input') from error
        _require(self.result['diagnostics']['bytes_read'] <= MAX_TOTAL_BYTES//2, 'total_byte_limit')
        record = ({'path': canonical, **info}, mark, raw)
        self.records[key] = (suffix, *record)
        return record

    def bound(self, binding, role):
        record = self.capture(binding['path'], role=role)
        _require(record[0]['sha256'] == binding['expected_sha256'], 'stale_'+role)
        return record

    def recheck(self):
        marks = {}
        for suffix, expected, _mark, _raw in self.records.values():
            limit = MAX_JSON_BYTES if suffix == '.json' else MAX_FILE_BYTES
            info, mark, _ = self._read(expected['path'], suffix, limit, False)
            _require({'path': expected['path'], **info} == expected,
                     'configuration_changed' if expected['path'] == CONFIG else 'file_changed_during_preview')
            marks[expected['path']] = (suffix, mark)
        for relative, (suffix, mark) in marks.items():
            metadata = self.path(relative, suffix).lstat();regular(metadata)
            _require(stamp(metadata) == mark, 'file_changed_during_preview')


def _lines(raw):
    # LF alone defines rows; CRLF/Unicode separators are literal original bytes.
    if not raw:
        return []
    pieces = raw.split(b'\n')
    return [part+b'\n' for part in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _quote(lines, selection, reason):
    start, end = selection['start_line'], selection['end_line']
    _require(end <= len(lines), reason)
    quote = b''.join(lines[start-1:end])
    _require(quote == selection['quote'].encode('utf-8'), reason)
    return quote


def _links(text):
    return {target.split('|', 1)[0].split('#', 1)[0].removesuffix('.md')
            for target in WIKI.findall(text)}


def _preview(reader, manifest, result):
    _validate(manifest)
    source = reader.bound(manifest['source'], 'source')
    destination = reader.bound(manifest['destination'], 'destination')
    _require(source[0]['path'].casefold() != destination[0]['path'].casefold(), 'same_source_destination')
    rows = _lines(source[2]);source[2].decode('utf-8');destination_text = destination[2].decode('utf-8')
    link = manifest['destination_link'];_quote(rows, link, 'destination_link_quote_mismatch')
    destination_name = Path(destination[0]['path']).stem
    _require(destination_name in _links(link['quote']), 'destination_not_linked')
    selected = sorted(manifest['items'], key=lambda item: item['source']['start_line'])
    removed = set();chunks = []
    for item in selected:
        selection = item['source'];quote = _quote(rows, selection, 'source_quote_mismatch')
        if item['state'] != 'completed':
            result['retained'].append({'id': item['id'], 'reason': 'not_completed'});continue
        start, end = selection['start_line'], selection['end_line']
        _require(not (start <= link['end_line'] and link['start_line'] <= end), 'destination_link_removed')
        identifier = r'(?<![A-Za-z0-9_.:-])'+re.escape(item['id'])+r'(?![A-Za-z0-9_.:-])'
        _require(re.search(identifier, destination_text) is None and quote not in destination[2], 'destination_item_conflict')
        row_links = _links(selection['quote']);names = set();references = []
        for reference in item['references']:
            path = reader.path(reference['path'], '.md');name = path.stem
            _require(name in row_links and name not in names, 'ambiguous_or_unused_reference')
            names.add(name)
            bound = reader.bound(reference, 'reference');references.append(bound[0]['path'])
        _require(row_links == names, 'unresolved_row_link')
        removed.update(range(start-1, end));chunks.append(quote)
        result['moved'].append({'id': item['id'], 'start_line': start, 'end_line': end,
                                'quote': quote.decode('utf-8')})
        result['reachability'].append({'id': item['id'], 'via_destination': destination[0]['path'],
                                       'references': references})
    projected = b''.join(row for number, row in enumerate(rows) if number not in removed)
    appended = b''.join(chunks)
    separator = (b'\r\n' if b'\r\n' in destination[2] else b'\n') if appended and destination[2] and not destination[2].endswith(b'\n') else b''
    archive = destination[2]+separator+appended
    reader.recheck()
    result.update(status='preview', complete=True, issues=[],
                  projected_index={'path': source[0]['path'], 'original_sha256': source[0]['sha256'],
                                   'sha256': hashlib.sha256(projected).hexdigest(), 'content': projected.decode('utf-8')},
                  archive_target={'path': destination[0]['path'], 'original_sha256': destination[0]['sha256'],
                                  'projected_sha256': hashlib.sha256(archive).hexdigest(),
                                  'separator': separator.decode(), 'append': appended.decode('utf-8'),
                                  'projected_content': archive.decode('utf-8')},
                  bindings=[record[1] for record in reader.records.values()])
    _require(len(json.dumps(result, ensure_ascii=True).encode('ascii')) <= MAX_OUTPUT_BYTES, 'output_too_large')
    return result


def preview(vault, manifest):
    """Return a data-only preview; incomplete inputs never return projected docs."""
    result = _blank()
    try:
        _validate(manifest)
        return _preview(_Reader(vault, result), manifest, result)
    except EvidenceError as error:
        return _fail(result, str(error))
    except (OSError, ValueError, HealthcheckError, UnicodeError, RecursionError, OverflowError, TypeError):
        return _fail(result, 'invalid_or_unreadable_input')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EvidenceError('invalid_arguments')


def main(argv=None):
    result = _blank()
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('--vault', type=Path, required=True)
        parser.add_argument('--input', required=True, help='explicit vault-relative JSON manifest')
        args = parser.parse_args(argv)
        reader = _Reader(args.vault, result)
        _, _, raw = reader.capture(args.input, '.json', limit=MAX_JSON_BYTES)
        result = _preview(reader, decode(raw), result)
    except EvidenceError as error:
        _fail(result, str(error))
    except (OSError, ValueError, HealthcheckError, UnicodeError, RecursionError, OverflowError, TypeError):
        _fail(result, 'invalid_or_unreadable_input')
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
