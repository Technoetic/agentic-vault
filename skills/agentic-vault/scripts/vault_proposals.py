#!/usr/bin/env python3
"""Review and explicitly apply one revision-bound Markdown lesson proposal.

Receipts are local accounting, not signatures or proof of human identity.
Only cooperating writers are locked. Individual files are atomically replaced;
the target and receipt do not form a filesystem transaction.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

from vault_healthcheck import HealthcheckError, _staged_schema_errors, validate_config
from vault_paths import resolve_note_path


MAX_NOTE_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
RECEIPT_DIR = '00-meta/proposals'
ID_RE = re.compile(r'[0-9a-f]{32}')
ENGINE_RE = re.compile(r'agentic-vault:(?:rule\s+engine=|generated\b|(?:healthcheck|hook)\s+engine=)', re.I)
MARKER_RE = re.compile(r'<!--\s*agentic-vault:(begin|end)\b.*?-->', re.S | re.I)
IMMUTABLE_FIELDS = ('version', 'id', 'lesson', 'summary', 'target', 'owner',
                    'base_sha256', 'original', 'candidate', 'candidate_sha256', 'created_at')


class ProposalError(Exception):
    """A safe, machine-readable error code."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_bounded(path: Path, limit: int) -> bytes:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ProposalError('unsafe_file')
    if metadata.st_size > limit:
        raise ProposalError('input_too_large')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ProposalError('input_too_large')
    return data


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProposalError('duplicate_json_key')
        value[key] = item
    return value


def decode_json(data: bytes):
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_unique_object)


class Store:
    def __init__(self, vault: Path):
        self.vault = vault
        config_path = resolve_note_path(vault, '00-meta/vault-config.json', ('.git',))
        self.config = validate_config(decode_json(read_bounded(config_path, MAX_CONFIG_BYTES)))
        self.denied = (*self.config['deny_zones'], '.git')
        # Config itself is checked again under its declared policy.
        self.path('00-meta/vault-config.json')

    def path(self, relative: str) -> Path:
        return resolve_note_path(self.vault, relative, self.denied)

    def note_path(self, relative: str) -> Path:
        path = self.path(relative)
        canonical = path.relative_to(self.vault.resolve()).as_posix()
        if path.suffix.casefold() != '.md' or canonical.casefold().startswith(RECEIPT_DIR + '/'):
            raise ProposalError('invalid_markdown_path')
        return path

    def receipt_path(self, proposal_id: str) -> Path:
        if not isinstance(proposal_id, str) or not ID_RE.fullmatch(proposal_id):
            raise ProposalError('invalid_id')
        return self.path(f'{RECEIPT_DIR}/{proposal_id}.json')

    @contextmanager
    def lock(self):
        directory = self.path(RECEIPT_DIR)
        directory.mkdir(exist_ok=True)
        lock_path = self.path(f'{RECEIPT_DIR}/.lock')
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ProposalError('locked_manual_recovery_required') from exc
        interrupted = False
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encode_json({'pid': os.getpid(), 'created_at': now()}))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        except BaseException as exc:
            interrupted = not isinstance(exc, Exception)
            raise
        finally:
            # An interruption retains evidence; never delete a pre-existing lock.
            if not interrupted:
                lock_path.unlink()

    def load(self, proposal_id: str) -> dict:
        receipt = decode_json(read_bounded(self.receipt_path(proposal_id), MAX_RECEIPT_BYTES))
        validate_receipt(receipt, proposal_id)
        return receipt

    def save(self, receipt: dict):
        receipt['receipt_sha256'] = sha(encode_json({k: v for k, v in receipt.items()
                                                   if k != 'receipt_sha256'}))
        data = encode_json(receipt) + b'\n'
        if len(data) > MAX_RECEIPT_BYTES:
            raise ProposalError('receipt_too_large')
        path = self.receipt_path(receipt['id'])
        atomic_write(path, data, before_replace=lambda: self.receipt_path(receipt['id']))


def atomic_write(path: Path, data: bytes, before_replace=None):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix='.proposal-', dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        if before_replace:
            before_replace()
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def managed_blocks(text: str) -> list[str]:
    blocks = []
    start = None
    markers = list(MARKER_RE.finditer(text))
    if len(markers) != len(re.findall(r'<!--\s*agentic-vault:(?:begin|end)\b', text, re.I)):
        raise ProposalError('malformed_managed_block')
    for marker in markers:
        if marker.group(1).lower() == 'begin':
            if start is not None:
                raise ProposalError('malformed_managed_block')
            start = marker.start()
        else:
            if start is None:
                raise ProposalError('malformed_managed_block')
            blocks.append(text[start:marker.end()])
            start = None
    if start is not None:
        raise ProposalError('malformed_managed_block')
    return blocks


