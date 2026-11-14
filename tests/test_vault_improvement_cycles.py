from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_evidence as evidence
try:
    cycles = importlib.import_module('vault_improvement_cycles')
except ModuleNotFoundError:
    cycles = None


class ImprovementCyclesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cycles, 'bounded improvement observer implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve() / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        })
        self.write('notes/request.md', 'Create reader.\nLocal only.\nShows title.\nShows body.\n')
        self.write('notes/result.md', '# Controlled result\n')
        fields = []
        for ident, kind, text in [('goal', 'goal', 'Create reader.'),
                                   ('scope', 'scope', 'Local only.'),
                                   ('AC-001', 'acceptance', 'Shows title.'),
                                   ('AC-002', 'acceptance', 'Shows body.')]:
            fields.append({'id': ident, 'kind': kind, 'text': text,
                           'origin': 'user_request', 'status': 'confirmed',
                           'sources': [{'path': 'notes/request.md', 'excerpt': text}]})
        self.write_json('input/task.json', {
            'version': 1, 'task': 'reader', 'fields': fields, 'questions': [],
        })
        contracts = importlib.import_module('vault_task_contracts')
        self.contract_id = contracts.capture(self.vault, 'input/task.json')['contract_id']
        self.coverage = {
            'version': 1, 'task': 'reader', 'requirements': ['REQ-001'],
            'acceptance': [
                {'id': 'AC-001', 'requirements': ['REQ-001'], 'checks': ['c1']},
                {'id': 'AC-002', 'requirements': ['REQ-001'], 'checks': ['c2']},
            ], 'required_checks': ['c1', 'c2'],
        }
        self.write_json('input/coverage.json', self.coverage)
        self.serial = 0

    def write(self, relative, raw):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, allow_nan=False))

    def report(self, statuses=('passed', 'passed')):
        self.serial += 1
        index = self.serial
        log = 'output/check-' + str(index) + '.log'
        raw = ('Controlled observation ' + str(index) + '.\n').encode('utf-8')
        self.write(log, raw)
        source = {'path': log, 'sha256': hashlib.sha256(raw).hexdigest()}
        review_sources = {}
        for kind in ('semantic', 'independent'):
            path = 'output/' + kind + '-' + str(index) + '.txt'
            data = ('Controlled ' + kind + ' observation.\n').encode('utf-8')
            self.write(path, data)
            review_sources[kind] = {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}
        store = evidence.Store(self.vault)
        receipt = evidence.snapshot(store, SimpleNamespace(
            task='reader', objective='Observe controlled fixture.', artifact=['notes/result.md']))
        native = {'verifier': 'fixture-host', 'checks': [], 'next_action': 'Review result.'}
        for ident, status in zip(('c1', 'c2'), statuses):
            native['checks'].append({
                'id': ident, 'claim': 'Controlled host observation.',
                'status': 'verified' if status == 'passed' else 'failed' if status == 'failed' else 'gap',
                'method': 'Read controlled log.', 'observed': 'Controlled output.',
                'evidence': [log], 'preserve': 'Preserve observed condition.' if status == 'passed' else '',
                'next_check': '' if status == 'passed' else 'Observe again.',
            })
        native_path = 'input/native-' + str(index) + '.json'
        self.write_json(native_path, native)
        evidence.record(store, receipt['id'], native_path)
        report = {'version': 1, 'contract_id': self.contract_id,
                  'evidence_id': receipt['id'], 'coverage_manifest': 'input/coverage.json',
                  'mechanical': [{'check_id': ident, 'status': status,
                                  'source': source if status in ('passed', 'failed') else None}
                                 for ident, status in zip(('c1', 'c2'), statuses)],
                  'reviews': [{'kind': kind, 'status': 'support', 'acceptance_ids': ['AC-001', 'AC-002'],
                               'source': review_sources[kind]} for kind in ('semantic', 'independent')],
                  'independent_required': False}
        path = 'input/evaluation-' + str(index) + '.json'
        self.write_json(path, report)
        return path

    def input(self, reports, *, maximum=8, costs=None, budget=None):
        costs = costs if costs is not None else [None] * len(reports)
        value = {'version': 1, 'contract_id': self.contract_id, 'max_generations': maximum,
                 'generations': [{'id': index, 'evaluation_report': report, 'cost_microusd': cost}
                                 for index, (report, cost) in enumerate(zip(reports, costs), 1)]}
        if budget is not None:
            value['max_cost_microusd'] = budget
        self.write_json('input/cycle.json', value)
        return value

    def audit(self, reports, **kwargs):
        self.input(reports, **kwargs)
        return cycles.audit(self.vault, 'input/cycle.json')

    def inventory(self):
        return {path.relative_to(self.vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.vault.rglob('*') if path.is_file()}

    def test_first_authoritative_pass_succeeds_read_only(self):
        self.input([self.report()], costs=[5], budget=10)
        before = self.inventory()
        result = cycles.audit(self.vault, 'input/cycle.json')
        self.assertEqual((result['status'], result['reason']), ('success', 'verified_success'))
        self.assertTrue(result['success'])
        self.assertEqual(result['authority'], 'advisory_observer_only')
        self.assertFalse(result['runtime_limits_enforced'])
        self.assertFalse(result['execution_verified'])
        self.assertEqual(result['cost']['known_subtotal_microusd'], 5)
        self.assertEqual(self.inventory(), before)

    def test_final_allowed_generation_can_succeed(self):
        result = self.audit([self.report(('failed', 'failed')), self.report()], maximum=2)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['terminal_generation'], 2)

    def test_no_progress_and_period_two_oscillation_stop_without_success(self):
        first = self.report(('failed', 'failed'))
        second = self.report(('failed', 'failed'))
        result = self.audit([first, second])
        self.assertEqual((result['status'], result['reason']), ('stopped', 'no_progress'))
        self.assertFalse(result['success'])
        reports = [self.report(statuses) for statuses in
                   [('failed', 'not_run'), ('not_run', 'failed'),
                    ('failed', 'not_run'), ('not_run', 'failed')]]
        result = self.audit(reports)
        self.assertEqual((result['status'], result['reason']), ('stopped', 'oscillation'))

    def test_regression_stops_when_a_previous_passed_acceptance_fails(self):
        result = self.audit([self.report(('passed', 'failed')), self.report(('failed', 'not_run'))])
        self.assertEqual(result['reason'], 'regression')
        self.assertEqual(result['regressed_acceptance_ids'], ['AC-001'])
        self.assertFalse(result['success'])

    def test_limits_and_observation_exhaustion_are_distinct(self):
        cases = [({'maximum': 1}, 'max_generations', 'stopped'),
                 ({'costs': [11], 'budget': 10}, 'cost_limit', 'stopped'),
                 ({'costs': [10], 'budget': 10}, 'cost_limit', 'stopped'),
                 ({}, 'observations_exhausted', 'continue')]
        for options, reason, status in cases:
            with self.subTest(reason=reason, options=options):
                result = self.audit([self.report(('failed', 'failed'))], **options)
                self.assertEqual((result['status'], result['reason']), (status, reason))
                self.assertFalse(result['success'])

    def test_unknown_cost_is_not_zero_or_budget_safe(self):
        result = self.audit([self.report(('failed', 'failed'))], budget=100)
        self.assertEqual(result['cost'], {'known_subtotal_microusd': 0,
                                         'unknown_count': 1, 'budget_safe': None})
        self.assertEqual(result['status'], 'continue')

    def test_pass_at_cost_boundary_succeeds_but_over_budget_does_not(self):
        report = self.report()
        self.assertEqual(self.audit([report], costs=[10], budget=10)['status'], 'success')
        result = self.audit([report], costs=[11], budget=10)
        self.assertEqual((result['status'], result['reason']), ('stopped', 'cost_limit'))

    def test_unknown_and_stale_reports_never_complete(self):
        result = self.audit([self.report(('not_run', 'not_run'))])
        self.assertFalse(result['success'])
        report = self.report()
        self.write('notes/result.md', '# Changed after native snapshot\n')
        result = self.audit([report])
        self.assertEqual((result['status'], result['reason']), ('blocked', 'stale_evaluation'))

    def test_unknown_contract_and_superseded_contract_block_without_success(self):
        report = self.report()
        value = json.loads((self.vault / report).read_text(encoding='utf-8'))
        original_id = self.contract_id
        value['contract_id'] = '0' * 64
        self.write_json(report, value)
        self.contract_id = '0' * 64
        result = self.audit([report])
        self.assertEqual((result['status'], result['reason']), ('blocked', 'stale_evaluation'))
        self.assertFalse(result['success'])
        self.contract_id = original_id
        value['contract_id'] = original_id
        self.write_json(report, value)
        importlib.import_module('vault_task_contracts').revise(self.vault, original_id, 'input/task.json')
        result = self.audit([report])
        self.assertEqual((result['status'], result['reason']), ('blocked', 'stale_evaluation'))
        self.assertFalse(result['success'])

    def test_fixed_coverage_and_independent_requirement_cannot_be_weakened(self):
        first, second = self.report(('failed', 'failed')), self.report(('failed', 'not_run'))
        value = json.loads((self.vault / second).read_text(encoding='utf-8'))
        value['independent_required'] = True
        self.write_json(second, value)
        with self.assertRaisesRegex(cycles.CycleError, 'changed_evaluation_criteria'):
            self.audit([first, second])
        value['independent_required'] = False
        value['coverage_manifest'] = 'input/other-coverage.json'
        self.write_json(value['coverage_manifest'], self.coverage)
        self.write_json(second, value)
        with self.assertRaisesRegex(cycles.CycleError, 'changed_evaluation_criteria'):
            self.audit([first, second])

    def test_saved_summary_and_unknown_acceptance_cannot_forge_success(self):
        report = self.report(('failed', 'failed'))
        value = json.loads((self.vault / report).read_text(encoding='utf-8'))
        value['status'] = 'passed'
        self.write_json(report, value)
        with self.assertRaises(cycles.CycleError):
            self.audit([report])
        del value['status']
        value['reviews'][0]['acceptance_ids'] = ['unknown']
        self.write_json(report, value)
        with self.assertRaises(cycles.CycleError):
            self.audit([report])

    def test_duplicate_reordered_reports_and_boolean_cost_are_rejected(self):
        first, second = self.report(('failed', 'failed')), self.report(('failed', 'passed'))
        changes = [lambda x: x['generations'][0].update(cost_microusd=True),
                   lambda x: x['generations'].reverse(),
                   lambda x: x['generations'][1].update(evaluation_report=first),
                   lambda x: x.update(max_generations=True),
                   lambda x: x.update(max_cost_microusd=False),
                   lambda x: x.update(unrecognized='value')]
        for change in changes:
            with self.subTest(change=change):
                value = self.input([first, second])
                change(value)
                self.write_json('input/cycle.json', value)
                with self.assertRaises(cycles.CycleError):
                    cycles.audit(self.vault, 'input/cycle.json')

    def test_observations_after_success_are_strictly_rejected(self):
        with self.assertRaisesRegex(cycles.CycleError, 'observations_after_terminal'):
            self.audit([self.report(), self.report(('failed', 'failed'))])

    def test_cycle_input_cannot_double_as_review_support(self):
        report = self.report()
        self.input([report])
        raw = (self.vault / 'input/cycle.json').read_bytes()
        value = json.loads((self.vault / report).read_text(encoding='utf-8'))
        value['reviews'][0]['source'] = {
            'path': 'input/cycle.json', 'sha256': hashlib.sha256(raw).hexdigest(),
        }
        self.write_json(report, value)
        with self.assertRaisesRegex(cycles.CycleError, 'input_source_overlap'):
            cycles.audit(self.vault, 'input/cycle.json')

    def test_deny_exclude_credentials_and_secret_inputs_are_rejected(self):
        good = self.report()
        for path in ('private/report.json', 'excluded/report.json', 'input/.env.json'):
            self.write(path, (self.vault / good).read_bytes())
            with self.subTest(path=path), self.assertRaises(cycles.CycleError):
                self.audit([path])
        value = self.input([good])
        value['generations'][0]['evaluation_report'] = 'input/sk-' + 'a' * 48 + '.json'
        self.write_json('input/cycle.json', value)
        with self.assertRaises(cycles.CycleError):
            cycles.audit(self.vault, 'input/cycle.json')

    def test_credential_cycle_inputs_and_reports_are_rejected_before_content_read(self):
        report = self.report()
        definition = self.input([report])
        credential_paths = ('input/credentials.json', 'input/.ssh/id_rsa',
                            'keys/service.pem', 'input/.npmrc')
        real_read = evidence.stable_read
        for role in ('input', 'report'):
            for relative in credential_paths:
                with self.subTest(role=role, path=relative):
                    self.write_json(relative, definition if role == 'input' else
                                    json.loads((self.vault / report).read_text(encoding='utf-8')))
                    if role == 'report':
                        self.input([relative])
                    target = (self.vault / relative).resolve()
                    def guarded_read(resolver, *args, **kwargs):
                        self.assertNotEqual(resolver(), target, 'credential content read was attempted')
                        return real_read(resolver, *args, **kwargs)
                    with mock.patch.object(evidence, 'stable_read', side_effect=guarded_read):
                        with self.assertRaises(cycles.CycleError):
                            cycles.audit(self.vault, relative if role == 'input' else 'input/cycle.json')

    def test_input_and_source_changes_during_evaluation_are_rejected(self):
        self.input([self.report()])
        real_evaluate = cycles.staged.evaluate
        for changed_path in ('input/cycle.json', 'notes/request.md', '00-meta/vault-config.json'):
            with self.subTest(changed_path=changed_path):
                original = (self.vault / changed_path).read_bytes()
                def changing_evaluate(*args, **kwargs):
                    result = real_evaluate(*args, **kwargs)
                    self.write(changed_path, original + b' ')
                    return result
                try:
                    with mock.patch.object(cycles.staged, 'evaluate', side_effect=changing_evaluate):
                        with self.assertRaises(cycles.CycleError):
                            cycles.audit(self.vault, 'input/cycle.json')
                finally:
                    self.write(changed_path, original)

    def test_cli_outputs_advisory_result_and_content_free_errors(self):
        self.input([self.report()])
        command = [sys.executable, str(SCRIPTS / 'vault_improvement_cycles.py'),
                   '--vault', str(self.vault), 'audit', '--input', 'input/cycle.json']
        child = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(json.loads(child.stdout)['status'], 'success')
        self.write('input/cycle.json', '{')
        child = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(child.stderr, '')
        self.assertFalse(json.loads(child.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
