#!/usr/bin/env python3
"""Read-only staged evaluation of explicit native evidence and review data.

The host supplies observations and performs every check or review. A passed
result describes current declared data only: it does not authenticate reviewers,
independently observe execution, grant permission or establish semantic truth.
No method text is executed and no model or network service is called.
"""
from __future__ import annotations

import argparse
from functools import wraps
from pathlib import Path

import vault_coverage as coverage
import vault_evidence as evidence
import vault_runs as runs
import vault_state as state
import vault_task_contracts as contracts
from jev_client import _matches_sensitive, contains_sensitive
from vault_healthcheck import HealthcheckError

AUTHORITY = 'data_only'
MAX_REPORT_BYTES = runs.MAX_JSON_BYTES
MAX_SOURCE_BYTES = runs.MAX_SOURCE_BYTES
MAX_LOG_BYTES = runs.MAX_EVIDENCE_BYTES
MAX_STAGE_BYTES = runs.MAX_TOTAL_BYTES
MECHANICAL_STATUSES = frozenset(('passed', 'failed', 'not_run', 'skipped', 'no_evidence'))
REVIEW_STATUSES = frozenset(('support', 'withhold', 'not_run'))
LIMITATIONS = (
    'Passed means complete current declared host observations and review data, not verified task truth.',
    'Native check statuses, execution claims and reviewer roles are caller declarations, not independent execution or identity proof.',
    'Source hashes bind selected bytes; they are not signatures, permissions or evidence of semantic fulfillment.',
    'Semantic and independent support cannot overturn failed native checks; absent or unexecuted checks never pass.',
    'An independent review source is structurally separate; its origin and independence are not authenticated.',
    'This read-only evaluator executes no check, invokes no model, grants no authority and changes no vault state.',
    'Freshness is point-in-time and limited to explicitly selected sources and the current contract lifecycle.',
)


