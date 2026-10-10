#!/usr/bin/env python3
"""Review, explicitly apply, or explicitly undo one revision-bound lesson proposal.

Receipts are local accounting, not signatures or proof of human identity.
Only cooperating writers are locked. Individual files are atomically replaced;
the target and receipt do not form a filesystem transaction.
"""
from __future__ import annotations

import argparse
import copy
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
from vault_evidence import EvidenceError, decode, regular, stable_read, stamp


MAX_NOTE_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 256 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
RECEIPT_DIR = '00-meta/proposals'
ID_RE = re.compile(r'[0-9a-f]{32}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
RESERVED_NOTE_DIRS = (RECEIPT_DIR, '00-meta/evidence', '00-meta/.agentic-vault/runtime')
ROLLBACK_BINDING_FIELDS = {'version', 'id', 'target', 'proposal_sha256',
                           'applied_receipt_sha256', 'applied_receipt_content_sha256',
                           'config_sha256', 'policy_sha256', 'current_sha256',
                           'candidate_sha256', 'original_sha256'}
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
    try:
        return decode(data)
    except EvidenceError as exc:
        raise ProposalError('invalid_json') from exc


def _stable(resolver, limit: int):
    try:
        return stable_read(resolver, limit, contents=True)
    except EvidenceError as exc:
        raise ProposalError('unsafe_or_changed_file') from exc


def read_stable(resolver, limit: int) -> bytes:
    return _stable(resolver, limit)[2]


class Store:
    def __init__(self, vault: Path):
        self.vault = vault
        _, self.config_mark, raw = _stable(
            lambda: resolve_note_path(vault, '00-meta/vault-config.json', ('.git',)), MAX_CONFIG_BYTES)
        self.config_sha256 = sha(raw)
        self.config = validate_config(decode_json(raw))
        self.denied = (*self.config['deny_zones'], '.git')
        self.policy_sha256 = sha(encode_json({'config': self.config,
                                             'reserved_note_dirs': RESERVED_NOTE_DIRS,
                                             'rollback_shared_state': 'partial_edit_required'}))
        # Config itself is checked again under its declared policy.
        self.path('00-meta/vault-config.json')

    def path(self, relative: str) -> Path:
        return resolve_note_path(self.vault, relative, self.denied)

    def note_path(self, relative: str, *, respect_exclusions: bool = False) -> Path:
        # Existing callers explicitly select ingest sources: retrieval exclusions
        # do not revoke that access. Undo opts into its stricter target boundary.
        path = (resolve_note_path(self.vault, relative, (*self.denied, *self.config['exclude_dirs']))
                if respect_exclusions else self.path(relative))
        canonical = path.relative_to(self.vault.resolve()).as_posix()
        if path.suffix.casefold() != '.md' or any(
                canonical.casefold() == reserved or canonical.casefold().startswith(reserved + '/')
                for reserved in RESERVED_NOTE_DIRS):
            raise ProposalError('invalid_markdown_path')
        return path

    def fresh(self):
        current = Store(self.vault)
        if (current.config_sha256 != self.config_sha256
                or current.policy_sha256 != self.policy_sha256):
            raise ProposalError('configuration_changed')
        return current

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
        receipt = decode_json(read_stable(lambda: self.receipt_path(proposal_id), MAX_RECEIPT_BYTES))
        validate_receipt(receipt, proposal_id)
        return receipt

    def save(self, receipt: dict, before_replace=None):
        receipt['receipt_sha256'] = sha(encode_json({k: v for k, v in receipt.items()
                                                   if k != 'receipt_sha256'}))
        data = encode_json(receipt) + b'\n'
        if len(data) > MAX_RECEIPT_BYTES:
            raise ProposalError('receipt_too_large')
        path = self.receipt_path(receipt['id'])
        def recheck():
            checked = self.receipt_path(receipt['id'])
            if checked.exists():
                read_stable(lambda: self.receipt_path(receipt['id']), MAX_RECEIPT_BYTES)
            if before_replace:
                before_replace()
        atomic_write(path, data, before_replace=recheck)


def atomic_write(path: Path, data: bytes, before_replace=None):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix='.proposal-', dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if read_stable(lambda: temporary_path, len(data)) != data:
            raise ProposalError('prepared_file_changed')
        os.chmod(temporary_path, mode)
        if read_stable(lambda: temporary_path, len(data)) != data:
            raise ProposalError('prepared_file_changed')
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
    if type(receipt['version']) is not int or receipt['version'] != 1 or receipt['id'] != proposal_id or receipt['owner'] != 'user':
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
        'rolling_back': ['proposed', 'check', 'approved', 'applying', 'applied',
                         'rollback_approved', 'rolling_back'],
        'rolled_back': ['proposed', 'check', 'approved', 'applying', 'applied',
                        'rollback_approved', 'rolling_back', 'rolled_back'],
    }
    if names != valid_histories.get(receipt['status']):
        raise ProposalError('invalid_history')
    if receipt['status'] in ('applying', 'applied', 'rolling_back', 'rolled_back'):
        if events[2].get('explicit_approval') is not True or events[3].get('candidate_sha256') != receipt['candidate_sha256']:
            raise ProposalError('invalid_checkpoint')
    if receipt['status'] in ('applied', 'rolling_back', 'rolled_back') and events[4].get('applied_sha256') != receipt['candidate_sha256']:
        raise ProposalError('invalid_history')
    if receipt['status'] in ('rolling_back', 'rolled_back'):
        validate_rollback_checkpoint(receipt)


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
    valid = current_sha == expected and receipt['status'] in ('proposed', 'applying', 'applied')
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


