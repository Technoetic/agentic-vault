#!/usr/bin/env python3
"""Read-only bounded observations over explicitly ordered staged reports.

This is an advisory observer, not an executing controller. Costs and ordering
are caller declarations; hashes do not authenticate history, execution or truth.
"""
from __future__ import annotations

import argparse
from functools import wraps
import json
from pathlib import Path
import sys

import vault_coverage as coverage
import vault_evidence as evidence
import vault_runs as runs
import vault_staged_evaluation as staged
from jev_client import contains_sensitive
from vault_healthcheck import HealthcheckError


MAX_GENERATIONS = 64
MAX_COST_MICROUSD = 2 ** 63 - 1
MAX_BINDINGS = 256
AUTHORITY = 'advisory_observer_only'
LIMITATIONS = [
    'No commands, models, retries, lesson promotion or external actions are executed.',
    'Limits are observations and advice; external runtime spending is not enforced.',
    'Generation order and costs are caller declarations, not authenticated history.',
    'Success means current staged observation data passed, not verified execution or semantic truth.',
    'Hashes bind point-in-time bytes and do not grant approval or permission.',
    'Unknown costs remain unknown; a known subtotal does not bound actual total spending.',
]


class CycleError(Exception):
    """A content-free operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CycleError:
            raise
        except (coverage.CoverageError, runs.RunError, runs.state.StateError, evidence.EvidenceError,
                staged.EvaluationError, HealthcheckError, ValueError, OSError,
                UnicodeError, RecursionError, OverflowError, TypeError, KeyError) as exc:
            raise CycleError('unsafe_or_invalid_input') from exc
    return call


class _Store(runs.Store):
    """Apply the existing opt-in public credential policy before content reads."""
    def __init__(self, vault):
        super().__init__(vault)
        self.public_paths = coverage._Store(vault)

    def path(self, relative, *, markdown=False):
        self.public_paths.path(relative)
        return super().path(relative, markdown=markdown)


def _integer(value, minimum, maximum, code):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CycleError(code)
    return value


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= set(value)
            or set(value) - set(required) - set(optional)):
        raise CycleError('invalid_cycle_schema')


def _definition(value):
    _fields(value, {'version', 'contract_id', 'max_generations', 'generations'},
            {'max_cost_microusd'})
    _integer(value['version'], 1, 1, 'invalid_version')
    runs._hash(value['contract_id'])
    maximum = _integer(value['max_generations'], 1, MAX_GENERATIONS, 'invalid_generation_limit')
    if 'max_cost_microusd' in value:
        _integer(value['max_cost_microusd'], 0, MAX_COST_MICROUSD, 'invalid_cost_limit')
    generations = value['generations']
    if not isinstance(generations, list) or not 1 <= len(generations) <= maximum:
        raise CycleError('invalid_generation_count')
    paths = set()
    for index, generation in enumerate(generations, 1):
        _fields(generation, {'id', 'evaluation_report', 'cost_microusd'})
        if type(generation['id']) is not int or generation['id'] != index:
            raise CycleError('invalid_generation_order')
        path = runs._text(generation['evaluation_report'], maximum=1024)
        if path.casefold() in paths:
            raise CycleError('duplicate_evaluation_report')
        paths.add(path.casefold())
        if generation['cost_microusd'] is not None:
            _integer(generation['cost_microusd'], 0, MAX_COST_MICROUSD, 'invalid_cost')
    return value


def _json(store, relative):
    info, value = store.json_file(relative)
    if Path(info['path']).suffix.casefold() != '.json':
        raise CycleError('invalid_json_path')
    return info, value


def _criteria(result, report, coverage):
    """Freeze criteria, native check mapping and declared review requirements."""
    acceptance = result['acceptance_ids']
    if (not isinstance(acceptance, list) or not acceptance
            or len(acceptance) != len(set(acceptance))):
        raise CycleError('invalid_evaluation_acceptance')
    mappings = sorted((entry['id'], tuple(sorted(entry['check_ids'])))
                      for entry in result['acceptance_results'])
    if sorted(entry[0] for entry in mappings) != sorted(acceptance):
        raise CycleError('invalid_evaluation_acceptance')
    return {'contract_id': result['contract_id'],
            'acceptance_ids': sorted(acceptance),
            'criteria_fingerprint': result['criteria_fingerprint'],
            'check_mapping': mappings, 'coverage_manifest': coverage,
            'independent_required': report['independent_required']}


def _failure_signature(result):
    # Changing output files or review prose is not acceptance progress.
    return (result['status'], tuple(sorted(
        (entry['id'], entry['status']) for entry in result['acceptance_results'])))


def _bounded_bindings(results, selected):
    bindings = {item['path'].casefold(): item for item in selected}
    for result in results:
        for item in result['source_bindings']:
            _fields(item, {'path', 'size', 'sha256'})
            runs._text(item['path'], maximum=1024)
            runs._hash(item['sha256'])
            _integer(item['size'], 0, runs.MAX_EVIDENCE_BYTES, 'invalid_binding_size')
            key = item['path'].casefold()
            if key in bindings and bindings[key] != item:
                raise CycleError('inconsistent_source_binding')
            bindings[key] = item
    if len(bindings) > MAX_BINDINGS or sum(item['size'] for item in bindings.values()) > runs.MAX_TOTAL_BYTES:
        raise CycleError('file_set_too_large')
    return sorted(bindings.values(), key=lambda item: item['path'])


@_public
def audit(vault, input_path):
    """Recompute a bounded declared history; return advice without writing files."""
    store = _Store(vault)
    input_source, value = _json(store, input_path)
    value = _definition(value)
    results, selected, frozen, canonical_paths = [], [input_source], None, set()
    for generation in value['generations']:
        source, report = _json(store, generation['evaluation_report'])
        if source['path'].casefold() in canonical_paths or source['path'].casefold() == input_source['path'].casefold():
            raise CycleError('duplicate_evaluation_report')
        canonical_paths.add(source['path'].casefold())
        result = staged.evaluate(vault, source['path'])
        if result['report_source'] != source or result['contract_id'] != value['contract_id']:
            raise CycleError('evaluation_contract_or_source_mismatch')
        if any(item['path'].casefold() == input_source['path'].casefold()
               for item in result['source_bindings']):
            raise CycleError('input_source_overlap')
        coverage_source, _ = _json(store, report['coverage_manifest'])
        # Stale contracts deliberately expose no currently accepted criteria.
        # They stop as stale rather than regaining current authority by parsing
        # historical fields merely to compare this history.
        if result['status'] != 'stale':
            criteria = _criteria(result, report, coverage_source)
            if frozen is not None and frozen != criteria:
                raise CycleError('changed_evaluation_criteria')
            frozen = criteria
        selected.extend((source, coverage_source))
        results.append(result)

    bindings = _bounded_bindings(results, selected)
    status, reason, terminal = 'continue', 'observations_exhausted', None
    subtotal, unknown = 0, 0
    budget = value.get('max_cost_microusd')
    passed_before, signatures, regressed, observations = set(), [], [], []
    for generation, result in zip(value['generations'], results):
        if terminal is not None:
            raise CycleError('observations_after_terminal')
        cost = generation['cost_microusd']
        if cost is None:
            unknown += 1
        else:
            subtotal += cost
        now_failed = set(result['failed_acceptance_ids'])
        regressed = sorted(passed_before & now_failed)
        signature = _failure_signature(result)
        signatures.append(signature)
        observations.append({'id': generation['id'], 'evaluation_report': result['report_source'],
                             'status': result['status'], 'failed_acceptance_ids': sorted(now_failed),
                             'cost_microusd': cost})
        # Exceeding the declared cost limit takes priority over a passing report.
        # Equality permits the currently observed generation to succeed.
        if budget is not None and subtotal > budget:
            status, reason = 'stopped', 'cost_limit'
        elif result['status'] == 'stale':
            status, reason = 'blocked', 'stale_evaluation'
        elif result['status'] == 'blocked':
            status, reason = 'blocked', 'blocked_evaluation'
        elif regressed:
            status, reason = 'stopped', 'regression'
        elif result['status'] == 'passed':
            status, reason = 'success', 'verified_success'
        elif len(signatures) >= 2 and signatures[-1] == signatures[-2]:
            status, reason = 'stopped', 'no_progress'
        elif len(signatures) >= 4 and signatures[-4] == signatures[-2] and signatures[-3] == signatures[-1]:
            status, reason = 'stopped', 'oscillation'
        elif budget is not None and subtotal == budget:
            status, reason = 'stopped', 'cost_limit'
        elif generation['id'] == value['max_generations']:
            status, reason = 'stopped', 'max_generations'
        elif result['status'] not in ('failed', 'unverified'):
            raise CycleError('unknown_evaluation_status')
        if status != 'continue':
            terminal = generation['id']
        passed_before.update(entry['id'] for entry in result['acceptance_results']
                             if entry['status'] == 'passed')

    # Recompute the complete staged boundary, including native receipt checks
    # and contract supersession, rather than trusting an earlier saved summary.
    for generation, expected in zip(value['generations'], results):
        current = staged.evaluate(vault, generation['evaluation_report'])
        if evidence.encode(current) != evidence.encode(expected):
            raise CycleError('evaluation_changed_during_audit')
    for expected in selected:
        actual, _ = _json(store, expected['path'])
        if actual != expected:
            raise CycleError('input_changed_during_audit')
    store.policy.fresh()
    if frozen is None:
        frozen = {'acceptance_ids': sorted(results[0]['acceptance_ids']),
                  'criteria_fingerprint': results[0]['criteria_fingerprint']}
    budget_safe = None if unknown or budget is None else subtotal <= budget
    output = {
        'version': 1, 'status': status, 'success': status == 'success', 'reason': reason,
        'advisory': 'continue' if status == 'continue' else 'stop',
        'terminal_generation': terminal, 'contract_id': value['contract_id'],
        'acceptance_ids': frozen['acceptance_ids'],
        'criteria_fingerprint': frozen['criteria_fingerprint'],
        'observations': observations, 'regressed_acceptance_ids': regressed,
        'limits': {'max_generations': value['max_generations'], 'max_cost_microusd': budget},
        'cost': {'known_subtotal_microusd': subtotal, 'unknown_count': unknown,
                 'budget_safe': budget_safe},
        'input_source': input_source, 'source_bindings': bindings,
        'authority': AUTHORITY, 'runtime_limits_enforced': False,
        'history_authenticated': False, 'execution_verified': False,
        'semantic_fulfillment_verified': False, 'limitations': list(LIMITATIONS),
    }
    if contains_sensitive(output):
        raise CycleError('sensitive_output')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', default='.')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('audit', help='observe an explicit bounded report history')
    command.add_argument('--input', required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(args.vault, args.input)
    except CycleError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