class EvaluationError(Exception):
    """A content-free operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except EvaluationError:
            raise
        except (coverage.CoverageError, evidence.EvidenceError, runs.RunError,
                state.StateError, contracts.ContractError, HealthcheckError,
                ValueError, OSError, UnicodeError, RecursionError, OverflowError,
                TypeError, RuntimeError) as exc:
            raise EvaluationError('unsafe_or_invalid_input') from exc
    return call


def _fields(value, fields, code):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise EvaluationError(code)


def _source(value):
    if value is None:
        return None
    _fields(value, {'path', 'sha256'}, 'invalid_source')
    if (not isinstance(value['path'], str) or not isinstance(value['sha256'], str)
            or not evidence.HASH_RE.fullmatch(value['sha256'])):
        raise EvaluationError('invalid_source')
    return dict(value)


def _read(store, relative, *, limit, json_input=False):
    info, mark, raw = store.fingerprint(relative, limit=limit, contents=True)
    if json_input and Path(info['path']).suffix.casefold() != '.json':
        raise EvaluationError('invalid_json_path')
    if _matches_sensitive(raw.decode('utf-8-sig')):
        raise EvaluationError('sensitive_input')
    return info, mark, raw


def _report(value):
    coverage._bounded(value)
    if contains_sensitive(value):
        raise EvaluationError('sensitive_input')
    _fields(value, {'version', 'contract_id', 'evidence_id', 'coverage_manifest',
                    'mechanical', 'reviews', 'independent_required'}, 'invalid_report')
    if (type(value['version']) is not int or value['version'] != 1
            or type(value['independent_required']) is not bool
            or not isinstance(value['coverage_manifest'], str)
            or not isinstance(value['contract_id'], str)
            or not evidence.HASH_RE.fullmatch(value['contract_id'])
            or not isinstance(value['evidence_id'], str)
            or not evidence.ID_RE.fullmatch(value['evidence_id'])
            or not isinstance(value['mechanical'], list)
            or len(value['mechanical']) > coverage.MAX_ITEMS
            or not isinstance(value['reviews'], list) or len(value['reviews']) != 2):
        raise EvaluationError('invalid_report')
    mechanical, seen = [], set()
    for item in value['mechanical']:
        _fields(item, {'check_id', 'status', 'source'}, 'invalid_mechanical_record')
        ident = coverage._id(item['check_id'])
        if (ident in seen or not isinstance(item['status'], str)
                or item['status'] not in MECHANICAL_STATUSES):
            raise EvaluationError('invalid_or_duplicate_check')
        seen.add(ident)
        source = _source(item['source'])
        if (item['status'] in ('passed', 'failed')) != (source is not None):
            raise EvaluationError('mechanical_source_required_or_forbidden')
        mechanical.append({**item, 'source': source})
    reviews, seen = [], set()
    for item in value['reviews']:
        _fields(item, {'kind', 'status', 'acceptance_ids', 'source'}, 'invalid_review_record')
        if (not isinstance(item['kind'], str) or item['kind'] not in ('semantic', 'independent')
                or item['kind'] in seen or not isinstance(item['status'], str)
                or item['status'] not in REVIEW_STATUSES):
            raise EvaluationError('invalid_or_duplicate_review')
        seen.add(item['kind'])
        ids = coverage._ids(item['acceptance_ids'], prefix='AC-', nonempty=True)
        source = _source(item['source'])
        if (item['status'] != 'not_run') != (source is not None):
            raise EvaluationError('review_source_required_or_forbidden')
        reviews.append({**item, 'acceptance_ids': ids, 'source': source})
    return {**value, 'mechanical': sorted(mechanical, key=lambda x: x['check_id']),
            'reviews': sorted(reviews, key=lambda x: x['kind'])}


def _contract_identity(contract):
    # Readiness and lifecycle are checked separately; this fingerprint fixes the
    # accepted task content for comparisons, rather than hashing a pass/fail label.
    return evidence.digest(evidence.encode(contract['accepted_fields']))


def _status(*, stale, failed, blocked, unverified):
    if stale:
        return 'stale'
    if failed:
        return 'failed'
    if blocked:
        return 'blocked'
    return 'unverified' if unverified else 'passed'


def _stale_contract_result(report, report_info, manifest, manifest_info, contract):
    """Display historical criteria without treating them as current requirements."""
    history = contract.get('historical_metadata')
    acceptance_ids = sorted(history['acceptance_ids']) if history else []
    criteria_fingerprint = (evidence.digest(evidence.encode(history['accepted_fields']))
                            if history else None)
    bindings = [report_info, manifest_info]
    if history:
        bindings += history['source_bindings']
        if history.get('input_source'):
            bindings.append(history['input_source'])
    bindings = list({item['path'].casefold(): item for item in bindings}.values())
    return {'version': 1, 'ok': False, 'status': 'stale', 'authority': AUTHORITY,
            'execution_verified': False, 'semantic_fulfillment_verified': False,
            'contract_id': report['contract_id'], 'acceptance_ids': acceptance_ids,
            'failed_acceptance_ids': [],
            'acceptance_results': [{'id': item['id'], 'status': 'stale',
                                    'check_ids': item['checks']} for item in manifest['acceptance']],
            'mechanical_results': [], 'reviews': report['reviews'],
            'independent_required': report['independent_required'],
            'criteria_fingerprint': criteria_fingerprint,
            'contract_fingerprint': contract['fingerprint'],
            'report_source': report_info, 'source_bindings': bindings,
            'coverage': None,
            'fingerprint': evidence.digest(evidence.encode({
                'report': report_info, 'manifest': manifest_info,
                'contract': contract['fingerprint'], 'criteria': criteria_fingerprint,
                'stale_contract': True})),
            'issues': [{'code': 'stale_contract'}], 'limitations': list(LIMITATIONS)
            + ['Historical criterion metadata is display-only and never current acceptance authority.']}


@_public
def evaluate(vault, report_path):
    """Evaluate a bounded vault-relative report; return JSON data without writing."""
    store = coverage._Store(vault)
    report_info, _, raw = _read(store, report_path, limit=MAX_REPORT_BYTES, json_input=True)
    report = _report(evidence.decode(raw))
    # Even an already stale contract cannot make an unsafe submitted source a
    # permissible public path. Validate boundaries before any stale short circuit.
    for item in report['mechanical'] + report['reviews']:
        if item['source'] is not None:
            store.path(item['source']['path'])
    manifest_info, _, manifest_raw = _read(
        store, report['coverage_manifest'], limit=coverage.MAX_MANIFEST_BYTES, json_input=True)
    manifest = coverage._manifest(evidence.decode(manifest_raw))
    contract = contracts.inspect(vault, report['contract_id'])
    if contract['status'] == 'stale':
        return _stale_contract_result(report, report_info, manifest, manifest_info, contract)
    acceptance_ids = sorted(contract['acceptance_ids'])
    if (not acceptance_ids or len(acceptance_ids) != len(set(acceptance_ids))
            or manifest['task'] != contract['task']
            or sorted(item['id'] for item in manifest['acceptance']) != acceptance_ids):
        raise EvaluationError('contract_coverage_mismatch')
    if any(set(item['acceptance_ids']) != set(acceptance_ids) for item in report['reviews']):
        raise EvaluationError('review_criteria_mismatch')
    # Native coverage owns graph correctness; reject unknown targets rather than
    # accepting review support for a different set of declared checks.
    graph = coverage._graph(manifest)
    if any(item['code'] in ('unknown_requirement', 'unknown_check') for item in graph):
        raise EvaluationError('unknown_coverage_id')
    expected = set(manifest['required_checks'])
    if any(item['check_id'] not in expected for item in report['mechanical']):
        raise EvaluationError('unknown_check_id')
    receipt, _ = store.load(report['evidence_id'])
    if receipt['task'] != contract['task']:
        raise EvaluationError('native_task_mismatch')
    native_checks = {item['id']: item for item in receipt['report']['checks']} if receipt['report'] else {}
    if native_checks.keys() - expected:
        raise EvaluationError('undeclared_native_check')
    receipt_info, _, _ = store.fingerprint(
        evidence.STORE_DIR + '/' + report['evidence_id'] + '.json',
        limit=evidence.MAX_RECEIPT_BYTES, internal=True)
    contract_info, _, _ = store.fingerprint(
        contract['path'], limit=runs.MAX_RECORD_BYTES, internal=True)
    native_files = receipt['artifacts'] + receipt['evidence']
    if receipt['report_source']:
        native_files += [receipt['report_source']]
    if sum(item['size'] for item in native_files) > evidence.MAX_SET_BYTES:
        raise EvaluationError('file_set_too_large')
    bindings = {}
    def add(info):
        key = info['path'].casefold()
        if key in bindings and bindings[key] != info:
            raise EvaluationError('conflicting_source_binding')
        bindings[key] = dict(info)
    add(report_info)
    add(manifest_info)
    for info in native_files + contract['source_bindings'] + [contract['input_source']]:
        add(info)
    add(receipt_info)
    add(contract_info)
    protected = {item['path'].casefold() for item in [report_info, manifest_info]
                 + native_files + contract['source_bindings'] + [contract['input_source']]}
    if report_info['path'].casefold() == manifest_info['path'].casefold():
        raise EvaluationError('input_source_overlap')
    # A selected staged input is never its own candidate, check log or contract input.
    if report_info['path'].casefold() in {
            item['path'].casefold() for item in native_files + contract['source_bindings']
            + [contract['input_source']]}:
        raise EvaluationError('input_source_overlap')
    issues, stale, stage_sources = [], False, {}
    used_stage_bytes = report_info['size'] + manifest_info['size']
    def read_source(source, *, review=False):
        nonlocal stale, used_stage_bytes
        canonical = store.path(source['path']).relative_to(store.root).as_posix()
        key = canonical.casefold()
        if review and key in protected:
            raise EvaluationError('review_source_overlap')
        try:
            info, _, _ = _read(store, canonical, limit=MAX_SOURCE_BYTES if review else MAX_LOG_BYTES)
        except FileNotFoundError:
            stale = True
            issues.append({'code': 'stale_stage_source', 'path': canonical})
            return canonical
        if info['sha256'] != source['sha256']:
            stale = True
            issues.append({'code': 'stale_stage_source', 'path': canonical})
        if key not in stage_sources:
            used_stage_bytes += info['size']
            if used_stage_bytes > MAX_STAGE_BYTES:
                raise EvaluationError('stage_source_set_too_large')
            stage_sources[key] = info
        # Keep native historical bindings on stale input, not the changed bytes.
        if key not in bindings:
            add(info)
        return canonical
    declared = {item['check_id']: item for item in report['mechanical']}
    native_evidence = {item['path']: item for item in receipt['evidence']}
    mechanical_results = []
    for ident in sorted(expected):
        item, native = declared.get(ident), native_checks.get(ident)
        declared_status = item['status'] if item else 'no_evidence'
        if item and item['source'] is not None:
            canonical = store.path(item['source']['path']).relative_to(store.root).as_posix()
            if native is None or canonical not in native['evidence'] or canonical not in native_evidence:
                raise EvaluationError('mechanical_log_not_native_evidence')
            if item['source']['sha256'] != native_evidence[canonical]['sha256']:
                stale = True
                issues.append({'code': 'stale_mechanical_binding', 'id': ident})
            read_source(item['source'])
        if native is None:
            outcome = 'no_evidence'
            issues.append({'code': 'missing_native_check', 'id': ident})
        elif native['status'] in ('failed', 'regression') or declared_status == 'failed':
            outcome = 'failed'
            issues.append({'code': 'failed_mechanical_check', 'id': ident})
        elif declared_status == 'passed' and native['status'] == 'verified':
            outcome = 'passed'
        else:
            outcome = declared_status if declared_status != 'passed' else 'no_evidence'
            if declared_status == 'passed':
                issues.append({'code': 'native_outcome_not_verified', 'id': ident})
        mechanical_results.append({'check_id': ident, 'status': outcome,
                                   'declared_status': declared_status,
                                   'native_status': native['status'] if native else None,
                                   'source': item['source'] if item else None})
    reviews = {item['kind']: item for item in report['reviews']}
    review_paths = set()
    for item in report['reviews']:
        if item['source'] is not None:
            path = read_source(item['source'], review=True)
            if path.casefold() in review_paths:
                raise EvaluationError('review_source_overlap')
            review_paths.add(path.casefold())
    checked = coverage.check(vault, report['coverage_manifest'], report['evidence_id'])
    issues.extend({'code': 'coverage_' + item['code'],
                   **{k: v for k, v in item.items() if k != 'code'}} for item in checked['issues'])
    if (checked['manifest'] != manifest_info
            or checked['evidence']['receipt_sha256'] != receipt['receipt_sha256']):
        stale = True
        issues.append({'code': 'changed_native_inputs'})
    stale = stale or checked['status'] == 'stale' or contract['status'] == 'stale'
    if contract['status'] == 'stale':
        issues.append({'code': 'stale_contract'})
    blocked = contract['status'] != 'ready_for_execution_review' and contract['status'] != 'stale'
    if blocked:
        issues.append({'code': 'contract_needs_clarification'})
    withheld = any(item['status'] == 'withhold' for item in report['reviews'])
    blocked = blocked or withheld
    if withheld:
        issues.append({'code': 'review_withheld'})
    review_missing = (reviews['semantic']['status'] != 'support'
                      or report['independent_required'] and reviews['independent']['status'] != 'support')
    if review_missing:
        issues.append({'code': 'required_review_not_supported'})
    # Native coverage owns freshness; its binding helper is reused for the final
    # point-in-time check, including sources changed after the coverage call.
    try:
        store.inputs.policy.fresh()
        store.fresh_policy()
        public_bindings = [item for item in bindings.values()
                           if item not in (receipt_info, contract_info)]
        changed, _ = evidence.bindings(store, public_bindings)
        if changed:
            stale = True
            issues.append({'code': 'changed_during_evaluation'})
        current_receipt, _, _ = store.fingerprint(
            receipt_info['path'], limit=evidence.MAX_RECEIPT_BYTES, internal=True)
        current_contract_info, _, _ = store.fingerprint(
            contract_info['path'], limit=runs.MAX_RECORD_BYTES, internal=True)
        current_contract = contracts.inspect(vault, report['contract_id'])
        if (current_receipt != receipt_info or current_contract_info != contract_info
                or current_contract != contract):
            stale = True
            issues.append({'code': 'changed_during_evaluation'})
    except (coverage.CoverageError, evidence.EvidenceError, runs.RunError, state.StateError,
            contracts.ContractError, OSError):
        stale = True
        issues.append({'code': 'changed_during_evaluation'})
    outcomes = {item['check_id']: item['status'] for item in mechanical_results}
    acceptance_results = []
    for item in manifest['acceptance']:
        statuses = [outcomes[ident] for ident in item['checks']]
        failed = 'failed' in statuses
        unverified = not statuses or any(status != 'passed' for status in statuses) or review_missing
        acceptance_results.append({'id': item['id'],
                                   'status': _status(stale=stale, failed=failed,
                                                     blocked=blocked, unverified=unverified),
                                   'check_ids': item['checks']})
    failed_ids = [item['id'] for item in manifest['acceptance']
                  if any(outcomes[ident] == 'failed' for ident in item['checks'])]
    status = _status(stale=stale, failed=bool(failed_ids), blocked=blocked,
                     unverified=(checked['status'] != 'complete' or review_missing
                                 or any(item['status'] != 'passed' for item in acceptance_results)))
    source_bindings = sorted(bindings.values(), key=lambda item: item['path'])
    criteria_fingerprint = _contract_identity(contract)
    fingerprint = evidence.digest(evidence.encode({
        'report': report_info, 'sources': source_bindings,
        'contract': contract['fingerprint'], 'criteria': criteria_fingerprint,
    }))
    return {'version': 1, 'ok': status == 'passed', 'status': status, 'authority': AUTHORITY,
            'execution_verified': False, 'semantic_fulfillment_verified': False,
            'contract_id': report['contract_id'], 'acceptance_ids': acceptance_ids,
            'failed_acceptance_ids': sorted(failed_ids),
            'acceptance_results': sorted(acceptance_results, key=lambda item: item['id']),
            'mechanical_results': mechanical_results, 'reviews': report['reviews'],
            'independent_required': report['independent_required'],
            'criteria_fingerprint': criteria_fingerprint, 'contract_fingerprint': contract['fingerprint'],
            'report_source': report_info, 'source_bindings': source_bindings,
            'coverage': checked, 'fingerprint': fingerprint,
            'issues': issues, 'limitations': list(LIMITATIONS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('evaluate', help='inspect staged review data without execution')
    command.add_argument('--input', required=True, dest='report_path')
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.vault, args.report_path)
        print(evidence.encode(result).decode('ascii'))
        return 0 if result['ok'] else 1
    except EvaluationError as exc:
        print(evidence.encode({'version': 1, 'ok': False, 'status': 'error',
                               'error': str(exc), 'authority': AUTHORITY}).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
