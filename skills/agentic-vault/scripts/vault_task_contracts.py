#!/usr/bin/env python3
"""Opt-in source-bound task declarations; no execution or authorization.

Origin labels are caller declarations, not authenticated identity. Exact source
excerpts and immutable checksummed receipts preserve a reviewable declaration;
they prove neither user authorship nor truth. Readiness invites execution review.
"""
from __future__ import annotations

import argparse
from functools import wraps
from pathlib import Path
import re

import vault_coverage as coverage
import vault_evidence as evidence
import vault_runs as runs
import vault_state as state
from vault_healthcheck import HealthcheckError

MAX_JSON_BYTES = runs.MAX_JSON_BYTES
MAX_SOURCE_BYTES = runs.MAX_SOURCE_BYTES
MAX_TOTAL_BYTES = runs.MAX_TOTAL_BYTES
MAX_FIELDS = 64
MAX_QUESTIONS = 32
MAX_SOURCES = 64
MAX_PARENTS = 64
MAX_CONTRACTS = 256
MAX_RUNTIME_ENTRIES = 4096
ID_RE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}')
RECEIPT_RE = re.compile(r'task-contract-([0-9a-f]{64})\.json')
KINDS = frozenset({'goal', 'scope', 'non_goal', 'constraint', 'acceptance'})
ORIGINS = frozenset({'user_request', 'user_confirmation', 'repository_fact', 'host_default'})
LIMITATIONS = [
    'origin_labels_are_caller_declarations',
    'source_hashes_and_excerpts_do_not_prove_truth_or_user_identity',
    'checksums_are_not_signatures',
    'readiness_is_not_authorization',
    'no_commands_models_or_network_are_executed',
]
LIMITS = dict(max_json_bytes=MAX_JSON_BYTES, max_source_bytes=MAX_SOURCE_BYTES,
              max_total_bytes=MAX_TOTAL_BYTES, max_fields=MAX_FIELDS,
              max_questions=MAX_QUESTIONS, max_sources=MAX_SOURCES,
              max_revision_depth=MAX_PARENTS, max_contracts=MAX_CONTRACTS,
              max_runtime_entries=MAX_RUNTIME_ENTRIES)


class ContractError(Exception):
    """A content-free operational error code."""


class _StaleContract(ContractError):
    """A validated receipt has historical, rather than current, declarations."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ContractError:
            raise
        except (runs.RunError, state.StateError, evidence.EvidenceError, HealthcheckError,
                OSError, ValueError, UnicodeError, TypeError, RecursionError,
                OverflowError, RuntimeError) as exc:
            raise ContractError('unsafe_or_invalid_input') from exc
    return call


def _id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ContractError('invalid_or_duplicate_id')
    return value


class _Store(runs.Store):
    """Keep native runtime writes and coverage's stricter public-file policy."""

    def __init__(self, vault):
        super().__init__(vault)
        self.public_paths = coverage._Store(vault)

    def path(self, relative, *, markdown=False):
        # Apply the existing credential-path owner before any inherited byte
        # reader, including stored receipt bindings and publication rechecks.
        try:
            self.public_paths.path(relative)
        except coverage.CoverageError as exc:
            raise ContractError('unsafe_public_path') from exc
        return super().path(relative, markdown=markdown)


def _path(store, value):
    # Native policy owns path/deny/symlink/credential checks. Require a single
    # portable spelling in this new format rather than accepting backslash aliases.
    if not isinstance(value, str) or '\\' in value:
        raise ContractError('noncanonical_path')
    canonical = store.path(value).relative_to(store.root).as_posix()
    if canonical != value:
        raise ContractError('noncanonical_path')
    return canonical


