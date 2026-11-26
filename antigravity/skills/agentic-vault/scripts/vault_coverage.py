#!/usr/bin/env python3
"""Opt-in, offline requirements-to-evidence data completeness checks.

An explicit manifest declares REQ IDs, AC mappings and required check IDs before
examining a selected vault_evidence receipt. A complete mapping means only that
the declared data and current file bindings are present. It neither proves
semantic fulfillment nor independently observes execution. This module never
executes a method, invokes a model, publishes a record or changes vault state.
"""
from __future__ import annotations

import argparse
from functools import wraps
from pathlib import Path
import re

import vault_evidence as evidence
import vault_runs as runs
import vault_state as state
from jev_client import _matches_sensitive, contains_sensitive
from vault_healthcheck import HealthcheckError

MAX_MANIFEST_BYTES = 64 * 1024
MAX_DEPTH = 8
MAX_ITEMS = 64
MAX_NODES = 16384
LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}')
HASH_RE = re.compile(r'[0-9a-f]{64}')
AUTHORITY = 'coverage_data_only'
REVIEW_STATUSES = frozenset(('unchecked', 'independent_reviewed'))
AUTOMATION_METHODS = frozenset(('agent', 'human', 'hybrid', 'script'))
REQUIRED_MANIFEST_KEYS = frozenset(('version', 'task', 'requirements', 'acceptance', 'required_checks'))
OPTIONAL_MANIFEST_KEYS = frozenset((
    'review_status', 'automation_methods', 'spec_files', 'solution_files', 'permitted_gaps',
))
SECRET_SEGMENTS = frozenset(('.git', '.ssh', '.aws', '.azure', '.gnupg', 'secrets', 'credentials'))
SECRET_SUFFIXES = frozenset(('.key', '.pem', '.p12', '.pfx', '.jks', '.kdbx'))
SECRET_BASENAMES = frozenset(('.npmrc', '.pypirc', '.netrc', '.dockercfg', '.git-credentials'))
SECRET_STEMS = frozenset(('secret', 'secrets', 'credential', 'credentials', 'token', 'tokens',
                        'service-account', 'service_account', 'api-key', 'api-keys',
                        'api_key', 'api_keys', 'auth-config', 'auth_config',
                        'secret-config', 'secret_config', 'secrets-config', 'secrets_config',
                        'credentials-config', 'credentials_config', 'token-config', 'token_config'))
LIMITATIONS = (
    'Mappings do not prove semantic fulfillment of requirements or acceptance criteria.',
    'Submitted statuses are host observations; execution is not independently verified.',
    'Missing results mean unexecuted or unreported; these alternatives are not distinguished.',
    'Fingerprints bind selected current bytes; checksums are not signatures or authorization.',
    'Only explicitly selected files and declared IDs are checked; no vault scan is performed.',
    'review_status and automation_methods are caller-declared metadata, not authenticated human sign-off.',
)