def validate_rollback_checkpoint(receipt: dict):
    events = receipt['events']
    approved, checkpoint = events[5:7]
    binding = checkpoint.get('binding')
    if (not isinstance(binding, dict) or set(binding) != ROLLBACK_BINDING_FIELDS
            or type(binding['version']) is not int or binding['version'] != 1
            or binding['id'] != receipt['id'] or binding['target'] != receipt['target']
            or binding['proposal_sha256'] != receipt['proposal_sha256']
            or binding['candidate_sha256'] != receipt['candidate_sha256']
            or binding['current_sha256'] != receipt['candidate_sha256']
            or binding['original_sha256'] != receipt['base_sha256']):
        raise ProposalError('invalid_rollback_checkpoint')
    for name in ROLLBACK_BINDING_FIELDS - {'version', 'id', 'target'}:
        if not isinstance(binding[name], str) or not HASH_RE.fullmatch(binding[name]):
            raise ProposalError('invalid_rollback_checkpoint')
    preview_sha = sha(encode_json(binding))
    applied = {k: v for k, v in receipt.items() if k != 'receipt_sha256'}
    applied.update(status='applied', events=events[:5])
    if (binding['applied_receipt_content_sha256'] != sha(encode_json(applied))
            or approved.get('explicit_approval') is not True
            or approved.get('preview_sha256') != preview_sha
            or checkpoint.get('preview_sha256') != preview_sha):
        raise ProposalError('invalid_rollback_checkpoint')
    if receipt['status'] == 'rolled_back' and (
            events[-1].get('restored_sha256') != receipt['base_sha256']
            or events[-1].get('preview_sha256') != preview_sha):
        raise ProposalError('invalid_rollback_checkpoint')


def _capture_mark(marks, name: str, mark):
    if marks is not None:
        if name in marks and marks[name] != mark:
            raise ProposalError('source_changed')
        marks[name] = mark


def _rollback_target(store: Store, receipt: dict, *, marks=None) -> bytes:
    target = store.note_path(receipt['target'], respect_exclusions=True)
    configured_handoff = store.config.get('handoff_note')
    if (target.name.casefold().endswith(('handoff.md', 'tasks.md'))
            or configured_handoff and target == store.note_path(configured_handoff)):
        raise ProposalError('shared_state_requires_partial_edit')
    # Restoration must still comply with ownership and the current schema.
    validate_change(store, receipt['target'], receipt['candidate'], receipt['original'])
    _, mark, current = _stable(lambda: store.note_path(receipt['target'], respect_exclusions=True),
                              MAX_NOTE_BYTES)
    _capture_mark(marks, 'target', mark)
    validate_change(store, receipt['target'], current.decode('utf-8'), receipt['original'])
    return current


