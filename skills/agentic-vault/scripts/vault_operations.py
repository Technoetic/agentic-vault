#!/usr/bin/env python3
"""Explicit caller-owned operation claims; this module never executes an effect.

A caller may act only after begin returns ``claimed`` and must finish within its
lease. Pending/uncertain/closed receipts never grant another execution. Durable
claims narrow the crash window but cannot guarantee exactly-once remote effects.
Locks cover cooperating writers only. Reconciliation records a caller observation
and closes uncertainty; it neither executes nor authorizes any operation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

from jev_client import contains_sensitive
from vault_evidence import EvidenceError, decode, stable_read
from vault_healthcheck import HealthcheckError
from vault_paths import resolve_note_path
from vault_state import (RUNTIME_DIR, StateError, _Policy, _atomic, _check_lease,
                         _cleanup_temporary, _encode, _ensure_runtime, _lease_guard,
                         advisory_lock)

MAX_INPUT_BYTES = 32 * 1024
MAX_PARAMETERS_BYTES = 16 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
MAX_RESULT_BYTES = 1024
MAX_DEPTH = 12
MAX_NODES = 1024
MAX_STRING = 8192
MAX_TTL_SECONDS = 86400
MAX_INTEGER = 2 ** 53 - 1
HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
TOKEN_RE = re.compile(r'[0-9a-f]{32}\Z')
STATES = frozenset({'pending', 'uncertain', 'succeeded', 'not-executed'})
RESULT_FIELDS = frozenset({'http_status', 'response_bytes', 'effect_count',
                           'remote_id_sha256', 'evidence_sha256', 'verified'})
RECEIPT_FIELDS = frozenset({'version', 'id', 'intent_sha256', 'action_sha256',
    'parameters_sha256', 'config_sha256', 'owner_token', 'created_at', 'ttl_seconds',
    'state', 'result', 'events', 'receipt_sha256'})


class OperationError(Exception):
    """Only bounded, content-free codes cross the API/CLI error boundary."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value, code='invalid_id'):
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise OperationError(code)
    return value


def _token(value):
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise OperationError('invalid_owner_token')
    return value


def _bounded_json(value, limit=MAX_INPUT_BYTES, *, sensitive=True):
    """Accept only bounded JSON types before canonical encoding or inspection."""
    nodes = 0
    active = set()
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise OperationError('invalid_input_structure')
        kind = type(item)
        if kind is str:
            if len(item) > MAX_STRING or '\x00' in item:
                raise OperationError('invalid_input_text')
            try:
                item.encode('utf-8')
            except UnicodeError as exc:
                raise OperationError('invalid_input_text') from exc
        elif kind is int:
            if abs(item) > MAX_INTEGER:
                raise OperationError('invalid_input_number')
        elif kind is float:
            if not math.isfinite(item):
                raise OperationError('invalid_input_number')
        elif kind in (dict, list):
            identity = id(item)
            if identity in active or len(item) > MAX_NODES:
                raise OperationError('invalid_input_structure')
            active.add(identity)
            if kind is dict:
                for key, member in item.items():
                    if type(key) is not str:
                        raise OperationError('invalid_input_structure')
                    visit(key, depth + 1)
                    visit(member, depth + 1)
            else:
                for member in item:
                    visit(member, depth + 1)
            active.remove(identity)
        elif kind not in (bool, type(None)):
            raise OperationError('invalid_input_structure')
    visit(value, 0)
    try:
        raw = _encode(value)
        if len(raw) > limit:
            raise OperationError('input_too_large')
        if sensitive and contains_sensitive(value):
            raise OperationError('sensitive_input')
        return raw
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OperationError('invalid_input') from exc


def _label(value):
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise OperationError('invalid_scope')
    return _bounded_json(value, 2048)


def _ttl(value):
    if (type(value) not in (int, float) or not 0 < value <= MAX_TTL_SECONDS
            or not math.isfinite(value)):
        raise OperationError('invalid_ttl')
    return value