def _definition(value):
    runs._safe_json(value)
    runs._fields(value, {'version', 'task', 'fields', 'questions'}, 'invalid_task_contract')
    if type(value['version']) is not int or value['version'] != 1:
        raise ContractError('invalid_version')
    runs._text(value['task'], maximum=256)
    if not isinstance(value['fields'], list) or len(value['fields']) > MAX_FIELDS:
        raise ContractError('invalid_fields')
    if not isinstance(value['questions'], list) or len(value['questions']) > MAX_QUESTIONS:
        raise ContractError('invalid_questions')
    fields, seen = [], set()
    for field in value['fields']:
        runs._fields(field, {'id', 'kind', 'text', 'origin', 'status', 'sources'}, 'invalid_field')
        identity = _id(field['id'])
        if identity in seen:
            raise ContractError('invalid_or_duplicate_id')
        seen.add(identity)
        if (not isinstance(field['kind'], str) or field['kind'] not in KINDS
                or not isinstance(field['origin'], str) or field['origin'] not in ORIGINS
                or not isinstance(field['status'], str) or field['status'] not in {'confirmed', 'proposed'}):
            raise ContractError('invalid_field')
        runs._text(field['text'])
        sources = field['sources']
        if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
            raise ContractError('invalid_sources')
        if field['origin'] != 'host_default' and not sources:
            raise ContractError('missing_source')
        source_seen = set()
        for source in sources:
            runs._fields(source, {'path', 'excerpt'}, 'invalid_source')
            runs._text(source['path'], maximum=1024)
            runs._text(source['excerpt'])
            identity = (source['path'].casefold(), source['excerpt'])
            if identity in source_seen:
                raise ContractError('duplicate_source')
            source_seen.add(identity)
        fields.append({**field, 'sources': sorted(sources, key=lambda s: (s['path'], s['excerpt']))})
    questions = []
    for question in value['questions']:
        runs._fields(question, {'id', 'text', 'blocking', 'status'}, 'invalid_question')
        identity = _id(question['id'])
        if identity in seen:
            raise ContractError('invalid_or_duplicate_id')
        seen.add(identity)
        runs._text(question['text'])
        if (type(question['blocking']) is not bool or not isinstance(question['status'], str)
                or question['status'] not in {'open', 'resolved', 'unknown'}):
            raise ContractError('invalid_question')
        questions.append(dict(question))
    return {**value, 'fields': sorted(fields, key=lambda f: f['id']),
            'questions': sorted(questions, key=lambda q: q['id'])}


def _sources(store, definition, input_source):
    files, texts = {}, {}
    total = input_source['size']
    for field in definition['fields']:
        for source in field['sources']:
            path = _path(store, source['path'])
            if path.casefold() == input_source['path'].casefold():
                raise ContractError('source_overlaps_input')
            if path not in files:
                if len(files) >= MAX_SOURCES:
                    raise ContractError('too_many_sources')
                if path.casefold() in {p.casefold() for p in files}:
                    raise ContractError('duplicate_file_binding')
                info, raw = store.fingerprint(path)
                total += info['size']
                if total > MAX_TOTAL_BYTES:
                    raise ContractError('file_set_too_large')
                files[path], texts[path] = info, raw.decode('utf-8-sig')
            if source['excerpt'] not in texts[path]:
                raise ContractError('source_excerpt_mismatch')
    return sorted(files.values(), key=lambda f: f['path'])


def _name(contract_id):
    runs._hash(contract_id)
    return 'task-contract-' + contract_id + '.json'


def _load(store, contract_id):
    receipt = store.read_record(_name(contract_id))
    if receipt['kind'] != 'task_contract' or receipt['id'] != contract_id:
        raise ContractError('invalid_contract_receipt')
    payload = receipt['payload']
    runs._fields(payload, {'definition', 'input_source', 'source_bindings', 'parent',
                           'config_sha256'}, 'invalid_contract_payload')
    if evidence.digest(evidence.encode(payload)) != contract_id:
        raise ContractError('contract_identity_mismatch')
    if _definition(payload['definition']) != payload['definition']:
        raise ContractError('noncanonical_definition')
    runs._hash(payload['config_sha256'])
    runs._fingerprint_record(store, payload['input_source'], limit=MAX_JSON_BYTES)
    if (not isinstance(payload['source_bindings'], list)
            or len(payload['source_bindings']) > MAX_SOURCES):
        raise ContractError('invalid_source_bindings')
    seen = set()
    total = payload['input_source']['size']
    for binding in payload['source_bindings']:
        runs._fingerprint_record(store, binding)
        identity = binding['path'].casefold()
        if identity in seen or identity == payload['input_source']['path'].casefold():
            raise ContractError('duplicate_file_binding')
        seen.add(identity)
        total += binding['size']
    if total > MAX_TOTAL_BYTES:
        raise ContractError('file_set_too_large')
    declared_paths = {_path(store, source['path']) for field in payload['definition']['fields']
                      for source in field['sources']}
    if declared_paths != {binding['path'] for binding in payload['source_bindings']}:
        raise ContractError('source_binding_mismatch')
    if payload['source_bindings'] != sorted(payload['source_bindings'], key=lambda f: f['path']):
        raise ContractError('noncanonical_source_bindings')
    if payload['parent'] is not None:
        runs._fields(payload['parent'], {'contract_id', 'receipt_sha256'}, 'invalid_parent')
        runs._hash(payload['parent']['contract_id'])
        runs._hash(payload['parent']['receipt_sha256'])
    return receipt