def _receipt_fingerprint(store: Store, receipt: dict, *, marks=None) -> str:
    _, mark, raw = _stable(lambda: store.receipt_path(receipt['id']), MAX_RECEIPT_BYTES)
    _capture_mark(marks, 'receipt', mark)
    current = decode_json(raw)
    validate_receipt(current, receipt['id'])
    if current != receipt:
        raise ProposalError('receipt_changed')
    return sha(raw)


def _rollback_sweep(store: Store, receipt: dict, marks):
    paths = {'receipt': store.receipt_path(receipt['id']),
             'target': store.note_path(receipt['target'], respect_exclusions=True),
             'config': store.path('00-meta/vault-config.json')}
    expected = {**marks, 'config': store.config_mark}
    for name, path in paths.items():
        metadata = path.lstat()
        try:
            regular(metadata)
        except EvidenceError as exc:
            raise ProposalError('unsafe_or_changed_file') from exc
        if stamp(metadata) != expected[name]:
            raise ProposalError('source_changed')


def rollback_preview(store: Store, receipt: dict) -> dict:
    """Return a deterministic undo binding and diff without writing any file."""
    store = store.fresh()
    if receipt['status'] not in ('applied', 'rolling_back', 'rolled_back'):
        raise ProposalError('not_applied')
    marks = {}
    receipt_file_sha = _receipt_fingerprint(store, receipt, marks=marks)
    current_sha = sha(_rollback_target(store, receipt, marks=marks))
    if receipt['status'] == 'applied':
        if current_sha != receipt['candidate_sha256']:
            raise ProposalError('stale_target')
        binding = dict(version=1, id=receipt['id'], target=receipt['target'],
                       proposal_sha256=receipt['proposal_sha256'],
                       applied_receipt_sha256=receipt_file_sha,
                       applied_receipt_content_sha256=receipt['receipt_sha256'],
                       config_sha256=store.config_sha256, policy_sha256=store.policy_sha256,
                       current_sha256=current_sha, candidate_sha256=receipt['candidate_sha256'],
                       original_sha256=receipt['base_sha256'])
    else:
        binding = copy.deepcopy(receipt['events'][6]['binding'])
        if (binding['config_sha256'] != store.config_sha256
                or binding['policy_sha256'] != store.policy_sha256):
            raise ProposalError('configuration_changed')
        allowed = (receipt['base_sha256'], receipt['candidate_sha256']) if receipt['status'] == 'rolling_back' else (receipt['base_sha256'],)
        if current_sha not in allowed:
            raise ProposalError('stale_target')
    # Later reads can stale earlier observations; finish with a fresh sweep.
    store = store.fresh()
    if (_receipt_fingerprint(store, receipt, marks=marks) != receipt_file_sha
            or sha(_rollback_target(store, receipt, marks=marks)) != current_sha):
        raise ProposalError('source_changed')
    store = store.fresh()
    _rollback_sweep(store, receipt, marks)
    return {'id': receipt['id'], 'status': receipt['status'], 'binding': binding,
            'preview_sha256': sha(encode_json(binding)), 'current_sha256': current_sha,
            'recovery_available': receipt['status'] == 'rolling_back' and current_sha == receipt['base_sha256'],
            'diff': ''.join(difflib.unified_diff(
                receipt['candidate'].splitlines(keepends=True), receipt['original'].splitlines(keepends=True),
                fromfile=receipt['target'] + ' (candidate)', tofile=receipt['target'] + ' (restored original)'))}