def canonical_parameters(parameters):
    """Return canonical JSON bytes; object order is irrelevant, list order is not.

    The body is transient and never written to a receipt. Reject known secrets
    rather than encode credentials into even a hashed identity.
    """
    if type(parameters) is not dict:
        raise OperationError('invalid_parameters')
    return _bounded_json(parameters, MAX_PARAMETERS_BYTES)


def _result(value):
    if type(value) is not dict or not set(value) <= RESULT_FIELDS:
        raise OperationError('invalid_result_metadata')
    for key, item in value.items():
        if key.endswith('_sha256'):
            _hash(item, 'invalid_result_metadata')
        elif key == 'verified':
            if type(item) is not bool:
                raise OperationError('invalid_result_metadata')
        elif type(item) is not int or not 0 <= item <= MAX_INTEGER:
            raise OperationError('invalid_result_metadata')
        elif key == 'http_status' and not 100 <= item <= 599:
            raise OperationError('invalid_result_metadata')
    _bounded_json(value, MAX_RESULT_BYTES)
    return dict(value)


def _utc(value):
    if type(value) is not str or len(value) > 48:
        raise OperationError('invalid_receipt')
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
            raise OperationError('invalid_receipt')
        stamp = result.timestamp()
        if not math.isfinite(stamp):
            raise OperationError('invalid_receipt')
        return stamp
    except (ValueError, OverflowError, OSError) as exc:
        raise OperationError('invalid_receipt') from exc


def _identity(intent_sha, action_sha):
    return _sha(_encode({'version': 1, 'intent_sha256': intent_sha,
                         'action_sha256': action_sha}))


def _stamp(record):
    record['receipt_sha256'] = _sha(_encode({key: value for key, value in record.items()
                                          if key != 'receipt_sha256'}))
    return record


def _validate_receipt(record, operation_id):
    if (type(record) is not dict or set(record) != RECEIPT_FIELDS
            or type(record['version']) is not int or record['version'] != 1
            or record['id'] != operation_id or record['state'] not in STATES):
        raise OperationError('invalid_receipt')
    for key in ('id', 'intent_sha256', 'action_sha256', 'parameters_sha256',
                'config_sha256', 'receipt_sha256'):
        _hash(record[key], 'invalid_receipt')
    if record['id'] != _identity(record['intent_sha256'], record['action_sha256']):
        raise OperationError('invalid_receipt')
    _token(record['owner_token'])
    created = _utc(record['created_at'])
    _ttl(record['ttl_seconds'])
    events = record['events']
    if type(events) is not list or not 1 <= len(events) <= 3:
        raise OperationError('invalid_receipt')
    names = []
    previous = created
    for event in events:
        if type(event) is not dict or set(event) != {'at', 'event', 'fence_sha256'}:
            raise OperationError('invalid_receipt')
        _hash(event['fence_sha256'], 'invalid_receipt')
        stamp = _utc(event['at'])
        if stamp < previous:
            raise OperationError('invalid_receipt')
        previous = stamp
        names.append(event['event'])
    if events[0]['at'] != record['created_at'] or names[0] != 'claimed':
        raise OperationError('invalid_receipt')
    unknown = len(names) >= 2 and names[1] in ('caller-unknown', 'lease-expired')
    valid = ((record['state'] == 'pending' and names == ['claimed'])
        or (record['state'] == 'uncertain' and len(names) == 2 and unknown)
        or (record['state'] == 'succeeded' and
            (names == ['claimed', 'finished'] or
             (len(names) == 3 and unknown and names[2] == 'reconciled-success')))
        or (record['state'] == 'not-executed' and len(names) == 3
            and unknown and names[2] == 'reconciled-not-executed'))
    if not valid or events[-1]['fence_sha256'] != _sha(record['owner_token'].encode()):
        raise OperationError('invalid_receipt')
    if record['state'] == 'succeeded':
        _result(record['result'])
    elif record['result'] is not None:
        raise OperationError('invalid_receipt')
    expected = _sha(_encode({key: value for key, value in record.items()
                            if key != 'receipt_sha256'}))
    if record['receipt_sha256'] != expected:
        raise OperationError('invalid_receipt')
    _bounded_json(record, MAX_RECEIPT_BYTES, sensitive=False)