def validate_change(store: Store, target: str, original: str, candidate: str):
    path = store.note_path(target)
    relative = path.relative_to(store.vault.resolve()).as_posix().casefold()
    rule_dirs = {'.claude/rules', store.config.get('rules_dir', '').casefold()}
    if (path.name.casefold().startswith('vault-')
            and any(directory and relative.startswith(directory + '/') for directory in rule_dirs)):
        raise ProposalError('engine_owned')
    if ENGINE_RE.search(original) or ENGINE_RE.search(candidate):
        raise ProposalError('engine_owned')
    if managed_blocks(original) != managed_blocks(candidate):
        raise ProposalError('managed_block_changed')
    # Reuse the existing pure schema checker; never run its Git/CLI paths.
    if Path(target).name.casefold() not in ('claude.md', 'agents.md'):
        if _staged_schema_errors(target, candidate.replace('\r\n', '\n'), store.config):
            raise ProposalError('invalid_frontmatter')


def validate_receipt(receipt, proposal_id: str):
    required = {*IMMUTABLE_FIELDS, 'proposal_sha256', 'receipt_sha256', 'status', 'events'}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ProposalError('invalid_receipt')
    if receipt['version'] != 1 or receipt['id'] != proposal_id or receipt['owner'] != 'user':
        raise ProposalError('invalid_receipt')
    for field in IMMUTABLE_FIELDS[1:]:
        if not isinstance(receipt[field], str):
            raise ProposalError('invalid_receipt')
    for field in ('original', 'candidate'):
        if len(receipt[field].encode('utf-8')) > MAX_NOTE_BYTES:
            raise ProposalError('input_too_large')
    if (receipt['base_sha256'] != sha(receipt['original'].encode('utf-8'))
            or receipt['candidate_sha256'] != sha(receipt['candidate'].encode('utf-8'))
            or receipt['proposal_sha256'] != sha(encode_json({k: receipt[k] for k in IMMUTABLE_FIELDS}))
            or receipt['receipt_sha256'] != sha(encode_json({k: v for k, v in receipt.items()
                                                           if k != 'receipt_sha256'}))):
        raise ProposalError('receipt_integrity_mismatch')
    events = receipt['events']
    if not isinstance(events, list) or not 1 <= len(events) <= 8 or not all(isinstance(e, dict) for e in events):
        raise ProposalError('invalid_history')
    names = [event.get('event') for event in events]
    valid_histories = {
        'proposed': ['proposed'],
        'rejected': ['proposed', 'rejected'],
        'applying': ['proposed', 'check', 'approved', 'applying'],
        'applied': ['proposed', 'check', 'approved', 'applying', 'applied'],
    }
    if names != valid_histories.get(receipt['status']):
        raise ProposalError('invalid_history')
    if receipt['status'] in ('applying', 'applied'):
        if events[2].get('explicit_approval') is not True or events[3].get('candidate_sha256') != receipt['candidate_sha256']:
            raise ProposalError('invalid_checkpoint')
    if receipt['status'] == 'applied' and events[-1].get('applied_sha256') != receipt['candidate_sha256']:
        raise ProposalError('invalid_history')


def event(name: str, **fields) -> dict:
    return {'event': name, 'at': now(), **fields}


def check(store: Store, receipt: dict) -> dict:
    validate_change(store, receipt['target'], receipt['original'], receipt['candidate'])
    current = read_bounded(store.note_path(receipt['target']), MAX_NOTE_BYTES).decode('utf-8')
    # Ownership is also checked against the actual file, not just receipt text.
    validate_change(store, receipt['target'], current, receipt['candidate'])
    current_sha = sha(current.encode('utf-8'))
    expected = receipt['candidate_sha256'] if receipt['status'] == 'applied' else receipt['base_sha256']
    if receipt['status'] == 'applying' and current_sha == receipt['candidate_sha256']:
        expected = current_sha
    valid = current_sha == expected and receipt['status'] != 'rejected'
    return {'id': receipt['id'], 'status': receipt['status'], 'valid': valid,
            'reason': 'ready' if valid else 'stale_or_rejected', 'current_sha256': current_sha,
            'recovery_available': receipt['status'] == 'applying' and current_sha == receipt['candidate_sha256']}


