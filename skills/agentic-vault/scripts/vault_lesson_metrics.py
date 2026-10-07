#!/usr/bin/env python3
"""Append observations for existing lesson proposals without applying any lesson.

Checksums and chain links detect ordinary corruption; they are not signatures.
An operator who rewrites checksums or removes the final chain suffix can alter
local history. Locks serialize cooperating writers, not arbitrary filesystem
edits. Evidence binds reported checks to files, never truth, identity or approval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

import vault_evidence as evidence
import vault_proposals as proposals
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import resolve_note_path


CONFIG_PATH = '00-meta/vault-config.json'
STORE_DIR = '00-meta/proposals/observations'
MAX_INPUT_BYTES = 16 * 1024
MAX_OBSERVATION_BYTES = 16 * 1024
MAX_OBSERVATIONS = 128
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_IDS = 16
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_READ_BYTES = 32 * 1024 * 1024
ID_RE = re.compile(r'[0-9a-f]{32}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
APPLICABILITY = ('applicable', 'not_applicable', 'unknown')
OUTCOMES = ('success', 'recurrence', 'adverse', 'unknown')
VERIFICATION = ('verified', 'unverified', 'unknown')
EVIDENCE_STATES = ('current', 'unknown', 'stale', 'unverified')
INPUT_FIELDS = {'attempt_id', 'session_id', 'applicability', 'outcome',
                'verification_status', 'evidence_ids'}
OBSERVATION_FIELDS = {
    'version', 'id', 'proposal_id', 'proposal_sha256', 'created_at', 'sequence',
    'previous_sha256', 'attempt_id', 'session_id', 'applicability', 'outcome',
    'requested_verification_status', 'verification_status', 'evidence',
    'proposal_status_at_record', 'candidate_sha256', 'target_sha256_at_record',
    'eligible_at_record', 'observation_sha256',
}


class LessonMetricsError(Exception):
    """A non-sensitive, machine-readable operational error."""


def _id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise LessonMetricsError('invalid_id')


def _identifier(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(c) < 32 for c in value)):
        raise LessonMetricsError('invalid_identifier')


def _checksum(value):
    return hashlib.sha256(evidence.encode(value)).hexdigest()


def _validate_input(observation):
    if not isinstance(observation, dict):
        raise LessonMetricsError('invalid_observation')
    if not INPUT_FIELDS - {'evidence_ids'} <= set(observation) <= INPUT_FIELDS:
        raise LessonMetricsError('invalid_observation')
    if len(evidence.encode(observation)) > MAX_INPUT_BYTES:
        raise LessonMetricsError('input_too_large')
    for field in ('attempt_id', 'session_id'):
        _identifier(observation[field])
    if observation['applicability'] not in APPLICABILITY:
        raise LessonMetricsError('invalid_applicability')
    if observation['outcome'] not in OUTCOMES:
        raise LessonMetricsError('invalid_outcome')
    if observation['verification_status'] not in VERIFICATION:
        raise LessonMetricsError('invalid_verification_status')
    ids = observation.get('evidence_ids', [])
    if not isinstance(ids, list) or len(ids) > MAX_EVIDENCE_IDS:
        raise LessonMetricsError('invalid_evidence_ids')
    for record_id in ids:
        _id(record_id)
    if len(set(ids)) != len(ids):
        raise LessonMetricsError('duplicate_evidence_id')
    return {**observation, 'evidence_ids': list(ids)}


def _effective_verification(requested, records):
    if requested == 'unknown':
        return 'unknown'
    if requested == 'verified' and records and all(r['status'] == 'current' for r in records):
        return 'verified'
    return 'unverified'


class Store:
    def __init__(self, vault):
        self.vault = Path(vault)
        self.root = self.vault.resolve(strict=True)
        self.marks = {}
        self.absent = set()
        self.read_bytes = 0
        self.evidence_cache = {}
        self.denied = ('.git',)
        info, _mark, data = self.read(CONFIG_PATH, limit=evidence.MAX_JSON_BYTES,
                                      contents=True, internal=True)
        self.config_sha256 = info['sha256']
        self.config = validate_config(evidence.decode(data))
        self.denied = (*self.config['deny_zones'], *self.config['exclude_dirs'], '.git')
        self.path(CONFIG_PATH, internal=True)

    def path(self, relative, *, internal=False):
        try:
            result = resolve_note_path(self.vault, relative, self.denied)
        except ValueError as exc:
            raise LessonMetricsError('unsafe_path') from exc
        canonical = result.relative_to(self.root).as_posix().casefold()
        if not internal and any(canonical == reserved or canonical.startswith(reserved + '/')
                                for reserved in (proposals.RECEIPT_DIR, evidence.STORE_DIR)):
            raise LessonMetricsError('reserved_store_path')
        return result

    def read(self, relative, *, limit, contents=False, internal=False, capture=True):
        path = self.path(relative, internal=internal)
        size = path.lstat().st_size
        if size > limit:
            raise LessonMetricsError('input_too_large')
        if size > MAX_READ_BYTES - self.read_bytes:
            raise LessonMetricsError('read_budget_exceeded')
        info, mark, data = evidence.stable_read(
            lambda: self.path(relative, internal=internal),
            min(limit, MAX_READ_BYTES - self.read_bytes), contents=contents)
        self.read_bytes += info['size']
        canonical = path.relative_to(self.root).as_posix()
        if capture:
            previous = self.marks.get(canonical)
            current = (info, mark, limit, internal)
            if previous and previous[:2] != current[:2]:
                raise LessonMetricsError('source_changed')
            self.marks[canonical] = current
        return info, mark, data

    def fresh_policy(self):
        info, _mark, _data = self.read(CONFIG_PATH, limit=evidence.MAX_JSON_BYTES,
                                      internal=True, capture=False)
        if info['sha256'] != self.config_sha256:
            raise LessonMetricsError('configuration_changed')

    def sweep(self):
        for relative, (_info, expected, _limit, internal) in self.marks.items():
            metadata = self.path(relative, internal=internal).lstat()
            evidence.regular(metadata)
            if evidence.stamp(metadata) != expected:
                raise LessonMetricsError('source_changed')
        for relative in self.absent:
            if self.path(relative, internal=True).exists():
                raise LessonMetricsError('source_changed')

    def recheck(self):
        self.fresh_policy()
        for relative, (expected, stamp, limit, internal) in list(self.marks.items()):
            current, mark, _data = self.read(relative, limit=limit, internal=internal,
                                             capture=False)
            if current != expected or mark != stamp:
                raise LessonMetricsError('source_changed')
        # Hashing a later source may stale an earlier source or the policy.
        # Sweep after all hash reads, and again after the final policy read.
        self.sweep()
        self.fresh_policy()
        self.sweep()

    def proposal(self, proposal_id):
        _id(proposal_id)
        _info, _mark, data = self.read(f'{proposals.RECEIPT_DIR}/{proposal_id}.json',
                                      limit=proposals.MAX_RECEIPT_BYTES,
                                      contents=True, internal=True)
        receipt = evidence.decode(data)
        proposals.validate_receipt(receipt, proposal_id)
        if type(receipt['version']) is not int:
            raise LessonMetricsError('invalid_receipt')
        target = self.path(receipt['target'])
        if target.suffix.casefold() != '.md':
            raise LessonMetricsError('invalid_markdown_path')
        try:
            info, _mark, _data = self.read(receipt['target'], limit=proposals.MAX_NOTE_BYTES)
            target_sha = info['sha256']
        except FileNotFoundError:
            self.absent.add(receipt['target'])
            target_sha = None
        eligible = receipt['status'] == 'applied' and target_sha == receipt['candidate_sha256']
        reason = ('eligible' if eligible else 'not_applied' if receipt['status'] != 'applied'
                  else 'target_missing' if target_sha is None else 'target_drifted')
        return receipt, {'eligible': eligible, 'reason': reason, 'target_sha256': target_sha}

    def evidence(self, record_id):
        if record_id in self.evidence_cache:
            return dict(self.evidence_cache[record_id])
        relative = f'{evidence.STORE_DIR}/{record_id}.json'
        result = {'id': record_id, 'status': 'unverified', 'receipt_sha256': None}
        try:
            _info, _mark, data = self.read(relative, limit=evidence.MAX_RECEIPT_BYTES,
                                          contents=True, internal=True)
            receipt = evidence.decode(data)
            evidence.validate_receipt(self, receipt, record_id)
            result['receipt_sha256'] = receipt['receipt_sha256']
            if receipt['report'] is not None:
                files = receipt['artifacts'] + receipt['evidence'] + [receipt['report_source']]
                stale = False
                for saved in files:
                    current, _mark, _data = self.read(saved['path'], limit=MAX_EVIDENCE_BYTES)
                    stale |= (current['sha256'] != saved['sha256'] or current['size'] != saved['size'])
                if stale:
                    result['status'] = 'stale'
                elif all(c['status'] == 'verified' for c in receipt['report']['checks']):
                    result['status'] = 'current'
        except FileNotFoundError:
            # A missing receipt is unknown; a known receipt with missing sources is stale.
            result['status'] = 'stale' if result['receipt_sha256'] else 'unknown'
            if result['receipt_sha256'] is None:
                self.absent.add(relative)
        except LessonMetricsError as exc:
            if str(exc) in ('read_budget_exceeded', 'source_changed'):
                raise
        except (OSError, ValueError, evidence.EvidenceError, RecursionError):
            pass
        self.evidence_cache[record_id] = dict(result)
        return result

    def names(self, proposal_id, *, ignore=None):
        directory = self.path(f'{STORE_DIR}/{proposal_id}', internal=True)
        try:
            with os.scandir(directory) as entries:
                result = []
                for entry in entries:
                    if entry.name == ignore:
                        continue
                    if len(result) >= MAX_OBSERVATIONS:
                        raise LessonMetricsError('observation_count_exceeded')
                    if not re.fullmatch(r'[0-9a-f]{32}\.json', entry.name):
                        raise LessonMetricsError('unexpected_observation_entry')
                    result.append(entry.name)
                return sorted(result)
        except FileNotFoundError:
            return []

    def observations(self, receipt):
        names = self.names(receipt['id'])
        result, total = [], 0
        for name in names:
            _info, _mark, data = self.read(f"{STORE_DIR}/{receipt['id']}/{name}",
                                          limit=MAX_OBSERVATION_BYTES, contents=True, internal=True)
            total += len(data)
            if total > MAX_STORE_BYTES:
                raise LessonMetricsError('observation_store_too_large')
            record = evidence.decode(data)
            _validate_record(record, receipt, name[:-5])
            result.append(record)
        result.sort(key=lambda record: record['sequence'])
        previous, attempts = None, set()
        for sequence, record in enumerate(result, 1):
            if record['sequence'] != sequence or record['previous_sha256'] != previous:
                raise LessonMetricsError('observation_chain_mismatch')
            if record['attempt_id'] in attempts:
                raise LessonMetricsError('duplicate_attempt')
            attempts.add(record['attempt_id'])
            previous = record['observation_sha256']
        return result, names, total

    def _mkdir(self, relative):
        path = self.path(relative, internal=True)
        path.mkdir(exist_ok=True)
        current = self.path(relative, internal=True)
        if current != path or not current.is_dir():
            raise LessonMetricsError('unsafe_store')
        return current

    @contextmanager
    def lock(self):
        self._mkdir(STORE_DIR)
        path = self.path(f'{STORE_DIR}/.lock', internal=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        flags |= getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise LessonMetricsError('locked_manual_recovery_required') from exc
        interrupted = False
        identity = None
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                identity = evidence.stamp(os.fstat(stream.fileno()))[:2]
                stream.write(evidence.encode({'pid': os.getpid(), 'created_at':
                                             datetime.now(timezone.utc).isoformat()}))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        except BaseException as exc:
            interrupted = not isinstance(exc, Exception)
            raise
        finally:
            if not interrupted:
                # Never delete another writer's substituted lock or traverse a new link.
                current = self.path(f'{STORE_DIR}/.lock', internal=True)
                if identity is not None and evidence.stamp(current.lstat())[:2] == identity:
                    current.unlink()

    def publish(self, record, names, total):
        data = evidence.encode(record) + b'\n'
        if len(data) > MAX_OBSERVATION_BYTES or total + len(data) > MAX_STORE_BYTES:
            raise LessonMetricsError('observation_store_too_large')
        directory = self._mkdir(f"{STORE_DIR}/{record['proposal_id']}")
        relative = f"{STORE_DIR}/{record['proposal_id']}/{record['id']}.json"
        path = self.path(relative, internal=True)
        fd, temporary = tempfile.mkstemp(prefix='.observation-', suffix='.tmp', dir=directory)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                temporary_mark = evidence.stamp(os.fstat(stream.fileno()))
            # Our temporary is deliberately excluded from the committed-file enumeration.
            current_names = self.names(record['proposal_id'], ignore=temporary.name)
            if current_names != names:
                raise LessonMetricsError('observation_store_changed')
            if self.path(relative, internal=True) != path:
                raise LessonMetricsError('unsafe_path')
            checked_temporary = self.path(temporary.relative_to(self.root).as_posix(), internal=True)
            temporary_info, current_mark, _data = self.read(
                checked_temporary.relative_to(self.root).as_posix(), limit=MAX_OBSERVATION_BYTES,
                internal=True)
            if (temporary_info != {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                    or current_mark != temporary_mark):
                raise LessonMetricsError('temporary_observation_changed')
            # Inventory and temporary hash reads must precede the final policy
            # and source sweep. The temporary also remains bound through it.
            self.recheck()
            # link() publishes complete bytes atomically and refuses to overwrite on all
            # supported platforms. Removing the temporary leaves one regular-file link.
            try:
                os.link(checked_temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise LessonMetricsError('observation_already_exists') from exc
        finally:
            checked = self.path(temporary.relative_to(self.root).as_posix(), internal=True)
            if checked.exists():
                checked.unlink()


def _validate_record(record, receipt, record_id):
    if not isinstance(record, dict) or set(record) != OBSERVATION_FIELDS:
        raise LessonMetricsError('invalid_observation_record')
    if (type(record['version']) is not int or record['version'] != 1 or record['id'] != record_id
            or record['proposal_id'] != receipt['id']
            or record['proposal_sha256'] != receipt['proposal_sha256']
            or record['candidate_sha256'] != receipt['candidate_sha256']):
        raise LessonMetricsError('observation_identity_mismatch')
    _id(record_id)
    if type(record['sequence']) is not int or not 1 <= record['sequence'] <= MAX_OBSERVATIONS:
        raise LessonMetricsError('invalid_observation_sequence')
    for field in ('previous_sha256', 'target_sha256_at_record'):
        value = record[field]
        if value is not None and (not isinstance(value, str) or not HASH_RE.fullmatch(value)):
            raise LessonMetricsError('invalid_observation_hash')
    try:
        if datetime.fromisoformat(record['created_at']).tzinfo is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise LessonMetricsError('invalid_observation_timestamp') from exc
    if record['proposal_status_at_record'] not in ('proposed', 'rejected', 'applying', 'applied'):
        raise LessonMetricsError('invalid_proposal_status')
    refs = record['evidence']
    if not isinstance(refs, list) or len(refs) > MAX_EVIDENCE_IDS:
        raise LessonMetricsError('invalid_observation_evidence')
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'id', 'status', 'receipt_sha256'}:
            raise LessonMetricsError('invalid_observation_evidence')
        _id(ref['id'])
        if ref['status'] not in EVIDENCE_STATES:
            raise LessonMetricsError('invalid_observation_evidence_status')
        fingerprint = ref['receipt_sha256']
        if fingerprint is not None and (not isinstance(fingerprint, str)
                                        or not HASH_RE.fullmatch(fingerprint)):
            raise LessonMetricsError('invalid_observation_evidence_hash')
        if ref['status'] in ('current', 'stale') and fingerprint is None:
            raise LessonMetricsError('invalid_observation_evidence_hash')
    _validate_input({k: record[k] for k in ('attempt_id', 'session_id', 'applicability', 'outcome')}
                    | {'verification_status': record['requested_verification_status'],
                       'evidence_ids': [ref['id'] for ref in refs]})
    expected_eligible = (record['proposal_status_at_record'] == 'applied'
                         and record['target_sha256_at_record'] == receipt['candidate_sha256'])
    if type(record['eligible_at_record']) is not bool or record['eligible_at_record'] != expected_eligible:
        raise LessonMetricsError('invalid_observation_eligibility')
    if record['verification_status'] != _effective_verification(record['requested_verification_status'], refs):
        raise LessonMetricsError('invalid_observation_verification')
    expected = _checksum({k: v for k, v in record.items() if k != 'observation_sha256'})
    if record['observation_sha256'] != expected:
        raise LessonMetricsError('observation_integrity_mismatch')


def _record(store, proposal_id, observation):
    observation = _validate_input(observation)
    receipt, eligibility = store.proposal(proposal_id)
    with store.lock():
        records, names, total = store.observations(receipt)
        if len(records) >= MAX_OBSERVATIONS:
            raise LessonMetricsError('observation_count_exceeded')
        if any(r['attempt_id'] == observation['attempt_id'] for r in records):
            raise LessonMetricsError('duplicate_attempt')
        refs = [store.evidence(record_id) for record_id in observation['evidence_ids']]
        record = dict(version=1, id=uuid.uuid4().hex, proposal_id=proposal_id,
                      proposal_sha256=receipt['proposal_sha256'],
                      created_at=datetime.now(timezone.utc).isoformat(),
                      sequence=len(records) + 1,
                      previous_sha256=records[-1]['observation_sha256'] if records else None,
                      attempt_id=observation['attempt_id'], session_id=observation['session_id'],
                      applicability=observation['applicability'], outcome=observation['outcome'],
                      requested_verification_status=observation['verification_status'],
                      verification_status=_effective_verification(observation['verification_status'], refs),
                      evidence=refs, proposal_status_at_record=receipt['status'],
                      candidate_sha256=receipt['candidate_sha256'],
                      target_sha256_at_record=eligibility['target_sha256'],
                      eligible_at_record=eligibility['eligible'])
        record['observation_sha256'] = _checksum(record)
        _validate_record(record, receipt, record['id'])
        store.publish(record, names, total)
    return {'ok': True, 'id': record['id'], 'proposal_id': proposal_id,
            'path': f"{STORE_DIR}/{proposal_id}/{record['id']}.json",
            'verification_status': record['verification_status'],
            'eligible_at_record': record['eligible_at_record'], 'authority': 'observation_data_only'}


def _summarize(store, proposal_id):
    receipt, eligibility = store.proposal(proposal_id)
    records, names, _total = store.observations(receipt)
    counts = dict(observations=len(records), eligible_attempts=0, ineligible_attempts=0,
                  known_outcome_attempts=0, verified_attempts=0, verified_known_outcome_attempts=0,
                  applicability={key: 0 for key in APPLICABILITY},
                  outcomes={key: 0 for key in OUTCOMES}, verified_outcomes={key: 0 for key in OUTCOMES})
    views = []
    for record in records:
        refs = []
        for saved in record['evidence']:
            current = store.evidence(saved['id'])
            if saved['receipt_sha256'] is not None and current['receipt_sha256'] != saved['receipt_sha256']:
                current['status'] = 'stale'
            refs.append({**current, 'status_at_record': saved['status']})
        verification = record['verification_status']
        if verification == 'verified':
            verification = _effective_verification('verified', refs)
        applicable = record['applicability'] == 'applicable'
        eligible = record['eligible_at_record'] and applicable
        counts['applicability'][record['applicability']] += 1
        if eligible:
            counts['eligible_attempts'] += 1
            counts['outcomes'][record['outcome']] += 1
            if record['outcome'] != 'unknown':
                counts['known_outcome_attempts'] += 1
            if verification == 'verified':
                counts['verified_attempts'] += 1
                counts['verified_outcomes'][record['outcome']] += 1
                if record['outcome'] != 'unknown':
                    counts['verified_known_outcome_attempts'] += 1
        else:
            counts['ineligible_attempts'] += 1
        views.append({k: record[k] for k in ('id', 'sequence', 'attempt_id', 'session_id',
                                             'applicability', 'outcome', 'eligible_at_record')}
                     | {'evidence': refs, 'verification_status': verification,
                        'eligible_for_effectiveness': eligible})
    known, verified_known = counts['known_outcome_attempts'], counts['verified_known_outcome_attempts']
    rates = {'observed_success_rate': counts['outcomes']['success'] / known if known else None,
             'observed_recurrence_rate': counts['outcomes']['recurrence'] / known if known else None,
             'observed_adverse_rate': counts['outcomes']['adverse'] / known if known else None,
             'verified_success_rate': counts['verified_outcomes']['success'] / verified_known if verified_known else None,
             'verified_recurrence_rate': counts['verified_outcomes']['recurrence'] / verified_known if verified_known else None,
             'verified_adverse_rate': counts['verified_outcomes']['adverse'] / verified_known if verified_known else None}
    if store.names(proposal_id) != names:
        raise LessonMetricsError('observation_store_changed')
    # Inventory reads can stale sources too, so verify bindings afterward.
    store.recheck()
    return {'ok': True, 'proposal_id': proposal_id, 'proposal_sha256': receipt['proposal_sha256'],
            'proposal_status': receipt['status'], 'current_eligibility': eligibility,
            'counts': counts, 'rates': rates, 'observations': views,
            'authority': 'observation_data_only',
            'denominator': 'applicable attempts with applied current target at record time; known outcomes for rates',
            'verification_meaning': 'host-reported verified checks with current file bindings; no truth or approval guarantee'}


def _safe_call(callback):
    try:
        return callback()
    except LessonMetricsError:
        raise
    except (evidence.EvidenceError, proposals.ProposalError) as exc:
        raise LessonMetricsError(str(exc)) from exc
    except (OSError, ValueError, HealthcheckError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise LessonMetricsError('invalid_or_unavailable_input') from exc


def record_observation(vault, proposal_id, observation) -> dict:
    """Explicitly append one observation; never approve, apply or mutate a receipt."""
    _id(proposal_id)
    return _safe_call(lambda: _record(Store(vault), proposal_id, observation))


def summarize(vault, proposal_id) -> dict:
    """Read historical eligible attempts and refresh their evidence bindings."""
    _id(proposal_id)
    return _safe_call(lambda: _summarize(Store(vault), proposal_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('record', 'summarize'):
        command = commands.add_parser(name)
        command.add_argument('--proposal-id', required=True)
        if name == 'record':
            command.add_argument('--input', required=True, help='explicit vault-relative observation JSON')
    args = parser.parse_args(argv)
    try:
        _id(args.proposal_id)
        if args.command == 'record':
            def explicit_record():
                store = Store(args.vault)
                if store.path(args.input).suffix.casefold() != '.json':
                    raise LessonMetricsError('invalid_input_path')
                _info, _mark, data = store.read(args.input, limit=MAX_INPUT_BYTES, contents=True)
                return _record(store, args.proposal_id, evidence.decode(data))
            result = _safe_call(explicit_record)
        else:
            result = summarize(args.vault, args.proposal_id)
        print(evidence.encode(result).decode('ascii'))
        return 0
    except LessonMetricsError as exc:
        print(evidence.encode({'ok': False, 'error': str(exc)}).decode('ascii'))
        return 2


if __name__ == '__main__':
    sys.exit(main())
