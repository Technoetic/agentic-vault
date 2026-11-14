#!/usr/bin/env python3
"""Generated synthetic controls for task workflows, not an external benchmark.

This checkout-only demo uses a temporary vault and declared host observations.
It calls no models or network services and makes no performance claims.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'


class SyntheticVault:
    def __init__(self, vault, evidence):
        self.vault, self.evidence, self.serial = vault, evidence, 0
        self.write_json('00-meta/vault-config.json', {'deny_zones': ['private']})
        self.write('notes/request.md', 'Create reader.\nLocal only.\nShows title.\n')
        self.write('notes/result.md', '# Controlled result\n')
        self.write('output/review.txt', 'Synthetic model-support declaration; no model was invoked.\n')
        self.definition = {'version': 1, 'task': 'synthetic-reader', 'fields': [
            {'id': ident, 'kind': kind, 'text': text, 'origin': 'user_request',
             'status': 'confirmed', 'sources': [{'path': 'notes/request.md', 'excerpt': text}]}
            for ident, kind, text in [('goal', 'goal', 'Create reader.'),
                                     ('scope', 'scope', 'Local only.'),
                                     ('AC-001', 'acceptance', 'Shows title.')]], 'questions': []}
        self.write_json('input/coverage.json', {
            'version': 1, 'task': 'synthetic-reader', 'requirements': ['REQ-001'],
            'acceptance': [{'id': 'AC-001', 'requirements': ['REQ-001'], 'checks': ['C-001']}],
            'required_checks': ['C-001'],
        })

    def write(self, relative, text):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def write_json(self, relative, value):
        self.write(relative, json.dumps(value, allow_nan=False))

    def source(self, relative):
        return {'path': relative, 'sha256': hashlib.sha256((self.vault / relative).read_bytes()).hexdigest()}

    def report(self, contract_id, status):
        self.serial += 1
        suffix = str(self.serial)
        log = 'output/check-' + suffix + '.log'
        self.write(log, 'Synthetic declared check observation ' + suffix + '.\n')
        store = self.evidence.Store(self.vault)
        receipt = self.evidence.snapshot(store, SimpleNamespace(
            task='synthetic-reader', objective='Observe controlled fixture.', artifact=['notes/result.md']))
        if status != 'no_evidence':
            native_status = {'passed': 'verified', 'failed': 'failed', 'not_run': 'gap'}[status]
            native_path = 'input/native-' + suffix + '.json'
            self.write_json(native_path, {'verifier': 'synthetic-host', 'checks': [{
                'id': 'C-001', 'claim': 'Controlled check result.', 'status': native_status,
                'method': 'Read synthetic output.', 'observed': 'Declared synthetic observation.',
                'evidence': [log], 'preserve': 'Preserve the checked condition.' if status == 'passed' else '',
                'next_check': '' if status == 'passed' else 'Observe again.',
            }], 'next_action': 'Review result.'})
            self.evidence.record(store, receipt['id'], native_path)
        path = 'input/staged-' + suffix + '.json'
        self.write_json(path, {
            'version': 1, 'contract_id': contract_id, 'evidence_id': receipt['id'],
            'coverage_manifest': 'input/coverage.json',
            'mechanical': [{'check_id': 'C-001', 'status': status,
                            'source': self.source(log) if status in ('passed', 'failed') else None}],
            'reviews': [
                {'kind': 'semantic', 'status': 'support', 'acceptance_ids': ['AC-001'],
                 'source': self.source('output/review.txt')},
                {'kind': 'independent', 'status': 'not_run', 'acceptance_ids': ['AC-001'], 'source': None},
            ], 'independent_required': False,
        })
        return path

    def cycle(self, observer, contract_id, reports, maximum=3):
        path = 'input/cycle.json'
        self.write_json(path, {'version': 1, 'contract_id': contract_id, 'max_generations': maximum,
                              'generations': [{'id': index, 'evaluation_report': report,
                                               'cost_microusd': None}
                                              for index, report in enumerate(reports, 1)]})
        return observer.audit(self.vault, path)


def evaluate():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    contracts, evidence, staged, cycles = [importlib.import_module(name) for name in (
        'vault_task_contracts', 'vault_evidence', 'vault_staged_evaluation', 'vault_improvement_cycles')]
    controls = {}
    with tempfile.TemporaryDirectory() as temporary:
        vault = Path(temporary) / 'vault'
        fixture = SyntheticVault(vault, evidence)
        fixture.write_json('input/task.json', fixture.definition)
        captured = contracts.capture(vault, 'input/task.json')
        contract_id = captured['contract_id']
        good = fixture.report(contract_id, 'passed')
        passed = staged.evaluate(vault, good)
        success = fixture.cycle(cycles, contract_id, [good], maximum=1)
        controls['capture_to_success'] = {
            'contract': captured['status'], 'staged': passed['status'], 'cycle': success['status'],
            'passed': captured['status'] == 'ready_for_execution_review'
                      and passed['status'] == 'passed' and success['success'],
        }
        for name in ('inferred_default', 'open_blocking_question'):
            definition = copy.deepcopy(fixture.definition)
            if name == 'inferred_default':
                definition['fields'][0].update(origin='host_default', sources=[])
            else:
                definition['questions'] = [{'id': 'Q-001', 'text': 'Which output is required?',
                                            'blocking': True, 'status': 'open'}]
            path = 'input/' + name + '.json'
            fixture.write_json(path, definition)
            result = contracts.capture(vault, path)
            controls[name] = {'contract': result['status'], 'passed': result['status'] == 'needs_clarification'}
        failed = fixture.report(contract_id, 'failed')
        failed_result = staged.evaluate(vault, failed)
        failed_cycle = fixture.cycle(cycles, contract_id, [failed], maximum=1)
        controls['hard_fail_with_model_support'] = {
            'staged': failed_result['status'], 'cycle_success': failed_cycle['success'],
            'passed': failed_result['status'] == 'failed' and not failed_cycle['success'],
        }
        unexecuted = {status: staged.evaluate(vault, fixture.report(contract_id, status))['status']
                      for status in ('not_run', 'no_evidence')}
        controls['unexecuted_checks'] = {**unexecuted, 'passed': all(
            status == 'unverified' for status in unexecuted.values())}
        repeat = fixture.cycle(cycles, contract_id, [failed, fixture.report(contract_id, 'failed')])
        limit = fixture.cycle(cycles, contract_id, [failed], maximum=1)
        controls['bounded_observation'] = {
            'repeated_failure': repeat['reason'], 'generation_limit': limit['reason'],
            'passed': repeat['reason'] == 'no_progress' and limit['reason'] == 'max_generations'
                      and not repeat['success'] and not limit['success'],
        }
        contracts.revise(vault, contract_id, 'input/task.json')
        old = staged.evaluate(vault, good)
        stale_cycle = fixture.cycle(cycles, contract_id, [good])
        controls['superseded_contract'] = {
            'staged': old['status'], 'cycle': stale_cycle['status'],
            'passed': old['status'] == 'stale' and not stale_cycle['success'],
        }
    return {'status': 'complete' if all(item['passed'] for item in controls.values()) else 'failed',
            'fixture_scope': 'generated_synthetic_controls_not_external_benchmark', 'controls': controls,
            'runtime_limits_enforced': False, 'model_calls_made': False, 'network_calls_made': False,
            'limitations': ['Generated controls are not an external benchmark or performance measurement.',
                            'Native and model-support observations are synthetic caller declarations.',
                            'Passing data does not verify execution, semantic truth, identity or permission.',
                            'The observer provides advice and does not enforce external spending.']}


def main():
    result = evaluate()
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