def propose(store: Store, args) -> dict:
    for text in (args.lesson, args.summary):
        if not text.strip() or len(text) > 2000:
            raise ProposalError('invalid_metadata')
    target = store.note_path(args.target)
    candidate_path = store.note_path(args.candidate)
    original = read_bounded(target, MAX_NOTE_BYTES).decode('utf-8')
    candidate = read_bounded(candidate_path, MAX_NOTE_BYTES).decode('utf-8')
    relative = target.relative_to(store.vault.resolve()).as_posix()
    validate_change(store, relative, original, candidate)
    if original == candidate:
        raise ProposalError('no_change')
    receipt = dict(version=1, id=uuid.uuid4().hex, lesson=args.lesson, summary=args.summary,
                   target=relative, owner='user', base_sha256=sha(original.encode('utf-8')),
                   original=original, candidate=candidate, candidate_sha256=sha(candidate.encode('utf-8')),
                   created_at=now())
    receipt['proposal_sha256'] = sha(encode_json(receipt))
    receipt.update(status='proposed', events=[event('proposed', validation='passed')])
    store.save(receipt)
    return {'id': receipt['id'], 'status': 'proposed'}


def apply(store: Store, receipt: dict) -> dict:
    result = check(store, receipt)
    if not result['valid']:
        raise ProposalError('stale_or_rejected')
    if receipt['status'] == 'applied':
        if result['current_sha256'] != receipt['candidate_sha256']:
            raise ProposalError('stale_target')
        return {'id': receipt['id'], 'status': 'applied', 'idempotent': True}
    if receipt['status'] == 'proposed':
        receipt['events'].extend([event('check', validation='passed', current_sha256=result['current_sha256']),
                                  event('approved', explicit_approval=True),
                                  event('applying', candidate_sha256=receipt['candidate_sha256'])])
        receipt['status'] = 'applying'
        store.save(receipt)
    target = store.note_path(receipt['target'])
    if result['current_sha256'] != receipt['candidate_sha256']:
        def recheck():
            # Fresh config, resolver, ownership and revision just before replace.
            latest = check(Store(store.vault), receipt)
            if not latest['valid'] or latest['current_sha256'] != receipt['base_sha256']:
                raise ProposalError('stale_target')
        atomic_write(target, receipt['candidate'].encode('utf-8'), before_replace=recheck)
    receipt['status'] = 'applied'
    receipt['events'].append(event('applied', applied_sha256=receipt['candidate_sha256'], validation='passed'))
    try:
        store.save(receipt)
    except (OSError, ValueError, ProposalError) as exc:
        raise ProposalError('incomplete_accounting') from exc
    return {'id': receipt['id'], 'status': 'applied'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    new = commands.add_parser('propose')
    for flag in ('target', 'candidate', 'lesson', 'summary'):
        new.add_argument('--' + flag, required=True)
    for name in ('inspect', 'check', 'apply', 'reject'):
        command = commands.add_parser(name)
        command.add_argument('id')
        if name == 'apply':
            command.add_argument('--approve', action='store_true')
        if name == 'reject':
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'apply' and not args.approve:
            raise ProposalError('explicit_approval_required')
        store = Store(args.vault)
        if args.command in ('inspect', 'check'):
            receipt = store.load(args.id)
            if args.command == 'inspect':
                validate_change(store, receipt['target'], receipt['original'], receipt['candidate'])
                result = dict(receipt)
                result['diff'] = ''.join(difflib.unified_diff(
                    receipt['original'].splitlines(keepends=True), receipt['candidate'].splitlines(keepends=True),
                    fromfile=receipt['target'] + ' (original)', tofile=receipt['target'] + ' (candidate)'))
            else:
                result = check(store, receipt)
        else:
            with store.lock():
                if args.command == 'propose':
                    result = propose(store, args)
                else:
                    receipt = store.load(args.id)
                    if args.command == 'apply':
                        result = apply(store, receipt)
                    else:
                        if receipt['status'] != 'proposed':
                            raise ProposalError('not_pending')
                        if not args.reason.strip() or len(args.reason) > 2000:
                            raise ProposalError('invalid_reason')
                        receipt['status'] = 'rejected'
                        receipt['events'].append(event('rejected', reason=args.reason))
                        store.save(receipt)
                        result = {'id': args.id, 'status': 'rejected'}
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result.get('valid', True) else 1
    except ProposalError as exc:
        print(json.dumps({'error': str(exc)}))
    except (OSError, ValueError, HealthcheckError, TypeError, KeyError, RecursionError):
        # Never echo paths, exception strings, config content or candidate data.
        print(json.dumps({'error': 'invalid_or_unavailable_input'}))
    return 1


if __name__ == '__main__':
    sys.exit(main())