def rollback(store: Store, receipt: dict, *, approve: bool, preview_hash: str) -> dict:
    """Restore one applied proposal under the caller-held cooperative lock.

    Approval flags record existing authorization. Interrupted recovery is only
    safe for the checkpoint's candidate/original bytes and matching policy.
    """
    if approve is not True:
        raise ProposalError('explicit_approval_required')
    if not isinstance(preview_hash, str) or not HASH_RE.fullmatch(preview_hash):
        raise ProposalError('invalid_preview_hash')
    preview = rollback_preview(store, receipt)
    if preview['preview_sha256'] != preview_hash:
        raise ProposalError('stale_preview')
    if receipt['status'] == 'rolled_back':
        return {'id': receipt['id'], 'status': 'rolled_back', 'idempotent': True}
    binding = preview['binding']
    receipt = copy.deepcopy(receipt)

    def guard(expected_receipt_sha: str, expected_target_sha: str):
        current_store = store.fresh()
        if (current_store.config_sha256 != binding['config_sha256']
                or current_store.policy_sha256 != binding['policy_sha256']):
            raise ProposalError('configuration_changed')
        current_receipt = current_store.load(receipt['id'])
        marks = {}
        if _receipt_fingerprint(current_store, current_receipt, marks=marks) != expected_receipt_sha:
            raise ProposalError('receipt_changed')
        if sha(_rollback_target(current_store, current_receipt, marks=marks)) != expected_target_sha:
            raise ProposalError('stale_target')
        current_store = current_store.fresh()
        _rollback_sweep(current_store, current_receipt, marks)

    if receipt['status'] == 'applied':
        receipt['events'].extend([
            event('rollback_approved', explicit_approval=True, preview_sha256=preview_hash),
            event('rolling_back', binding=binding, preview_sha256=preview_hash)])
        receipt['status'] = 'rolling_back'
        store.save(receipt, before_replace=lambda: guard(binding['applied_receipt_sha256'],
                                                         receipt['candidate_sha256']))
    checkpoint_sha = _receipt_fingerprint(store, receipt)
    if preview['current_sha256'] != receipt['base_sha256']:
        target = store.note_path(receipt['target'], respect_exclusions=True)
        atomic_write(target, receipt['original'].encode('utf-8'),
                     before_replace=lambda: guard(checkpoint_sha, receipt['candidate_sha256']))
    else:
        guard(checkpoint_sha, receipt['base_sha256'])
    receipt['events'].append(event('rolled_back', restored_sha256=receipt['base_sha256'],
                                   preview_sha256=preview_hash, validation='passed'))
    receipt['status'] = 'rolled_back'
    try:
        store.save(receipt, before_replace=lambda: guard(checkpoint_sha, receipt['base_sha256']))
    except (OSError, ValueError, ProposalError) as exc:
        raise ProposalError('incomplete_accounting') from exc
    return {'id': receipt['id'], 'status': 'rolled_back'}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Rejected values may contain credentials. Discard argparse's message
        # for both the top-level parser and every subcommand parser.
        print(json.dumps({'error': 'invalid_cli_arguments'}))
        self.exit(2)


def main(argv=None) -> int:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_ArgumentParser)
    new = commands.add_parser('propose')
    for flag in ('target', 'candidate', 'lesson', 'summary'):
        new.add_argument('--' + flag, required=True)
    for name in ('inspect', 'check', 'apply', 'reject', 'rollback-preview', 'rollback'):
        command = commands.add_parser(name)
        command.add_argument('id')
        if name in ('apply', 'rollback'):
            command.add_argument('--approve', action='store_true')
        if name == 'rollback':
            command.add_argument('--preview-hash', required=True)
        if name == 'reject':
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ('apply', 'rollback') and not args.approve:
            raise ProposalError('explicit_approval_required')
        store = Store(args.vault)
        if args.command in ('inspect', 'check', 'rollback-preview'):
            receipt = store.load(args.id)
            if args.command == 'inspect':
                validate_change(store, receipt['target'], receipt['original'], receipt['candidate'])
                result = dict(receipt)
                result['diff'] = ''.join(difflib.unified_diff(
                    receipt['original'].splitlines(keepends=True), receipt['candidate'].splitlines(keepends=True),
                    fromfile=receipt['target'] + ' (original)', tofile=receipt['target'] + ' (candidate)'))
            elif args.command == 'rollback-preview':
                result = rollback_preview(store, receipt)
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
                    elif args.command == 'rollback':
                        result = rollback(store, receipt, approve=args.approve, preview_hash=args.preview_hash)
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
