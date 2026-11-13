#!/usr/bin/env python3
"""Optional host-authored ontology contracts bound to current Markdown bytes.

Bindings identify annotation provenance, never semantic truth, approval or
underlying answer data. Nothing is extracted, executed, transmitted or written.
Currentness is a point-in-time observation, not an OS-enforced file freeze.
"""
from __future__ import annotations

import math
from pathlib import Path
import re

from vault_evidence import EvidenceError, decode, digest, encode, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError, validate_config
from vault_memory import (_Policy, _proof, _text, CONFIG_PATH, MAX_FILE_BYTES,
                          MAX_JSON_BYTES, MAX_QUOTE_CHARS)
from vault_paths import relative_parts
from vault_revocation import RevocationPolicy


SCHEMA_VERSION = 1
REPORT_TYPE = 'ontology_contract_report'
MAX_RECORDS = 128
MAX_TYPES = 32
MAX_SOURCES = 4
MAX_TEXT = 8192
RUNTIME_PATH = '00-meta/.agentic-vault/runtime'
ID_RE = re.compile(r'[a-z][a-z0-9_.:-]{0,95}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
ROOT_FIELDS = {'schema_version', 'ontology_id', 'version', 'types', 'entities',
               'relations', 'links', 'metrics'}
REGISTRY_FIELDS = ROOT_FIELDS | {'type', 'authority', 'declaration',
                               'config_sha256', 'registry_sha256'}
RECORD_FIELDS = {
    'entities': {'id', 'type', 'label', 'aliases', 'review', 'sources'},
    'relations': {'id', 'label', 'domain', 'range', 'review', 'sources'},
    'links': {'id', 'subject', 'predicate', 'object', 'review', 'sources'},
    'metrics': {'id', 'label', 'aliases', 'definition', 'unit', 'grain',
                'population', 'aggregation', 'review', 'sources'},
}
FAILURES = (EvidenceError, HealthcheckError, OSError, ValueError, TypeError,
            KeyError, RecursionError, OverflowError, RuntimeError)


def unavailable(reason):
    """A content-free report; reason is a bounded machine code, not exception text."""
    if not isinstance(reason, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,95}', reason) is None:
        reason = 'invalid_or_unreadable_input'
    return {'type': REPORT_TYPE, 'schema_version': SCHEMA_VERSION,
            'authority': 'data_only', 'status': 'unavailable', 'reason': reason,
            'context': [], 'matches': [], 'gaps': []}


def bounded_json(value):
    """Bound primitive work before canonical serialization, including host objects."""
    pending = [(value, 0)]
    nodes, characters = 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 8192 or depth > 12:
            raise EvidenceError('invalid_json_bounds')
        if type(item) is str:
            characters += len(item)
            if characters > MAX_JSON_BYTES:
                raise EvidenceError('json_too_large')
        elif type(item) is dict:
            if len(item) > 32 or any(type(key) is not str for key in item):
                raise EvidenceError('invalid_json_bounds')
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            # A valid 32-metric plan can report one unknown-ID gap plus five
            # missing-slot gaps per metric. Record limits are checked below.
            if len(item) > 512:
                raise EvidenceError('invalid_json_bounds')
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            if item.bit_length() > 64:
                raise EvidenceError('invalid_json_bounds')
        elif type(item) is float:
            if not math.isfinite(item):
                raise EvidenceError('non_finite_json')
        elif item is not None and type(item) is not bool:
            raise EvidenceError('invalid_json_bounds')
    try:
        raw = encode(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise EvidenceError('invalid_json_bounds') from exc
    if len(raw) + 1 > MAX_JSON_BYTES:
        raise EvidenceError('json_too_large')
    return raw


def _safe_parts(relative):
    _text(relative, 1024)
    parts = relative_parts(relative, 'path')
    if tuple(part.casefold() for part in parts[:3]) == tuple(RUNTIME_PATH.split('/')):
        raise EvidenceError('engine_runtime_input')
    return parts


class _OntologyPolicy(_Policy):
    """Reuse evidence policy, with an unconditional runtime boundary everywhere."""

    def __init__(self, vault):
        # The shared policy initializes revocations with the loaded config. Add
        # the mandatory exclusion before that reader can open its registry or
        # any declared source, while retaining the actual raw config binding.
        self.vault = vault
        self.result = {'diagnostics': {'binding_bytes_read': 0}}
        self.root = vault.resolve(strict=True)
        self.config_info, self.config_mark, data = self._config()
        config = validate_config(decode(data))
        self.rules = (*(config.get('deny_zones') or ()),
                      *(config.get('exclude_dirs') or ()), '.git', RUNTIME_PATH)
        self.path(CONFIG_PATH, '.json')
        self.revocations = RevocationPolicy(vault, {
            **config, 'exclude_dirs': [*(config.get('exclude_dirs') or ()), RUNTIME_PATH]})
        self.sources = {}
        self.source_attempts = set()
        self.initial_bytes = 0
        self.input_binding = None
        self.declaration_mark = None

    def path(self, relative, suffix='.md'):
        _safe_parts(relative)
        return super().path(relative, suffix)

    def recheck(self):
        super().recheck()
        if self.input_binding is not None:
            relative, expected = self.input_binding
            info, mark, _ = stable_read(lambda: self.path(relative, '.json'), MAX_JSON_BYTES)
            if info != expected or mark != self.declaration_mark:
                raise EvidenceError('declaration_changed')
            # No subsequent read may leave this last binding silently stale.
            self.fresh()
            self.sweep({path: source[1] for path, source in self.sources.items()})
            metadata = self.path(relative, '.json').lstat()
            regular(metadata)
            if stamp(metadata) != mark:
                raise EvidenceError('declaration_changed')


def policy(vault):
    if not isinstance(vault, Path):
        raise EvidenceError('invalid_vault')
    return _OntologyPolicy(vault)


def _id(value):
    if type(value) is not str or ID_RE.fullmatch(value) is None:
        raise EvidenceError('invalid_ontology_id')
    return value


def _hash(value):
    if type(value) is not str or HASH_RE.fullmatch(value) is None:
        raise EvidenceError('invalid_binding')


def _binding(value, suffix, maximum):
    if type(value) is not dict or set(value) != {'path', 'sha256', 'size'}:
        raise EvidenceError('invalid_binding')
    _safe_parts(value['path'])
    if Path(value['path']).suffix.casefold() != suffix:
        raise EvidenceError('invalid_binding')
    _hash(value['sha256'])
    if type(value['size']) is not int or not 0 <= value['size'] <= maximum:
        raise EvidenceError('invalid_binding')


def _source_schema(source, captured):
    required = {'path', 'line', 'quote'}
    if captured:
        required |= {'binding', 'span_sha256'}
    if (type(source) is not dict or not required <= set(source)
            or not set(source) <= required | {'expected_sha256'}):
        raise EvidenceError('invalid_source_schema')
    _safe_parts(source['path'])
    if type(source['line']) is not int or not 1 <= source['line'] <= MAX_FILE_BYTES + 1:
        raise EvidenceError('invalid_source_schema')
    _text(source['quote'], MAX_QUOTE_CHARS, quote=True)
    if 'expected_sha256' in source:
        _hash(source['expected_sha256'])
    if captured:
        _binding(source['binding'], '.md', MAX_FILE_BYTES)
        _hash(source['span_sha256'])
        if (source['binding']['path'] != source['path']
                or source['span_sha256'] != digest(source['quote'].encode('utf-8'))):
            raise EvidenceError('invalid_source_binding')


def _declarations(value, *, captured=False):
    bounded_json(value)
    if (type(value) is not dict or set(value) != ROOT_FIELDS
            or type(value['schema_version']) is not int
            or value['schema_version'] != SCHEMA_VERSION):
        raise EvidenceError('invalid_declaration')
    _id(value['ontology_id'])
    _text(value['version'], MAX_TEXT)
    types = value['types']
    if type(types) is not list or not 1 <= len(types) <= MAX_TYPES:
        raise EvidenceError('invalid_types')
    for item in types:
        _id(item)
    if len(set(types)) != len(types):
        raise EvidenceError('duplicate_type')
    seen, total = set(), 0
    for collection, fields in RECORD_FIELDS.items():
        records = value[collection]
        if type(records) is not list:
            raise EvidenceError('invalid_records')
        total += len(records)
        if total > MAX_RECORDS:
            raise EvidenceError('record_limit')
        for record in records:
            if type(record) is not dict or set(record) != fields:
                raise EvidenceError('invalid_record')
            ident = _id(record['id'])
            if ident in seen:
                raise EvidenceError('duplicate_record_id')
            seen.add(ident)
            if record['review'] not in ('candidate', 'reviewed'):
                raise EvidenceError('invalid_review')
            for name in fields - {'id', 'aliases', 'review', 'sources'}:
                _text(record[name], MAX_TEXT)
            if 'aliases' in fields:
                aliases = record['aliases']
                if type(aliases) is not list or len(aliases) > MAX_RECORDS:
                    raise EvidenceError('invalid_aliases')
                for alias in aliases:
                    _text(alias, MAX_TEXT)
            sources = record['sources']
            if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
                raise EvidenceError('invalid_sources')
            for source in sources:
                _source_schema(source, captured)
    entities = {record['id']: record for record in value['entities']}
    relations = {record['id']: record for record in value['relations']}
    for entity in entities.values():
        if entity['type'] not in types:
            raise EvidenceError('unknown_entity_type')
    for relation in relations.values():
        if relation['domain'] not in types or relation['range'] not in types:
            raise EvidenceError('unknown_relation_type')
    triples = set()
    for link in value['links']:
        triple = (link['subject'], link['predicate'], link['object'])
        if triple in triples:
            raise EvidenceError('duplicate_link')
        triples.add(triple)
        subject, predicate, target = triple
        if subject not in entities or target not in entities or predicate not in relations:
            raise EvidenceError('unknown_link_reference')
        relation = relations[predicate]
        if (entities[subject]['type'] != relation['domain']
                or entities[target]['type'] != relation['range']):
            raise EvidenceError('link_type_mismatch')


def _compile(reader, declaration_path):
    path = reader.path(declaration_path, '.json')
    relative = path.relative_to(reader.root).as_posix()
    info, mark, raw = stable_read(lambda: reader.path(relative, '.json'),
                                  MAX_JSON_BYTES, contents=True)
    reader.input_binding = (relative, info)
    reader.declaration_mark = mark
    declaration = decode(raw)
    _declarations(declaration)
    records = {}
    for name in RECORD_FIELDS:
        records[name] = []
        for record in declaration[name]:
            proofs = []
            for source in record['sources']:
                proof = _proof(source, reader)
                proofs.append({**proof, 'span_sha256': digest(source['quote'].encode('utf-8'))})
            records[name].append({**record, 'sources': proofs})
    result = {'type': 'ontology_registry', 'schema_version': SCHEMA_VERSION,
              'authority': 'data_only', 'ontology_id': declaration['ontology_id'],
              'version': declaration['version'], 'types': list(declaration['types']),
              **records, 'declaration': {'path': relative, **info},
              'config_sha256': reader.config_info['sha256']}
    result['registry_sha256'] = digest(bounded_json(result))
    bounded_json(result)
    return result


def _snapshot(snapshot):
    # Freeze caller-owned host containers, then validate every cached shape.
    snapshot = decode(bounded_json(snapshot))
    if (type(snapshot) is not dict or set(snapshot) != REGISTRY_FIELDS
            or snapshot['type'] != 'ontology_registry'
            or snapshot['authority'] != 'data_only'):
        raise EvidenceError('invalid_registry')
    _declarations({key: snapshot[key] for key in ROOT_FIELDS}, captured=True)
    _binding(snapshot['declaration'], '.json', MAX_JSON_BYTES)
    _hash(snapshot['config_sha256'])
    _hash(snapshot['registry_sha256'])
    if digest(encode({key: value for key, value in snapshot.items()
                      if key != 'registry_sha256'})) != snapshot['registry_sha256']:
        raise EvidenceError('registry_checksum_mismatch')
    return snapshot


def capture_registry(vault: Path, declaration_path: str) -> dict:
    """Compile one explicitly selected safe JSON sidecar; never write it."""
    try:
        reader = policy(vault)
        result = _compile(reader, declaration_path)
        reader.recheck()
        return result
    except EvidenceError as exc:
        return unavailable(str(exc))
    except FAILURES:
        return unavailable('invalid_or_unreadable_input')


def _with_registry(vault, snapshot, builder):
    """Use only a complete policy-current registry and recheck after building."""
    try:
        snapshot = _snapshot(snapshot)
        reader = policy(vault)
        if reader.config_info['sha256'] != snapshot['config_sha256']:
            raise EvidenceError('configuration_changed')
        registry = _compile(reader, snapshot['declaration']['path'])
        if registry['declaration'] != snapshot['declaration']:
            raise EvidenceError('declaration_changed')
        # A valid self-authored hash cannot replace recompiled declarations.
        if registry != snapshot:
            raise EvidenceError('registry_changed')
        try:
            result = builder(registry)
        except Exception:
            # Builders are trusted internal readers, but their failures must
            # never expose exception text or partly assembled usable context.
            return unavailable('builder_failed')
        raw = bounded_json(result)
        if (type(result) is not dict or result.get('type') != REPORT_TYPE
                or type(result.get('schema_version')) is not int
                or result['schema_version'] != SCHEMA_VERSION
                or result.get('authority') != 'data_only'
                or result.get('registry_sha256') != snapshot['registry_sha256']
                or not {'status', 'context', 'matches', 'gaps'} <= set(result)
                or type(result['status']) is not str
                or any(type(result[name]) is not list for name in ('context', 'matches', 'gaps'))):
            raise EvidenceError('invalid_builder_result')
        # Return a frozen result so retained builder references cannot modify it.
        result = decode(raw)
        reader.recheck()
        return result
    except EvidenceError as exc:
        return unavailable(str(exc))
    except FAILURES:
        return unavailable('invalid_or_changed_registry')


def check_registry(vault: Path, snapshot: dict) -> dict:
    """Report definition currentness, never truth or answer-data availability."""
    return _with_registry(vault, snapshot, lambda registry: {
        'type': REPORT_TYPE, 'schema_version': SCHEMA_VERSION, 'authority': 'data_only',
        'registry_sha256': registry['registry_sha256'], 'status': 'current',
        'context': [], 'matches': [], 'gaps': []})