class CoverageError(Exception):
    """An operational error code that never includes submitted content."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CoverageError:
            raise
        except evidence.EvidenceError as exc:
            code = ('unsupported_evidence_outcome' if str(exc) == 'invalid_check_status'
                    else 'unsafe_or_invalid_evidence')
            raise CoverageError(code) from exc
        except (runs.RunError, state.StateError, HealthcheckError, ValueError, OSError,
                UnicodeError, RecursionError, OverflowError, TypeError, RuntimeError) as exc:
            raise CoverageError('unsafe_or_invalid_input') from exc
    return call


def _bounded(value, *, depth=MAX_DEPTH):
    """Bound parsed trees before any recursive sensitive-value inspection."""
    pending, visited = [(value, 0)], 0
    while pending:
        item, level = pending.pop()
        visited += 1
        if level > depth or visited > MAX_NODES:
            raise CoverageError('input_too_complex')
        if isinstance(item, dict):
            pending.extend((part, level + 1) for pair in item.items() for part in pair)
        elif isinstance(item, list):
            pending.extend((part, level + 1) for part in item)


def _fields(value, fields, code):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise CoverageError(code)


def _id(value, prefix=''):
    if (not isinstance(value, str) or not LABEL_RE.fullmatch(value)
            or (prefix and not value.startswith(prefix)) or value == prefix
            or _matches_sensitive(value)):
        raise CoverageError('invalid_id')
    return value


def _ids(value, *, prefix='', nonempty=False):
    if (not isinstance(value, list) or len(value) > MAX_ITEMS
            or (nonempty and not value)):
        raise CoverageError('invalid_id_list')
    ids = [_id(item, prefix) for item in value]
    if len(set(ids)) != len(ids):
        raise CoverageError('duplicate_id')
    return sorted(ids)


def _rel_path(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or value != value.strip() or '\\' in value or value.startswith('/')
            or ':' in value or any(ord(char) < 32 for char in value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise CoverageError('invalid_path')
    return value


def _manifest(value):
    _bounded(value)
    if contains_sensitive(value):
        raise CoverageError('sensitive_input')
    if (not isinstance(value, dict)
            or not REQUIRED_MANIFEST_KEYS <= value.keys()
            or (value.keys() - REQUIRED_MANIFEST_KEYS - OPTIONAL_MANIFEST_KEYS)):
        raise CoverageError('invalid_manifest')
    task = value['task']
    if (type(value['version']) is not int or value['version'] != 1
            or not isinstance(task, str) or not task.strip() or len(task) > 256
            or any(ord(char) < 32 for char in task)
            or not isinstance(value['acceptance'], list)
            or len(value['acceptance']) > MAX_ITEMS):
        raise CoverageError('invalid_manifest')
    requirements = _ids(value['requirements'], prefix='REQ-', nonempty=True)
    checks = _ids(value['required_checks'], nonempty=True)
    acceptance, seen = [], set()
    for item in value['acceptance']:
        _fields(item, {'id', 'requirements', 'checks'}, 'invalid_acceptance')
        ident = _id(item['id'], 'AC-')
        if ident in seen:
            raise CoverageError('duplicate_id')
        seen.add(ident)
        acceptance.append({'id': ident,
                           'requirements': _ids(item['requirements'], prefix='REQ-'),
                           'checks': _ids(item['checks'])})
    review_status = value.get('review_status', 'unchecked')
    if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
        raise CoverageError('invalid_review_status')
    raw_methods = value.get('automation_methods')
    automation_methods = []
    if raw_methods is not None:
        if (not isinstance(raw_methods, list) or not raw_methods
                or len(raw_methods) > len(AUTOMATION_METHODS)):
            raise CoverageError('invalid_automation_methods')
        for item in raw_methods:
            if not isinstance(item, str) or item not in AUTOMATION_METHODS:
                raise CoverageError('invalid_automation_methods')
        if len(set(raw_methods)) != len(raw_methods):
            raise CoverageError('duplicate_automation_method')
        automation_methods = sorted(raw_methods)
    raw_specs = value.get('spec_files')
    spec_files = []
    if raw_specs is not None:
        if not isinstance(raw_specs, list) or len(raw_specs) > MAX_ITEMS:
            raise CoverageError('invalid_spec_files')
        seen_specs = set()
        for item in raw_specs:
            _fields(item, {'path', 'sha256'}, 'invalid_spec_files')
            rel = _rel_path(item['path'])
            sha = item['sha256']
            if not isinstance(sha, str) or not HASH_RE.fullmatch(sha):
                raise CoverageError('invalid_spec_files')
            key = rel.casefold()
            if key in seen_specs:
                raise CoverageError('duplicate_spec_file')
            seen_specs.add(key)
            spec_files.append({'path': rel, 'sha256': sha})
        spec_files.sort(key=lambda entry: entry['path'])
    raw_solutions = value.get('solution_files')
    solution_files = []
    if raw_solutions is not None:
        if not isinstance(raw_solutions, list) or len(raw_solutions) > MAX_ITEMS:
            raise CoverageError('invalid_solution_files')
        seen_solutions = set()
        for item in raw_solutions:
            rel = _rel_path(item)
            key = rel.casefold()
            if key in seen_solutions:
                raise CoverageError('duplicate_solution_file')
            seen_solutions.add(key)
            solution_files.append(rel)
        solution_files.sort()
    raw_gaps = value.get('permitted_gaps')
    permitted_gaps = []
    if raw_gaps is not None:
        permitted_gaps = _ids(raw_gaps, nonempty=False)
    return {'version': 1, 'task': task, 'requirements': requirements,
            'acceptance': sorted(acceptance, key=lambda item: item['id']),
            'required_checks': checks,
            'review_status': review_status,
            'automation_methods': automation_methods,
            'spec_files': spec_files,
            'solution_files': solution_files,
            'permitted_gaps': permitted_gaps}


class _Store(evidence.Store):
    """Reuse evidence provenance while imposing optional run-input safety rules."""
    def __init__(self, vault):
        self.inputs = runs.Store(vault)
        super().__init__(vault)

    def path(self, relative, *, internal=False):
        # Check current deny/exclude policy before every selected public-file read.
        self.inputs.policy.fresh()
        path = super().path(relative, internal=internal)
        if not internal:
            self.inputs.path(relative)
            # The reused legacy run/evidence APIs do not reject every known
            # credential basename. Strengthen only this opt-in public reader.
            parts = path.relative_to(self.root).parts
            if any(part.casefold() in SECRET_SEGMENTS or part.casefold().startswith('.env')
                   or part.casefold() in SECRET_BASENAMES
                   or Path(part).stem.casefold() in SECRET_STEMS
                   or Path(part).suffix.casefold() in SECRET_SUFFIXES for part in parts):
                raise CoverageError('credential_file_forbidden')
        return path


def _graph(manifest):
    requirements, checks = set(manifest['requirements']), set(manifest['required_checks'])
    mapped_requirements, mapped_checks, issues = set(), set(), []
    for item in manifest['acceptance']:
        known_requirements = set(item['requirements']) & requirements
        known_checks = set(item['checks']) & checks
        if not known_requirements or not known_checks:
            issues.append({'code': 'unmapped_acceptance', 'id': item['id']})
        for ident in sorted(set(item['requirements']) - requirements):
            issues.append({'code': 'unknown_requirement', 'id': ident, 'acceptance': item['id']})
        for ident in sorted(set(item['checks']) - checks):
            issues.append({'code': 'unknown_check', 'id': ident, 'acceptance': item['id']})
        # A partial edge cannot conceal an otherwise unmapped node.
        if known_requirements and known_checks:
            mapped_requirements.update(known_requirements)
            mapped_checks.update(known_checks)
    issues.extend({'code': 'unmapped_requirement', 'id': ident}
                  for ident in sorted(requirements - mapped_requirements))
    issues.extend({'code': 'unmapped_check', 'id': ident}
                  for ident in sorted(checks - mapped_checks))
    for ident in sorted(set(manifest['permitted_gaps']) - checks):
        issues.append({'code': 'unknown_permitted_gap', 'id': ident})
    spec_keys = {entry['path'].casefold(): entry['path'] for entry in manifest['spec_files']}
    for sol_path in manifest['solution_files']:
        if sol_path.casefold() in spec_keys:
            issues.append({'code': 'spec_solution_overlap', 'path': sol_path})
    return issues


def _outcomes(manifest, receipt):
    """Compare independently declared expected IDs with submitted observations."""
    expected = set(manifest['required_checks'])
    permitted_gaps = set(manifest['permitted_gaps']) & expected
    report = receipt['report']
    submitted, issues, permitted_gaps_used = {}, [], []
    if report is None:
        issues.append({'code': 'report_not_recorded'})
    else:
        for item in report['checks']:
            try:
                ident = _id(item['id'])
            except CoverageError:
                issues.append({'code': 'unsupported_check_id'})
                continue
            submitted[ident] = item['status']
    for ident in sorted(expected - submitted.keys()):
        issues.append({'code': 'missing_check', 'id': ident})
        issues.append({'code': 'unexecuted_or_unreported_check', 'id': ident})
    issues.extend({'code': 'undeclared_check', 'id': ident}
                  for ident in sorted(submitted.keys() - expected))
    for ident in sorted(expected & submitted.keys()):
        status = submitted[ident]
        if status == 'gap' and ident in permitted_gaps:
            permitted_gaps_used.append(ident)
        elif status != 'verified':
            code = {'gap': 'unsupported_outcome', 'failed': 'reported_failed',
                    'regression': 'reported_regression'}[status]
            issues.append({'code': code, 'id': ident})
    return issues, len(report['checks']) if report else 0, sum(
        status == 'verified' for ident, status in submitted.items() if ident in expected), permitted_gaps_used


def _check_spec_files(store, spec_files):
    """Verify immutable spec/challenge files against pinned SHA-256 digests."""
    spec_issues, spec_marks, verified_specs = [], {}, []
    for entry in spec_files:
        rel, expected_sha = entry['path'], entry['sha256']
        try:
            current, mark, _ = store.fingerprint(rel, limit=evidence.MAX_FILE_BYTES)
        except FileNotFoundError:
            spec_issues.append({'code': 'spec_file_missing', 'path': rel})
            continue
        except evidence.EvidenceError as exc:
            spec_issues.append({'code': 'spec_file_unsafe', 'path': rel, 'reason': str(exc)})
            continue
        spec_marks[current['path']] = mark
        if current['sha256'] != expected_sha:
            spec_issues.append({'code': 'spec_hash_mismatch', 'path': rel})
        else:
            verified_specs.append({'path': current['path'], 'sha256': current['sha256'], 'size': current['size']})
    return spec_issues, spec_marks, verified_specs


@_public
def check(vault, manifest_path, evidence_id):
    """Inspect one manifest and one selected receipt, without creating any state."""
    store = _Store(vault)
    info, manifest_mark, raw = store.fingerprint(
        manifest_path, limit=MAX_MANIFEST_BYTES, contents=True)
    if _matches_sensitive(raw.decode('utf-8-sig')):
        raise CoverageError('sensitive_input')
    manifest = _manifest(evidence.decode(raw))
    receipt, receipt_marks = store.load(evidence_id)
    _bounded(receipt, depth=12)
    if contains_sensitive(receipt):
        raise CoverageError('sensitive_input')
    files = receipt['artifacts'] + receipt['evidence']
    sources = [receipt['report_source']] if receipt['report_source'] else []
    if sum(item['size'] for item in files + sources) > evidence.MAX_SET_BYTES:
        raise CoverageError('file_set_too_large')
    # Validate every selected public path before hashing any of its file bodies.
    for item in files + sources:
        store.path(item['path'])
    for item in manifest['spec_files']:
        store.path(item['path'])
    for sol_path in manifest['solution_files']:
        store.path(sol_path)
    issues = _graph(manifest)
    outcome_issues, submitted_count, verified_count, permitted_gaps_used = _outcomes(manifest, receipt)
    issues.extend(outcome_issues)
    if receipt['task'] != manifest['task']:
        issues.append({'code': 'task_mismatch'})
    stale, marks = evidence.bindings(store, files)
    if sources:
        source_issues, source_marks = evidence.bindings(store, sources, limit=evidence.MAX_JSON_BYTES)
        stale.extend(source_issues)
        marks.update(source_marks)
    issues.extend({'code': 'stale_evidence', 'path': item['path'], 'reason': item['reason']}
                  for item in stale)
    spec_issues, spec_marks, verified_specs = _check_spec_files(store, manifest['spec_files'])
    issues.extend(spec_issues)
    marks.update(spec_marks)
    changed = False
    try:
        store.inputs.policy.fresh()
        store.fresh_policy()
        store.sweep({info['path']: manifest_mark})
        store.sweep(marks)
        store.sweep(receipt_marks, internal=True)
        # A second stable manifest read also detects same-metadata byte changes.
        current, _, _ = store.fingerprint(info['path'], limit=MAX_MANIFEST_BYTES)
        if current != info:
            raise CoverageError('changed_during_check')
    except (CoverageError, evidence.EvidenceError, runs.RunError, state.StateError, OSError):
        changed = True
        issues.append({'code': 'changed_during_check'})
    status = ('stale' if stale or spec_issues or changed else 'pending' if receipt['report'] is None
              else 'incomplete' if issues else 'complete')
    result = {'version': 1, 'ok': status == 'complete', 'status': status, 'authority': AUTHORITY,
              'semantic_fulfillment_verified': False, 'execution_verified': False,
              'review_status': manifest['review_status'],
              'independent_reviewed': manifest['review_status'] == 'independent_reviewed',
              'manifest': info,
              'evidence': {'id': receipt['id'], 'receipt_sha256': receipt['receipt_sha256'],
                           'status': 'stale' if stale or changed else
                           'pending' if receipt['report'] is None else 'current'},
              'counts': {'requirements': len(manifest['requirements']),
                         'acceptance': len(manifest['acceptance']),
                         'required_checks': len(manifest['required_checks']),
                         'submitted_checks': submitted_count,
                         'reported_verified_checks': verified_count},
              'issues': issues, 'limitations': list(LIMITATIONS)}
    if manifest['automation_methods']:
        result['automation_methods'] = manifest['automation_methods']
    if manifest['spec_files']:
        result['spec_bindings'] = verified_specs
    if manifest['solution_files']:
        result['solution_files'] = manifest['solution_files']
    if manifest['permitted_gaps']:
        result['permitted_gaps_used'] = permitted_gaps_used
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('check', help='inspect explicit coverage data without writing')
    command.add_argument('--manifest', required=True)
    command.add_argument('--evidence', required=True, dest='evidence_id')
    args = parser.parse_args(argv)
    try:
        result = check(args.vault, args.manifest, args.evidence_id)
        print(evidence.encode(result).decode('ascii'))
        return 0 if result['ok'] else 1
    except CoverageError as exc:
        print(evidence.encode({'version': 1, 'ok': False, 'status': 'error',
                               'error': str(exc), 'authority': AUTHORITY}).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