def _policy(vault):
    try:
        return _Policy(vault)
    except (StateError, EvidenceError, HealthcheckError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('unsafe_configuration') from exc


def _read(policy, name, *, limit=MAX_RECEIPT_BYTES):
    try:
        return stable_read(lambda: policy.runtime(name), limit, contents=True)
    except (EvidenceError, StateError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('unsafe_or_changed_record') from exc


def _load(policy, operation_id):
    name = 'operation-' + _hash(operation_id) + '.json'
    info, _mark, raw = _read(policy, name)
    try:
        record = decode(raw)
        _validate_receipt(record, operation_id)
        return record, info['sha256']
    except (EvidenceError, ValueError, TypeError, KeyError) as exc:
        raise OperationError('invalid_receipt') from exc


def _fresh(policy, record=None):
    try:
        current = policy.fresh()
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('configuration_changed') from exc
    if record is not None and record['config_sha256'] != current.config_sha256:
        raise OperationError('configuration_changed')
    return current


def _save(policy, lease, record, previous_sha=None):
    _stamp(record)
    _validate_receipt(record, record['id'])
    data = _bounded_json(record, MAX_RECEIPT_BYTES, sensitive=False) + b'\n'
    name = 'operation-' + record['id'] + '.json'
    _ensure_runtime(_fresh(policy, record))
    path = policy.runtime(name)
    def replace(temporary):
        with _lease_guard(policy, lease):
            current = _fresh(policy, record)
            _check_lease(current, lease)
            checked = current.runtime(name)
            staged = current.runtime(temporary.name)
            info, _, raw = stable_read(lambda: current.runtime(temporary.name),
                                       MAX_RECEIPT_BYTES + 1, contents=True)
            if staged != temporary or checked != path or raw != data:
                raise OperationError('prepared_record_changed')
            if previous_sha is None:
                if checked.exists():
                    raise OperationError('record_already_exists')
            else:
                info, _, _ = _read(current, name)
                if info['sha256'] != previous_sha:
                    raise OperationError('stale_receipt')
            # Last configuration/lease checks are adjacent to the single replace.
            _fresh(policy, record)
            _check_lease(current, lease)
            if (record['state'] == 'succeeded'
                    and record['events'][-1]['event'] == 'finished' and _expired(record)):
                # Preserve the durable pending claim if the owner expired while
                # its completion was staged. Next begin fences it as uncertain.
                raise OperationError('operation_uncertain')
            os.replace(temporary, checked)
    try:
        _atomic(path, data, replace, cleanup_guard=lambda temporary:
                _cleanup_temporary(policy, name, temporary, runtime=True))
    except OperationError:
        raise
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('record_write_unavailable') from exc
    _fresh(policy, record)
    observed, _ = _load(policy, record['id'])
    if observed != record:
        raise OperationError('record_write_unavailable')


def _expired(record):
    return time.time() >= _utc(record['created_at']) + record['ttl_seconds']


def _event(record, name, state):
    record['owner_token'] = uuid.uuid4().hex
    record['state'] = state
    record['events'].append({'at': _now(), 'event': name,
                            'fence_sha256': _sha(record['owner_token'].encode())})


def _view(policy, record, *, disposition=None, owner=False):
    result = {key: record[key] for key in ('version', 'id', 'parameters_sha256',
              'created_at', 'ttl_seconds', 'state', 'result', 'receipt_sha256', 'events')}
    result['current'] = record['config_sha256'] == policy.config_sha256
    result['lease_expired'] = record['state'] == 'pending' and _expired(record)
    if disposition is not None:
        result['disposition'] = disposition
    if owner:
        result['owner_token'] = record['owner_token']
    # Detach nested containers; mutating a report cannot modify an internal record.
    return json.loads(_encode(result))


def begin(vault, intent, action, parameters, *, ttl_seconds=300, wait_seconds=2):
    """Durably claim one intention/action or report blocked/reused/closed.

    ``intent`` must identify existing caller authorization. The helper does not
    create approval. Only ``claimed`` supplies an owner token; act within its TTL
    and finish. Same intent/action with different parameters is a conflict. A new
    intent is a separate scope and must have its own existing authorization.
    """
    intent_sha = _sha(_label(intent))
    action_sha = _sha(_label(action))
    parameters_sha = _sha(canonical_parameters(parameters))
    _ttl(ttl_seconds)
    if (type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 60
            or not math.isfinite(wait_seconds)):
        raise OperationError('invalid_wait')
    operation_id = _identity(intent_sha, action_sha)
    policy = _policy(vault)
    try:
        with advisory_lock(vault, 'operation:' + operation_id, wait_seconds=wait_seconds) as lease:
            _fresh(policy)
            name = 'operation-' + operation_id + '.json'
            if policy.runtime(name).exists():
                record, previous = _load(policy, operation_id)
                _fresh(policy, record)
                if record['parameters_sha256'] != parameters_sha:
                    raise OperationError('intent_conflict')
                if record['state'] == 'pending' and _expired(record):
                    _event(record, 'lease-expired', 'uncertain')
                    _save(policy, lease, record, previous)
                disposition = {'succeeded': 'reused', 'not-executed': 'closed'}.get(
                    record['state'], 'blocked')
                _fresh(policy, record)
                return _view(policy, record, disposition=disposition)
            token = uuid.uuid4().hex
            created = _now()
            record = {'version': 1, 'id': operation_id, 'intent_sha256': intent_sha,
                'action_sha256': action_sha, 'parameters_sha256': parameters_sha,
                'config_sha256': policy.config_sha256, 'owner_token': token,
                'created_at': created, 'ttl_seconds': ttl_seconds, 'state': 'pending',
                'result': None, 'events': [{'at': created, 'event': 'claimed',
                                           'fence_sha256': _sha(token.encode())}]}
            _save(policy, lease, record)
            if _expired(record):
                _, previous = _load(policy, operation_id)
                _event(record, 'lease-expired', 'uncertain')
                _save(policy, lease, record, previous)
                return _view(policy, record, disposition='blocked')
            return _view(policy, record, disposition='claimed', owner=True)
    except OperationError:
        raise
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('operation_unavailable') from exc


def _owner(policy, operation_id, owner_token):
    record, previous = _load(policy, operation_id)
    _fresh(policy, record)
    if record['owner_token'] != _token(owner_token):
        raise OperationError('owner_fence_changed')
    if record['state'] != 'pending':
        raise OperationError('operation_not_pending')
    return record, previous


def finish(vault, operation_id, owner_token, result):
    """Record host-observed success metadata, never the raw remote response.

    Receipt failure after an effect is unknown: call mark_uncertain if possible
    and hold for explicit reconciliation. Never replay on a write error.
    """
    result = _result(result)
    operation_id = _hash(operation_id)
    _token(owner_token)
    policy = _policy(vault)
    try:
        with advisory_lock(vault, 'operation:' + operation_id, wait_seconds=2) as lease:
            record, previous = _owner(policy, operation_id, owner_token)
            if _expired(record):
                _event(record, 'lease-expired', 'uncertain')
                _save(policy, lease, record, previous)
                raise OperationError('operation_uncertain')
            record['result'] = result
            _event(record, 'finished', 'succeeded')
            _save(policy, lease, record, previous)
            return _view(policy, record)
    except OperationError:
        raise
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('operation_unavailable') from exc


def mark_uncertain(vault, operation_id, owner_token):
    """Fence an owned pending claim after timeout/crash/unknown result; no replay."""
    operation_id = _hash(operation_id)
    _token(owner_token)
    policy = _policy(vault)
    try:
        with advisory_lock(vault, 'operation:' + operation_id, wait_seconds=2) as lease:
            record, previous = _owner(policy, operation_id, owner_token)
            _event(record, 'caller-unknown', 'uncertain')
            _save(policy, lease, record, previous)
            return _view(policy, record)
    except OperationError:
        raise
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('operation_unavailable') from exc


def reconcile(vault, operation_id, expected_sha256, *, outcome, result=None):
    """Close uncertainty against one inspected revision; cannot grant execution.

    outcome is succeeded or not-executed. The latter remains permanently closed
    for this intention/action. A genuinely new action requires a separate caller
    intention and existing authorization, outside this reconciliation API.
    """
    operation_id = _hash(operation_id)
    _hash(expected_sha256, 'invalid_receipt_hash')
    if outcome not in ('succeeded', 'not-executed'):
        raise OperationError('invalid_reconciliation')
    if outcome == 'succeeded':
        result = _result(result)
    elif result is not None:
        raise OperationError('invalid_reconciliation')
    policy = _policy(vault)
    try:
        with advisory_lock(vault, 'operation:' + operation_id, wait_seconds=2) as lease:
            record, previous = _load(policy, operation_id)
            _fresh(policy, record)
            if record['receipt_sha256'] != expected_sha256:
                raise OperationError('stale_receipt')
            if record['state'] != 'uncertain':
                raise OperationError('operation_not_uncertain')
            record['result'] = result
            _event(record, 'reconciled-success' if outcome == 'succeeded'
                   else 'reconciled-not-executed', outcome)
            _save(policy, lease, record, previous)
            return _view(policy, record)
    except OperationError:
        raise
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('operation_unavailable') from exc


def inspect(vault, operation_id):
    """Read-only redacted receipt view; expired pending lease remains non-executable.

    A subsequent begin persists uncertain. current reports config binding only,
    not truth, verified authorization, or successful remote execution.
    """
    policy = _policy(vault)
    record, _ = _load(policy, _hash(operation_id))
    _fresh(policy)
    return _view(policy, record)


def load_input(vault, relative):
    """Bounded, stable, explicit vault-relative CLI JSON input with note policy."""
    policy = _policy(vault)
    def resolve():
        return resolve_note_path(policy.vault, relative, policy.rules)
    try:
        resolve()  # Deny/exclude/runtime/link checks happen before any content read.
        _, _, raw = stable_read(resolve, MAX_INPUT_BYTES, contents=True)
        value = decode(raw)
        _bounded_json(value)
        if type(value) is not dict:
            raise OperationError('invalid_input')
        _fresh(policy)
        return value
    except OperationError:
        raise
    except (EvidenceError, StateError, ValueError, OSError, RuntimeError) as exc:
        raise OperationError('unsafe_or_invalid_input') from exc


def _fields(value, required, optional=()):
    if set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise OperationError('invalid_arguments')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes untrusted argv, including credential values.
        self.exit(2, '{"error":"invalid_arguments"}\n')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--vault', required=True)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    for command in ('begin', 'finish', 'mark-uncertain', 'reconcile'):
        commands.add_parser(command).add_argument('--input', required=True)
    commands.add_parser('inspect').add_argument('--id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = inspect(args.vault, args.id)
        else:
            value = load_input(args.vault, args.input)
            if args.command == 'begin':
                _fields(value, ('intent', 'action', 'parameters'), ('ttl_seconds', 'wait_seconds'))
                result = begin(args.vault, **value)
            elif args.command == 'finish':
                _fields(value, ('id', 'owner_token', 'result'))
                result = finish(args.vault, value['id'], value['owner_token'], value['result'])
            elif args.command == 'mark-uncertain':
                _fields(value, ('id', 'owner_token'))
                result = mark_uncertain(args.vault, value['id'], value['owner_token'])
            else:
                _fields(value, ('id', 'expected_sha256', 'outcome'), ('result',))
                result = reconcile(args.vault, value['id'], value['expected_sha256'],
                                   outcome=value['outcome'], result=value.get('result'))
        print(_encode(result).decode())
        # An inspection is a read result. Replay checks use the disposition and
        # owner token, never process success alone, to decide whether they may act.
        return 0
    except (OperationError, StateError, EvidenceError, HealthcheckError, ValueError,
            TypeError, KeyError, OSError, RuntimeError, UnicodeError, RecursionError):
        print('{"error":"operation_unavailable"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
