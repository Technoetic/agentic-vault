#!/usr/bin/env python3
"""Bind reported verification evidence to explicitly selected vault file versions.

Current bindings do not prove claim truth, verifier identity, or command execution.
Checks are point-in-time observations, not an OS-enforced freeze of the workspace.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import resolve_note_path


STORE_DIR = '00-meta/evidence'
CONFIG_PATH = '00-meta/vault-config.json'
MAX_FILES = 64
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_SET_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_TEXT = 8192
ID_RE = re.compile(r'[0-9a-f]{32}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
CHECK_FIELDS = {'id', 'claim', 'status', 'method', 'observed', 'evidence',
                'preserve', 'next_check'}
RECEIPT_FIELDS = {'version', 'id', 'created_at', 'task', 'objective', 'artifacts',
                  'report', 'report_source', 'evidence', 'recorded_at', 'receipt_sha256'}


class EvidenceError(Exception):
    """A non-sensitive operational error code."""


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError('duplicate_json_key')
        result[key] = value
    return result


def _constant(_value):
    raise EvidenceError('non_finite_json')


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise EvidenceError('non_finite_json')
    return result


def decode(data):
    try:
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_object,
                          parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceError('invalid_json') from exc


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def text(value, *, required=True):
    if not isinstance(value, str) or len(value) > MAX_TEXT or '\x00' in value:
        raise EvidenceError('invalid_text')
    if required and not value.strip():
        raise EvidenceError('empty_text')


def stamp(metadata):
    # Python's Windows lstat/fstat can report different ctime values after an
    # atomic rename (directory-entry creation time vs handle information).
    # ctime is not a portable change clock there; content is always hashed.
    change_time = metadata.st_ctime_ns if os.name != 'nt' else 0
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
            change_time, metadata.st_nlink)


def regular(metadata):
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or getattr(metadata, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise EvidenceError('unsafe_file')


def stable_read(resolver, limit, *, contents=False):
    """Read from a checked descriptor, then re-resolve and compare file identity."""
    path = resolver()
    before = path.lstat()
    regular(before)
    if before.st_size > limit:
        raise EvidenceError('input_too_large')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    chunks = []
    hasher = hashlib.sha256()
    size = 0
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        regular(opened)
        if stamp(opened) != stamp(before):
            raise EvidenceError('file_changed_during_read')
        while True:
            chunk = stream.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise EvidenceError('input_too_large')
            hasher.update(chunk)
            if contents:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
    current = resolver()
    last = current.lstat()
    regular(last)
    if current != path or stamp(before) != stamp(after) or stamp(after) != stamp(last):
        raise EvidenceError('file_changed_during_read')
    if size != last.st_size:
        raise EvidenceError('file_changed_during_read')
    return {'sha256': hasher.hexdigest(), 'size': size}, stamp(last), b''.join(chunks)


class Store:
    def __init__(self, vault):
        self.vault = Path(vault)
        self.root = self.vault.resolve(strict=True)
        info, _mark, data = stable_read(
            lambda: resolve_note_path(self.vault, CONFIG_PATH, ('.git',)),
            MAX_JSON_BYTES, contents=True)
        self.config_sha = info['sha256']
        self.config = validate_config(decode(data))
        self.denied = (*self.config['deny_zones'], '.git')
        self.path(CONFIG_PATH, internal=True)

    def path(self, relative, *, internal=False):
        try:
            resolved = resolve_note_path(self.vault, relative, self.denied)
        except ValueError as exc:
            raise EvidenceError('unsafe_path') from exc
        canonical = resolved.relative_to(self.root).as_posix()
        if not internal and (canonical.casefold() == STORE_DIR
                             or canonical.casefold().startswith(STORE_DIR + '/')):
            raise EvidenceError('reserved_evidence_path')
        return resolved

    def fresh_policy(self):
        other = Store(self.vault)
        if other.config_sha != self.config_sha:
            raise EvidenceError('configuration_changed')

    def fingerprint(self, relative, *, limit=MAX_FILE_BYTES, contents=False, internal=False):
        path = self.path(relative, internal=internal)
        canonical = path.relative_to(self.root).as_posix()
        info, mark, data = stable_read(
            lambda: self.path(canonical, internal=internal), limit, contents=contents)
        return {'path': canonical, **info}, mark, data

    def sweep(self, marks, *, internal=False):
        for relative, expected in marks.items():
            metadata = self.path(relative, internal=internal).lstat()
            regular(metadata)
            if stamp(metadata) != expected:
                raise EvidenceError('file_changed_during_read')

    def capture(self, paths):
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
            raise EvidenceError('invalid_file_count')
        records, marks, seen, identities = [], {}, set(), set()
        total = 0
        for relative in paths:
            info, mark, _ = self.fingerprint(relative)
            key = info['path']
            if key in seen or mark[:2] in identities:
                raise EvidenceError('duplicate_file')
            seen.add(key)
            identities.add(mark[:2])
            total += info['size']
            if total > MAX_SET_BYTES:
                raise EvidenceError('set_too_large')
            records.append(info)
            marks[info['path']] = mark
        self.sweep(marks)
        return records, marks

    def receipt_path(self, record_id):
        if not isinstance(record_id, str) or not ID_RE.fullmatch(record_id):
            raise EvidenceError('invalid_id')
        return self.path(f'{STORE_DIR}/{record_id}.json', internal=True)

    @contextmanager
    def lock(self):
        directory = self.path(STORE_DIR, internal=True)
        directory.mkdir(exist_ok=True)
        lock_path = self.path(f'{STORE_DIR}/.lock', internal=True)
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise EvidenceError('locked_manual_recovery_required') from exc
        interrupted = False
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encode({'pid': os.getpid(), 'created_at': timestamp()}))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        except BaseException as exc:
            interrupted = not isinstance(exc, Exception)
            raise
        finally:
            if not interrupted:
                lock_path.unlink()

    def load(self, record_id):
        path = self.receipt_path(record_id)
        _info, mark, data = self.fingerprint(path.relative_to(self.root).as_posix(),
                                            limit=MAX_RECEIPT_BYTES, contents=True,
                                            internal=True)
        receipt = decode(data)
        validate_receipt(self, receipt, record_id)
        return receipt, {path.relative_to(self.root).as_posix(): mark}

    def save(self, receipt):
        path = self.receipt_path(receipt['id'])
        receipt['receipt_sha256'] = digest(encode({k: v for k, v in receipt.items()
                                                  if k != 'receipt_sha256'}))
        data = encode(receipt)
        if len(data) > MAX_RECEIPT_BYTES:
            raise EvidenceError('receipt_too_large')
        fd, temporary = tempfile.mkstemp(prefix='.evidence-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.receipt_path(receipt['id'])
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_report(store, report):
    if not isinstance(report, dict) or set(report) != {'verifier', 'checks', 'next_action'}:
        raise EvidenceError('invalid_report')
    text(report['verifier'])
    text(report['next_action'])
    checks = report['checks']
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_FILES:
        raise EvidenceError('invalid_checks')
    ids = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != CHECK_FIELDS:
            raise EvidenceError('invalid_check')
        for field in CHECK_FIELDS - {'evidence', 'preserve', 'next_check'}:
            text(check[field])
        for field in ('preserve', 'next_check'):
            text(check[field], required=False)
        if check['id'] in ids:
            raise EvidenceError('duplicate_check_id')
        ids.add(check['id'])
        if check['status'] not in ('verified', 'gap', 'failed', 'regression'):
            raise EvidenceError('invalid_check_status')
        refs = check['evidence']
        if not isinstance(refs, list) or len(refs) > MAX_FILES:
            raise EvidenceError('invalid_evidence_refs')
        for relative in refs:
            store.path(relative)
        if check['status'] == 'verified':
            if not refs or not check['preserve'].strip():
                raise EvidenceError('verified_requires_evidence_and_preserve')
        elif not check['next_check'].strip():
            raise EvidenceError('gap_requires_next_check')


def validate_files(store, records, *, allow_empty=False, limit=MAX_FILE_BYTES):
    if not isinstance(records, list) or len(records) > MAX_FILES or (not records and not allow_empty):
        raise EvidenceError('invalid_file_records')
    seen, total = set(), 0
    for info in records:
        if not isinstance(info, dict) or set(info) != {'path', 'sha256', 'size'}:
            raise EvidenceError('invalid_file_record')
        path = store.path(info['path'])
        key = path.relative_to(store.root).as_posix()
        if key in seen:
            raise EvidenceError('duplicate_file')
        seen.add(key)
        if (not isinstance(info['sha256'], str) or not HASH_RE.fullmatch(info['sha256'])
                or type(info['size']) is not int or not 0 <= info['size'] <= limit):
            raise EvidenceError('invalid_fingerprint')
        total += info['size']
    if total > MAX_SET_BYTES:
        raise EvidenceError('set_too_large')


def validate_receipt(store, receipt, record_id):
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise EvidenceError('invalid_receipt')
    if type(receipt['version']) is not int or receipt['version'] != 1 or receipt['id'] != record_id:
        raise EvidenceError('invalid_receipt')
    for field in ('task', 'objective', 'created_at'):
        text(receipt[field])
    for value in (receipt['created_at'], receipt['recorded_at']):
        if value is not None:
            try:
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError('timezone missing')
            except (ValueError, TypeError) as exc:
                raise EvidenceError('invalid_timestamp') from exc
    expected = digest(encode({k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
    if receipt['receipt_sha256'] != expected:
        raise EvidenceError('receipt_checksum_mismatch')
    validate_files(store, receipt['artifacts'])
    validate_files(store, receipt['evidence'], allow_empty=True)
    if receipt['report'] is None:
        if receipt['report_source'] is not None or receipt['recorded_at'] is not None or receipt['evidence']:
            raise EvidenceError('invalid_pending_receipt')
        return
    if receipt['recorded_at'] is None:
        raise EvidenceError('invalid_receipt')
    validate_report(store, receipt['report'])
    validate_files(store, [receipt['report_source']], limit=MAX_JSON_BYTES)
    expected_refs = {store.path(p).relative_to(store.root).as_posix()
                     for c in receipt['report']['checks'] for p in c['evidence']}
    actual_refs = {p['path'] for p in receipt['evidence']}
    if expected_refs != actual_refs:
        raise EvidenceError('evidence_reference_mismatch')
    report_key = receipt['report_source']['path']
    if report_key in actual_refs | {p['path'] for p in receipt['artifacts']}:
        raise EvidenceError('report_source_overlap')


def bindings(store, files, *, limit=MAX_FILE_BYTES):
    issues, marks = [], {}
    for saved in files:
        try:
            current, mark, _ = store.fingerprint(saved['path'], limit=limit)
            marks[current['path']] = mark
            if current['sha256'] != saved['sha256'] or current['size'] != saved['size']:
                issues.append({'path': saved['path'], 'reason': 'changed'})
        except FileNotFoundError:
            issues.append({'path': saved['path'], 'reason': 'missing'})
        except (OSError, EvidenceError):
            issues.append({'path': saved['path'], 'reason': 'unreadable_or_unsafe'})
    return issues, marks


def snapshot(store, args):
    text(args.task)
    text(args.objective)
    with store.lock():
        artifacts, marks = store.capture(args.artifact)
        receipt = dict(version=1, id=uuid.uuid4().hex, created_at=timestamp(), task=args.task,
                       objective=args.objective, artifacts=artifacts, report=None,
                       report_source=None, evidence=[], recorded_at=None)
        store.fresh_policy()
        store.sweep(marks)
        store.save(receipt)
    return {'ok': True, 'id': receipt['id'], 'path': f"{STORE_DIR}/{receipt['id']}.json",
            'status': 'pending'}


def record(store, record_id, report_path):
    with store.lock():
        receipt, receipt_marks = store.load(record_id)
        if receipt['report'] is not None:
            raise EvidenceError('already_recorded')
        issues, marks = bindings(store, receipt['artifacts'])
        if issues:
            raise EvidenceError('candidate_changed')
        source, source_mark, data = store.fingerprint(report_path, limit=MAX_JSON_BYTES, contents=True)
        report = decode(data)
        validate_report(store, report)
        # Normalize aliases before deduplicating shared evidence across checks.
        refs = {}
        for check in report['checks']:
            normalized = []
            for relative in check['evidence']:
                canonical = store.path(relative).relative_to(store.root).as_posix()
                refs[canonical] = canonical
                normalized.append(canonical)
            check['evidence'] = normalized
        if len(refs) > MAX_FILES:
            raise EvidenceError('invalid_file_count')
        overlaps = set(refs) | {p['path'] for p in receipt['artifacts']}
        if source['path'] in overlaps:
            raise EvidenceError('report_source_overlap')
        evidence, evidence_marks = store.capture(list(refs.values())) if refs else ([], {})
        # Overlap with the candidate is allowed as supporting white-box evidence,
        # but the final sweep must preserve the candidate's earlier observation.
        store.fresh_policy()
        store.sweep(marks)
        store.sweep(evidence_marks)
        store.sweep({source['path']: source_mark})
        store.sweep(receipt_marks, internal=True)
        receipt.update(report=report, report_source=source, evidence=evidence,
                       recorded_at=timestamp())
        store.save(receipt)
    return {'ok': True, 'id': record_id, 'path': f'{STORE_DIR}/{record_id}.json', 'status': 'recorded'}


def review(store, record_id, *, handoff=False):
    receipt, receipt_marks = store.load(record_id)
    files = receipt['artifacts'] + receipt['evidence']
    issues, marks = bindings(store, files)
    if receipt['report_source'] is not None:
        source_issues, source_marks = bindings(store, [receipt['report_source']], limit=MAX_JSON_BYTES)
        issues.extend(source_issues)
        marks.update(source_marks)
    try:
        store.fresh_policy()
        store.sweep(marks)
        store.sweep(receipt_marks, internal=True)
    except (OSError, EvidenceError):
        issues.append({'reason': 'changed_during_check'})
    status = 'stale' if issues else ('pending' if receipt['report'] is None else 'current')
    if status == 'pending':
        issues.append({'reason': 'report_not_recorded'})
    result = {'ok': status == 'current', 'id': record_id, 'task': receipt['task'],
              'objective': receipt['objective'], 'path': f'{STORE_DIR}/{record_id}.json',
              'status': status, 'valid': status == 'current', 'artifacts': receipt['artifacts'],
              'issues': issues, 'authority': 'evidence_data_only'}
    if handoff:
        result.update(preserve=[], gaps=[], next_action='Re-verify the current artifacts using a new snapshot.')
        if status == 'current':
            report = receipt['report']
            result['verifier'] = report['verifier']
            result['next_action'] = report['next_action']
            for check in report['checks']:
                if check['status'] == 'verified':
                    result['preserve'].append({'id': check['id'], 'claim': check['claim'],
                                               'condition': check['preserve'], 'evidence': check['evidence']})
                else:
                    result['gaps'].append({k: check[k] for k in ('id', 'claim', 'status', 'next_check')})
        else:
            result['gaps'] = issues or [{'reason': 'report_not_recorded'}]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    snap = commands.add_parser('snapshot', help='capture candidate file versions before verification')
    snap.add_argument('--task', required=True)
    snap.add_argument('--objective', required=True)
    snap.add_argument('--artifact', action='append', required=True)
    rec = commands.add_parser('record', help='attach one verifier report without executing its text')
    rec.add_argument('id')
    rec.add_argument('--report', required=True)
    for command in ('check', 'handoff'):
        commands.add_parser(command, help='inspect current file bindings without writing').add_argument('id')
    args = parser.parse_args(argv)
    try:
        store = Store(args.vault)
        if args.command == 'snapshot':
            result = snapshot(store, args)
        elif args.command == 'record':
            result = record(store, args.id, args.report)
        else:
            result = review(store, args.id, handoff=args.command == 'handoff')
        print(encode(result).decode('ascii'))
        return 0 if result['ok'] else 1
    except EvidenceError as exc:
        code = str(exc)
    except (OSError, ValueError, HealthcheckError, RecursionError, OverflowError):
        code = 'invalid_or_unreadable_input'
    print(encode({'ok': False, 'error': code}).decode('ascii'))
    return 2


if __name__ == '__main__':
    sys.exit(main())