def _parents(store, receipt):
    seen = {receipt['id']}
    parent = receipt['payload']['parent']
    while parent is not None:
        if parent['contract_id'] in seen or len(seen) >= MAX_PARENTS:
            raise ContractError('invalid_revision_chain')
        seen.add(parent['contract_id'])
        previous = _load(store, parent['contract_id'])
        if previous['receipt_sha256'] != parent['receipt_sha256']:
            raise ContractError('parent_receipt_changed')
        parent = previous['payload']['parent']


def _children(store, contract_id, *, adding=False):
    directory = store.record_path('')
    if not directory.exists():
        return []
    children, scanned, matched = [], 0, 0
    for entry in directory.iterdir():
        scanned += 1
        if scanned > MAX_RUNTIME_ENTRIES:
            raise ContractError('runtime_scan_limit')
        match = RECEIPT_RE.fullmatch(entry.name)
        if match is None:
            continue
        matched += 1
        if matched + int(adding) > MAX_CONTRACTS:
            raise ContractError('contract_scan_limit')
        candidate = _load(store, match.group(1))
        parent = candidate['payload']['parent']
        if parent is not None and parent['contract_id'] == contract_id:
            previous = _load(store, contract_id)
            if parent['receipt_sha256'] != previous['receipt_sha256']:
                raise ContractError('parent_receipt_changed')
            children.append(candidate['id'])
    store.policy.fresh()
    return children


def _current(store, receipt, *, supersession=True):
    payload = receipt['payload']
    _parents(store, receipt)
    try:
        if payload['config_sha256'] != store.policy.config_sha256:
            raise ContractError('configuration_changed')
        runs._current_files(store, [payload['input_source']], limit=MAX_JSON_BYTES)
        _, value = store.json_file(payload['input_source']['path'])
        if _definition(value) != payload['definition']:
            raise ContractError('definition_binding_mismatch')
        if _sources(store, payload['definition'], payload['input_source']) != payload['source_bindings']:
            raise ContractError('stale_source')
    except (ContractError, runs.RunError, state.StateError, evidence.EvidenceError,
            HealthcheckError, OSError, ValueError, UnicodeError, TypeError,
            RecursionError, OverflowError, RuntimeError) as exc:
        raise _StaleContract('stale_source') from exc
    if supersession and _children(store, receipt['id']):
        raise _StaleContract('superseded_contract')
    if 'receipt_sha256' in receipt and _load(store, receipt['id']) != receipt:
        raise ContractError('contract_receipt_changed')
    store.policy.fresh()


def _result(contract_id, receipt=None, *, stale=False):
    payload = receipt['payload'] if receipt is not None and not stale else None
    definition = payload['definition'] if payload else dict(task=None, fields=[], questions=[])
    accepted = [f for f in definition['fields']
                if f['origin'] in {'user_request', 'user_confirmation'} and f['status'] == 'confirmed']
    acceptance_ids = [f['id'] for f in accepted if f['kind'] == 'acceptance']
    ready = (sum(f['kind'] == 'goal' for f in accepted) == 1
             and any(f['kind'] == 'scope' for f in accepted) and bool(acceptance_ids)
             and not any(q['blocking'] and q['status'] != 'resolved' for q in definition['questions']))
    return dict(contract_id=contract_id, fingerprint=contract_id,
                status=('stale' if stale else 'ready_for_execution_review' if ready else 'needs_clarification'),
                acceptance_ids=acceptance_ids, accepted_fields=accepted,
                repository_facts=[f for f in definition['fields'] if f['origin'] == 'repository_fact'],
                fields=definition['fields'], questions=definition['questions'], task=definition['task'],
                parent=payload['parent'] if payload else None,
                source_bindings=payload['source_bindings'] if payload else [],
                input_source=payload['input_source'] if payload else None,
                config_sha256=payload['config_sha256'] if payload else None,
                receipt_sha256=receipt['receipt_sha256'] if receipt is not None and not stale else None,
                path=runs.RUNTIME_DIR + '/' + _name(contract_id),
                limits=dict(LIMITS), authority='data_only', historical_metadata=None,
                limitations=list(LIMITATIONS))


