#!/usr/bin/env python3
"""Mask explicitly revoked, source-bound spans and declared derivations.

This is a bounded declaration mechanism, not semantic inference or a promise
of complete forgetting. Undeclared aliases, exported copies and Git history
are outside its scope. All checks are point-in-time observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

from vault_evidence import EvidenceError, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import relative_parts, resolve_note_path


CONFIG_PATH = '00-meta/vault-config.json'
MAX_CONFIG_BYTES = 256 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_NODES = 128
MAX_EDGES = 256
MAX_SOURCE_FILES = 128
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BINDING_BYTES = 32 * 1024 * 1024
ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
SCOPE = 'explicit_source_bound_spans_and_declared_derivations_only'
ROOT_FIELDS = {'schema_version', 'nodes', 'derivations', 'revoked'}
NODE_FIELDS = {'id', 'path', 'start_line', 'end_line'}


class RevocationError(ValueError):
    """A fixed, non-sensitive failure code. Omit affected documents on error."""


def _encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _bounded(value):
    """Bound host objects before JSON encoding, including self-referential data."""
    pending = [(value, 0)]
    visited, characters = 0, 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if visited > 8192 or depth > 8:
            raise RevocationError('invalid_registry')
        if type(item) is str:
            characters += len(item)
            if characters > MAX_JSON_BYTES:
                raise RevocationError('registry_too_large')
        elif type(item) is dict:
            if len(item) > 8:
                raise RevocationError('invalid_registry')
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            if len(item) > MAX_EDGES:
                raise RevocationError('invalid_registry')
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (bool, int):
            raise RevocationError('invalid_registry')
        elif type(item) is int and item.bit_length() > 64:
            raise RevocationError('invalid_registry')
    try:
        if len(_encode(value)) > MAX_JSON_BYTES:
            raise RevocationError('registry_too_large')
    except (UnicodeError, TypeError, RecursionError, OverflowError) as exc:
        raise RevocationError('invalid_registry') from exc


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RevocationError('duplicate_json_key')
        result[key] = value
    return result


def _invalid_number(_value):
    raise RevocationError('invalid_json_number')


def _decode(data):
    if len(data) > MAX_JSON_BYTES:
        raise RevocationError('registry_too_large')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=_invalid_number, parse_float=_invalid_number)
        _bounded(value)
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise RevocationError('invalid_registry_json') from exc


def _path_text(value):
    if (type(value) is not str or len(value) > 1024
            or any(unicodedata.category(char).startswith('C') or char in '\u2028\u2029'
                   for char in value)):
        raise RevocationError('unsafe_path')
    try:
        return '/'.join(relative_parts(value, 'path'))
    except ValueError as exc:
        raise RevocationError('unsafe_path') from exc


class _Reader:
    def __init__(self, vault, config):
        self.vault = vault
        self.bytes_read = 0
        self.source_attempts = set()
        try:
            if not isinstance(vault, Path):
                raise RevocationError('invalid_configuration')
            self.root = vault.resolve(strict=True)
            rules = []
            for key in ('deny_zones', 'exclude_dirs'):
                values = config.get(key, [])
                if type(values) not in (list, tuple) or len(values) > 1024:
                    raise RevocationError('invalid_configuration')
                rules.extend(_path_text(value) for value in values)
            self.rules = (*rules, '.git')
        except (OSError, ValueError, RuntimeError) as exc:
            raise RevocationError('invalid_configuration') from exc

    def path(self, relative, suffix):
        relative = _path_text(relative)
        try:
            path = resolve_note_path(self.vault, relative, self.rules)
            if path.suffix.casefold() != suffix:
                raise RevocationError('unsafe_path')
            return path
        except (OSError, ValueError, RuntimeError) as exc:
            raise RevocationError('unsafe_path') from exc

    def canonical(self, relative, suffix='.md'):
        return self.path(relative, suffix).relative_to(self.root).as_posix()

    def read(self, relative, suffix='.md', *, contents=True, limit=MAX_FILE_BYTES):
        if suffix == '.md':
            # Resolve aliases on Windows, while retaining genuinely distinct
            # case-sensitive filenames on POSIX.
            key = self.canonical(relative)
            if key not in self.source_attempts and len(self.source_attempts) >= MAX_SOURCE_FILES:
                raise RevocationError('source_file_limit')
            self.source_attempts.add(key)
        remaining = MAX_BINDING_BYTES - self.bytes_read
        if remaining <= 0:
            raise RevocationError('binding_byte_limit')
        try:
            info, mark, data = stable_read(lambda: self.path(relative, suffix),
                                           min(limit, remaining), contents=contents)
            self.bytes_read += info['size']
            return info, mark, data
        except (OSError, ValueError, EvidenceError, RuntimeError) as exc:
            raise RevocationError('unsafe_or_changed_input') from exc

    def sweep(self, records):
        try:
            for relative, (_info, expected, suffix) in records.items():
                metadata = self.path(relative, suffix).lstat()
                regular(metadata)
                if stamp(metadata) != expected:
                    raise RevocationError('input_changed')
        except (OSError, ValueError, EvidenceError, RuntimeError) as exc:
            raise RevocationError('input_changed') from exc


def _schema(value, reader, *, captured):
    _bounded(value)
    if (type(value) is not dict or set(value) != ROOT_FIELDS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['nodes']) is not list or len(value['nodes']) > MAX_NODES
            or type(value['derivations']) is not list or len(value['derivations']) > MAX_EDGES
            or type(value['revoked']) is not list or len(value['revoked']) > MAX_NODES):
        raise RevocationError('invalid_registry_schema')
    ids, folded_ids, paths, hashes, nodes = set(), set(), {}, {}, []
    fields = NODE_FIELDS | ({'sha256'} if captured else set())
    for node in value['nodes']:
        if type(node) is not dict or set(node) != fields:
            raise RevocationError('invalid_node')
        node_id = node['id']
        if (type(node_id) is not str or not ID_RE.fullmatch(node_id)
                or node_id.casefold() in folded_ids):
            raise RevocationError('invalid_or_duplicate_node_id')
        ids.add(node_id)
        folded_ids.add(node_id.casefold())
        start, end = node['start_line'], node['end_line']
        if (type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= MAX_FILE_BYTES):
            raise RevocationError('invalid_line_span')
        path = reader.canonical(node['path'])
        spelling = _path_text(node['path'])
        key = path.casefold()
        if key in paths and paths[key] != spelling:
            raise RevocationError('case_aliased_path')
        paths[key] = spelling
        result = {**node, 'path': path}
        if captured:
            digest = node['sha256']
            if type(digest) is not str or not HASH_RE.fullmatch(digest):
                raise RevocationError('invalid_source_hash')
            if key in hashes and hashes[key] != digest:
                raise RevocationError('inconsistent_source_hash')
            hashes[key] = digest
        nodes.append(result)
    edges, seen = [], set()
    for edge in value['derivations']:
        if (type(edge) is not dict or set(edge) != {'parent', 'child'}
                or type(edge['parent']) is not str or type(edge['child']) is not str
                or edge['parent'] not in ids or edge['child'] not in ids):
            raise RevocationError('invalid_derivation')
        pair = edge['parent'], edge['child']
        if pair in seen:
            raise RevocationError('duplicate_derivation')
        seen.add(pair)
        edges.append(dict(edge))
    revoked, seen = [], set()
    for node_id in value['revoked']:
        if type(node_id) is not str or node_id not in ids or node_id in seen:
            raise RevocationError('invalid_revoked_id')
        seen.add(node_id)
        revoked.append(node_id)
    return {'schema_version': 1, 'nodes': nodes, 'derivations': edges, 'revoked': revoked}


def _lines(data):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise RevocationError('invalid_source_utf8') from exc
    if '\r' in text.replace('\r\n', ''):
        raise RevocationError('unsupported_line_endings')
    # Only LF is a line boundary. Unicode separators stay within original lines.
    lines = text.split('\n')
    result = [line + '\n' for line in lines[:-1]]
    if lines[-1]:
        result.append(lines[-1])
    return result


def _validate_bounds(nodes, lines):
    if any(node['end_line'] > len(lines) for node in nodes):
        raise RevocationError('line_span_out_of_bounds')


def _verify_sources(reader, nodes):
    grouped, records = {}, {}
    for node in nodes:
        grouped.setdefault(node['path'], []).append(node)
    for relative, bindings in grouped.items():
        info, mark, data = reader.read(relative)
        if info['sha256'] != bindings[0]['sha256']:
            raise RevocationError('stale_source_binding')
        _validate_bounds(bindings, _lines(data))
        records[relative] = (info, mark, '.md')
    reader.sweep(records)
    return records


def _config(vault):
    try:
        info, mark, data = stable_read(
            lambda: resolve_note_path(vault, CONFIG_PATH, ('.git',)),
            MAX_CONFIG_BYTES, contents=True)
        # Config uses its established larger bound, but shares duplicate checks.
        raw = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                         parse_constant=_invalid_number)
        config = validate_config(raw)
        reader = _Reader(vault, config)
        current, current_mark, _ = reader.read(CONFIG_PATH, '.json', limit=MAX_CONFIG_BYTES)
        if current != info or current_mark != mark:
            raise RevocationError('configuration_changed')
        return config, reader, (info, mark, '.json')
    except (OSError, ValueError, UnicodeError, EvidenceError, HealthcheckError,
            RecursionError, RuntimeError) as exc:
        raise RevocationError('invalid_configuration') from exc


class RevocationPolicy:
    """Request-scoped filter. Call recheck immediately before emitting results."""

    def __init__(self, vault: Path, config: dict):
        self.enabled = False
        self.registry_sha256 = None
        self.affected_paths = set()
        self.revoked_ids = set()
        self._nodes, self._spans, self._sources = [], {}, {}
        self._edge_count = 0
        self._vault, self._config = vault, config
        if type(config) is not dict:
            raise RevocationError('invalid_configuration')
        relative = config.get('memory_revocation_file', '')
        if type(relative) is not str:
            raise RevocationError('invalid_registry_path')
        if relative == '':
            return
        self.enabled = True
        self._reader = _Reader(vault, config)
        self._registry_path = self._reader.canonical(relative, '.json')
        info, mark, data = self._reader.read(self._registry_path, '.json', limit=MAX_JSON_BYTES)
        self._registry_info, self._registry_mark = info, mark
        self.registry_sha256 = info['sha256']
        registry = _schema(_decode(data), self._reader, captured=True)
        self._nodes = registry['nodes']
        self._edge_count = len(registry['derivations'])
        adjacency = {node['id']: [] for node in self._nodes}
        for edge in registry['derivations']:
            adjacency[edge['parent']].append(edge['child'])
        pending = list(registry['revoked'])
        while pending:
            node_id = pending.pop()
            if node_id not in self.revoked_ids:
                self.revoked_ids.add(node_id)
                pending.extend(adjacency[node_id])
        for node in self._nodes:
            if node['id'] in self.revoked_ids:
                self._spans.setdefault(node['path'], []).append(node)
        self.affected_paths = set(self._spans)
        self._registry_fresh()

    def _registry_fresh(self):
        info, mark, _ = self._reader.read(self._registry_path, '.json', contents=False,
                                           limit=MAX_JSON_BYTES)
        if info != self._registry_info or mark != self._registry_mark:
            raise RevocationError('registry_changed')

    def _source(self, relative):
        info, mark, data = self._reader.read(relative)
        return info, mark, data

    def _mask(self, relative, info, mark, data):
        nodes = self._spans[relative]
        if info['sha256'] != nodes[0]['sha256']:
            raise RevocationError('stale_source_binding')
        lines = _lines(data)
        _validate_bounds(nodes, lines)
        masked = [False] * len(lines)
        for node in nodes:
            for index in range(node['start_line'] - 1, node['end_line']):
                masked[index] = True
        output = ''.join(('\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else '')
                         if masked[index] else line for index, line in enumerate(lines))
        self._sources[relative] = (info, mark, '.md')
        self._registry_fresh()
        self._reader.sweep(self._sources)
        return output

    def filter_text(self, relative: str, text: str) -> str:
        if not self.enabled:
            return text
        canonical = self._reader.canonical(relative)
        if canonical not in self._spans:
            return text
        self._registry_fresh()
        if type(text) is not str or len(text) > MAX_FILE_BYTES:
            raise RevocationError('invalid_source_text')
        try:
            encoded = text.encode('utf-8')
        except UnicodeError as exc:
            raise RevocationError('invalid_source_utf8') from exc
        if len(encoded) > MAX_FILE_BYTES:
            raise RevocationError('input_too_large')
        info, mark, data = self._source(canonical)
        if data != encoded:
            raise RevocationError('source_text_mismatch')
        return self._mask(canonical, info, mark, data)

    def filter_path(self, relative: str) -> str:
        if not self.enabled:
            reader = _Reader(self._vault, self._config)
            _info, _mark, data = reader.read(relative)
            return ''.join(_lines(data))
        canonical = self._reader.canonical(relative)
        self._registry_fresh()
        info, mark, data = self._source(canonical)
        if canonical in self._spans:
            return self._mask(canonical, info, mark, data)
        self._sources[canonical] = (info, mark, '.md')
        return ''.join(_lines(data))

    def recheck(self) -> None:
        if not self.enabled:
            return
        self._registry_fresh()
        marks = {}
        for relative, (expected, _mark, suffix) in self._sources.items():
            info, mark, _ = self._reader.read(relative, suffix, contents=False)
            if info != expected:
                raise RevocationError('source_changed')
            marks[relative] = (info, mark, suffix)
        self._reader.sweep(marks)
        self._registry_fresh()
        self._reader.sweep(marks)

    def summary(self) -> dict:
        return {'enabled': self.enabled, 'registry_sha256': self.registry_sha256,
                'node_count': len(self._nodes), 'derivation_count': self._edge_count,
                'revoked_count': len(self.revoked_ids), 'revoked_ids': sorted(self.revoked_ids),
                'affected_path_count': len(self.affected_paths), 'scope': SCOPE}


def capture_registry(vault: Path, declarations: dict) -> dict:
    """Return a verified source-bound registry without writing any file."""
    _config_value, reader, config_record = _config(vault)
    registry = _schema(declarations, reader, captured=False)
    grouped = {}
    for node in registry['nodes']:
        grouped.setdefault(node['path'], []).append(node)
    records = {CONFIG_PATH: config_record}
    # Reserve half of the binding budget for complete final hash rechecks.
    for relative, nodes in grouped.items():
        if reader.bytes_read >= MAX_BINDING_BYTES // 2:
            raise RevocationError('binding_byte_limit')
        info, mark, data = reader.read(relative, limit=min(
            MAX_FILE_BYTES, MAX_BINDING_BYTES // 2 - reader.bytes_read))
        _validate_bounds(nodes, _lines(data))
        for node in nodes:
            node['sha256'] = info['sha256']
        records[relative] = (info, mark, '.md')
    _bounded(registry)
    for relative, (expected, _mark, suffix) in records.items():
        info, mark, _ = reader.read(relative, suffix, contents=False,
                                    limit=MAX_CONFIG_BYTES if relative == CONFIG_PATH else MAX_FILE_BYTES)
        if info != expected:
            raise RevocationError('input_changed')
        records[relative] = (info, mark, suffix)
    reader.sweep(records)
    return registry


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise RevocationError('invalid_arguments')


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('--vault', required=True, type=Path)
        parser.add_argument('command', choices=('capture', 'check'))
        parser.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        config, reader, config_record = _config(args.vault)
        relative = reader.canonical(args.input, '.json')
        input_info, input_mark, data = reader.read(relative, '.json', limit=MAX_JSON_BYTES)
        if args.command == 'capture':
            result = capture_registry(args.vault, _decode(data))
        else:
            policy = RevocationPolicy(args.vault, {**config, 'memory_revocation_file': relative})
            policy._sources = _verify_sources(policy._reader, policy._nodes)
            result = policy.summary()
        current_info, current_mark, _ = reader.read(relative, '.json', contents=False, limit=MAX_JSON_BYTES)
        if current_info != input_info or current_mark != input_mark:
            raise RevocationError('input_changed')
        input_records = {CONFIG_PATH: config_record,
                         relative: (current_info, current_mark, '.json')}
        reader.sweep(input_records)
        # Input/config checks must not make previously checked sources stale.
        # CLI readers each have a cumulative bound; capture uses two readers
        # and therefore at most twice MAX_BINDING_BYTES over the whole command.
        if args.command == 'capture':
            source_records = _verify_sources(reader, result['nodes'])
            reader.sweep({**input_records, **source_records})
        else:
            policy.recheck()
            policy._reader.sweep({**input_records, **policy._sources})
        sys.stdout.write(_encode(result).decode('ascii') + '\n')
        return 0
    except (RevocationError, OSError, ValueError, EvidenceError, HealthcheckError,
            UnicodeError, RecursionError, RuntimeError):
        # Never echo raw paths, JSON, payloads or exception messages.
        sys.stdout.write('{"error":"revocation_validation_failed"}\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