@_public
def inspect(vault, contract_id):
    """Read declarations and freshness; stale declarations are historical only."""
    _name(contract_id)
    receipt = None
    try:
        store = _Store(vault)
        receipt = _load(store, contract_id)
        _current(store, receipt)
        return _result(contract_id, receipt)
    except _StaleContract:
        result = _result(contract_id, stale=True)
        previous = _result(contract_id, receipt)
        result['historical_metadata'] = {key: previous[key] for key in (
            'task', 'acceptance_ids', 'accepted_fields', 'source_bindings',
            'input_source', 'config_sha256', 'receipt_sha256', 'path')}
        result['limitations'].append('historical_metadata_is_not_current_acceptance')
        return result
    except (ContractError, runs.RunError, state.StateError, evidence.EvidenceError,
            HealthcheckError, OSError, ValueError, UnicodeError, TypeError,
            RecursionError, OverflowError, RuntimeError):
        return _result(contract_id, stale=True)


def _capture(vault, input_path, parent_id=None):
    store = _Store(vault)
    path = _path(store, input_path)
    info, value = store.json_file(path)
    definition = _definition(value)
    parent_receipt = _load(store, parent_id) if parent_id is not None else None
    if parent_receipt is not None:
        _parents(store, parent_receipt)
    parent = (dict(contract_id=parent_id, receipt_sha256=parent_receipt['receipt_sha256'])
              if parent_receipt else None)
    payload = dict(definition=definition, input_source=info,
                   source_bindings=_sources(store, definition, info), parent=parent,
                   config_sha256=store.policy.config_sha256)
    contract_id = evidence.digest(evidence.encode(payload))
    name = _name(contract_id)
    # The small opt-in store has a bounded receipt count. A shared native lock
    # serializes its capacity check and parent supersession across all writers.
    resource = 'task-contracts'
    def recheck():
        # Revisions retain historical parent bytes, not a requirement that the
        # parent's replaced source files still be current.
        if parent_receipt is not None and _load(store, parent_id) != parent_receipt:
            raise ContractError('parent_receipt_changed')
        candidate = dict(id=contract_id, payload=payload)
        _current(store, candidate, supersession=False)
        _children(store, contract_id, adding=not store.record_path(name).exists())
        if parent_id is not None and any(child != contract_id for child in _children(store, parent_id)):
            raise ContractError('superseded_contract')
    with state.advisory_lock(vault, resource, wait_seconds=1) as lease:
        recheck()
        if store.record_path(name).exists():
            receipt = _load(store, contract_id)
            _current(store, receipt)
        else:
            receipt = store.publish(name, 'task_contract', contract_id, payload, recheck, lease)
    return _result(contract_id, receipt)


@_public
def capture(vault, input_path):
    """Explicitly freeze a bounded task declaration; identical bytes are reused."""
    return _capture(vault, input_path)


@_public
def revise(vault, contract_id, input_path):
    """Create an immutable revision with a native parent receipt binding."""
    _name(contract_id)
    return _capture(vault, input_path, contract_id)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(evidence.encode(dict(ok=False, error='invalid_cli_arguments')).decode('ascii'))
        self.exit(2)


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_ArgumentParser)
    commands.add_parser('capture').add_argument('--input', required=True)
    for command in ('inspect', 'check'):
        commands.add_parser(command).add_argument('id')
    revision = commands.add_parser('revise')
    revision.add_argument('id')
    revision.add_argument('--input', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            result = capture(args.vault, args.input)
        elif args.command == 'revise':
            result = revise(args.vault, args.id, args.input)
        else:
            result = inspect(args.vault, args.id)
        print(evidence.encode(result).decode('ascii'))
        return 0
    except ContractError as exc:
        print(evidence.encode(dict(ok=False, error=str(exc))).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
