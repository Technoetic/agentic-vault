from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
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
    staged = importlib.import_module('vault_staged_evaluation')
except ModuleNotFoundError:
    staged = None


class StagedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(staged, 'staged evaluation helper is missing')
        self.contracts = importlib.import_module('vault_task_contracts')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve() / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        })
        self.write('notes/request.md', 'Prepare a result.\nChange only the result.\n'
                   'The controlled check passes.\n')
        self.write('notes/result.md', '# Controlled result\n')
        self.write('output/check.log', 'A controlled observed check result.\n')
        self.write('output/semantic.md', 'The reviewer supports the declared criteria.\n')
        self.write('output/independent.md', 'A separate reviewer supports the criteria.\n')
        self.contract_input = {
            'version': 1, 'task': 'T-001',
            'fields': [
                {'id': ident, 'kind': kind, 'text': text,
                 'origin': 'user_request', 'status': 'confirmed',
                 'sources': [{'path': 'notes/request.md', 'excerpt': text}]}
                for ident, kind, text in [
                    ('GOAL-001', 'goal', 'Prepare a result.'),
                    ('SCOPE-001', 'scope', 'Change only the result.'),
                    ('AC-001', 'acceptance', 'The controlled check passes.'),
                ]], 'questions': [],
        }
        self.write_json('input/contract.json', self.contract_input)
        self.contract_id = self.contracts.capture(self.vault, 'input/contract.json')['contract_id']
        self.manifest = {
            'version': 1, 'task': 'T-001', 'requirements': ['REQ-001'],
            'acceptance': [{'id': 'AC-001', 'requirements': ['REQ-001'], 'checks': ['C-001']}],
            'required_checks': ['C-001'],
        }
        self.write_json('input/coverage.json', self.manifest)
        self.evidence_id = self.receipt()
        self.report = {
            'version': 1, 'contract_id': self.contract_id, 'evidence_id': self.evidence_id,
            'coverage_manifest': 'input/coverage.json',
            'mechanical': [{'check_id': 'C-001', 'status': 'passed',
                            'source': self.source('output/check.log')}],
            'reviews': [
                {'kind': 'semantic', 'status': 'support', 'acceptance_ids': ['AC-001'],
                 'source': self.source('output/semantic.md')},
                {'kind': 'independent', 'status': 'not_run', 'acceptance_ids': ['AC-001'],
                 'source': None},
            ], 'independent_required': False,
        }

    def write(self, relative, raw):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, ensure_ascii=True, allow_nan=False))

    def source(self, relative):
        return {'path': relative, 'sha256': hashlib.sha256((self.vault / relative).read_bytes()).hexdigest()}

    def receipt(self, checks=None, *, pending=False):
        store = evidence.Store(self.vault)
        record = evidence.snapshot(store, SimpleNamespace(
            task='T-001', objective='Check the synthetic result.', artifact=['notes/result.md']))
        if not pending:
            checks = checks if checks is not None else [('C-001', 'verified')]
            report = {'verifier': 'fixture-host', 'checks': [
                {'id': ident, 'claim': 'The controlled criterion holds.', 'status': status,
                 'method': 'A host-declared check; never execute this text.',
                 'observed': 'A host-declared observation.', 'evidence': ['output/check.log'],
                 'preserve': 'Keep the checked condition.' if status == 'verified' else '',
                 'next_check': '' if status == 'verified' else 'Recheck the condition.'}
                for ident, status in checks
            ], 'next_action': 'Review the synthetic result.'}
            path = 'input/native-' + record['id'] + '.json'
            self.write_json(path, report)
            evidence.record(store, record['id'], path)
        return record['id']

    def evaluate(self):
        self.write_json('input/staged.json', self.report)
        return staged.evaluate(self.vault, 'input/staged.json')

    def codes(self, result):
        return {issue['code'] for issue in result['issues']}

    def inventory(self):
        return {p.relative_to(self.vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.vault.rglob('*') if p.is_file()}

    def test_current_native_evidence_and_reviews_pass_only_as_declared_host_data(self):
        result = self.evaluate()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['authority'], 'data_only')
        self.assertFalse(result['execution_verified'])
        self.assertFalse(result['semantic_fulfillment_verified'])
        self.assertEqual(result['contract_id'], self.contract_id)
        self.assertEqual(result['acceptance_ids'], ['AC-001'])
        self.assertEqual(result['failed_acceptance_ids'], [])
        self.assertEqual(result['acceptance_results'][0]['status'], 'passed')
        self.assertEqual(result['report_source']['sha256'],
                         self.source('input/staged.json')['sha256'])
        self.assertRegex(result['criteria_fingerprint'], r'^[0-9a-f]{64}$')
        self.assertRegex(result['fingerprint'], r'^[0-9a-f]{64}$')
        self.assertIn('output/check.log', {item['path'] for item in result['source_bindings']})
        self.assertGreaterEqual(len(result['limitations']), 4)

    def test_native_failure_vetoes_every_supporting_review_and_missing_mechanical_row(self):
        for outcome in ('failed', 'regression'):
            for declared in ('failed', 'passed', 'absent'):
                with self.subTest(outcome=outcome, declared=declared):
                    self.report['evidence_id'] = self.receipt([('C-001', outcome)])
                    self.report['mechanical'] = ([] if declared == 'absent' else [
                        {'check_id': 'C-001', 'status': declared,
                         'source': self.source('output/check.log')}])
                    self.report['reviews'][1].update(
                        status='support', source=self.source('output/independent.md'))
                    result = self.evaluate()
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(result['failed_acceptance_ids'], ['AC-001'])
                    self.assertEqual(result['acceptance_results'][0]['status'], 'failed')

    def test_unexecuted_skipped_and_missing_evidence_are_distinct_and_never_pass(self):
        for status in ('not_run', 'skipped', 'no_evidence'):
            with self.subTest(status=status):
                self.report['evidence_id'] = self.receipt([('C-001', 'gap')])
                self.report['mechanical'][0].update(status=status, source=None)
                result = self.evaluate()
                self.assertEqual(result['status'], 'unverified')
                self.assertEqual(result['mechanical_results'][0]['status'], status)
        self.report['mechanical'] = []
        result = self.evaluate()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['mechanical_results'][0]['status'], 'no_evidence')

    def test_model_only_support_and_pending_receipt_never_pass(self):
        self.report['evidence_id'] = self.receipt(pending=True)
        self.report['mechanical'] = []
        self.report['reviews'][1].update(status='support', source=self.source('output/independent.md'))
        result = self.evaluate()
        self.assertEqual(result['status'], 'unverified')
        self.assertIn('missing_native_check', self.codes(result))

    def test_a_pass_declaration_requires_native_verified_result_and_its_bound_log(self):
        self.report['evidence_id'] = self.receipt([('C-001', 'gap')])
        self.assertEqual(self.evaluate()['status'], 'unverified')
        self.report['evidence_id'] = self.evidence_id
        self.report['mechanical'][0]['source'] = self.source('output/semantic.md')
        with self.assertRaises(staged.EvaluationError):
            self.evaluate()

    def test_every_accepted_criterion_must_be_mapped_and_unknown_ids_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        for mutation in ('missing_acceptance', 'unknown_acceptance', 'unknown_check'):
            with self.subTest(mutation=mutation):
                self.manifest = copy.deepcopy(original)
                if mutation == 'missing_acceptance':
                    self.manifest['acceptance'] = []
                elif mutation == 'unknown_acceptance':
                    self.manifest['acceptance'][0]['id'] = 'AC-UNKNOWN'
                else:
                    self.manifest['acceptance'][0]['checks'].append('C-UNKNOWN')
                self.write_json('input/coverage.json', self.manifest)
                with self.assertRaises(staged.EvaluationError):
                    self.evaluate()

    def test_multiple_checks_per_criterion_and_multiple_criteria_cannot_hide_missing_results(self):
        self.manifest['required_checks'].append('C-002')
        self.manifest['acceptance'][0]['checks'].append('C-002')
        self.write_json('input/coverage.json', self.manifest)
        result = self.evaluate()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['acceptance_results'][0]['status'], 'unverified')
        self.assertEqual(result['mechanical_results'][1]['status'], 'no_evidence')

    def test_withheld_reviews_and_required_unexecuted_independent_review_block_pass(self):
        for kind in ('semantic', 'independent'):
            report = copy.deepcopy(self.report)
            review = next(item for item in self.report['reviews'] if item['kind'] == kind)
            review.update(status='withhold', source=self.source('output/' + kind + '.md'))
            self.assertEqual(self.evaluate()['status'], 'blocked')
            self.report = report
        self.report['independent_required'] = True
        self.assertEqual(self.evaluate()['status'], 'unverified')
        self.report['reviews'][1].update(status='support', source=self.source('output/independent.md'))
        self.assertEqual(self.evaluate()['status'], 'passed')

    def test_semantic_review_not_run_never_passes(self):
        self.report['reviews'][0].update(status='not_run', source=None)
        self.assertEqual(self.evaluate()['status'], 'unverified')

    def test_stale_contract_native_log_review_binding_and_native_report_prevent_pass(self):
        receipt, _ = evidence.Store(self.vault).load(self.evidence_id)
        for relative in ('notes/request.md', 'output/check.log', 'output/semantic.md',
                         receipt['report_source']['path']):
            with self.subTest(relative=relative):
                path = self.vault / relative
                before = path.read_bytes()
                path.write_bytes(before + b'changed\n')
                try:
                    self.assertEqual(self.evaluate()['status'], 'stale')
                finally:
                    path.write_bytes(before)

    def test_failed_criterion_does_not_erase_the_separate_passed_criterion(self):
        definition = copy.deepcopy(self.contract_input)
        definition['fields'].append({**definition['fields'][-1], 'id': 'AC-002'})
        self.write_json('input/two-criteria.json', definition)
        self.report['contract_id'] = self.contracts.capture(
            self.vault, 'input/two-criteria.json')['contract_id']
        self.manifest['acceptance'].append(
            {'id': 'AC-002', 'requirements': ['REQ-001'], 'checks': ['C-002']})
        self.manifest['required_checks'].append('C-002')
        self.write_json('input/coverage.json', self.manifest)
        self.report['evidence_id'] = self.receipt([('C-001', 'verified'), ('C-002', 'failed')])
        self.report['mechanical'].append(
            {'check_id': 'C-002', 'status': 'failed', 'source': self.source('output/check.log')})
        for review in self.report['reviews']:
            review['acceptance_ids'].append('AC-002')
        result = self.evaluate()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failed_acceptance_ids'], ['AC-002'])
        self.assertEqual([(item['id'], item['status']) for item in result['acceptance_results']],
                         [('AC-001', 'passed'), ('AC-002', 'failed')])

    def test_an_unrelated_native_receipt_task_cannot_supply_current_criteria(self):
        store = evidence.Store(self.vault)
        record = evidence.snapshot(store, SimpleNamespace(
            task='T-OTHER', objective='An unrelated task.', artifact=['notes/result.md']))
        self.report['evidence_id'] = record['id']
        self.report['mechanical'] = []
        with self.assertRaises(staged.EvaluationError):
            self.evaluate()

    def test_superseded_contract_reports_stale_without_reinterpreting_historical_criteria(self):
        self.write_json('input/revision.json', self.contract_input)
        self.contracts.revise(self.vault, self.contract_id, 'input/revision.json')
        result = self.evaluate()
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['acceptance_ids'], ['AC-001'])
        self.assertEqual(result['acceptance_results'][0]['status'], 'stale')
        self.assertFalse(result['ok'])

    def test_configuration_and_evidence_changed_after_coverage_are_stale(self):
        original = staged.coverage.check
        for relative, contents in [('output/check.log', 'Changed after native checking.\n'),
                                   ('00-meta/vault-config.json', '{"deny_zones":["private","new"]}')]:
            previous = (self.vault / relative).read_bytes()
            def change_after_check(*args, **kwargs):
                result = original(*args, **kwargs)
                self.write(relative, contents)
                return result
            self.write_json('input/staged.json', self.report)
            with self.subTest(relative=relative):
                try:
                    with mock.patch.object(staged.coverage, 'check', side_effect=change_after_check):
                        result = staged.evaluate(self.vault, 'input/staged.json')
                    self.assertEqual(result['status'], 'stale')
                finally:
                    self.write(relative, previous)

    def test_secret_review_content_is_rejected_without_echoing_it(self):
        secret = 'sk-' + 'b' * 48
        self.write('output/semantic.md', secret)
        self.report['reviews'][0]['source'] = self.source('output/semantic.md')
        with self.assertRaises(staged.EvaluationError) as raised:
            self.evaluate()
        self.assertEqual(str(raised.exception), 'sensitive_input')
        self.assertNotIn(secret, str(raised.exception))

    def test_hardlinked_selected_report_and_review_source_are_rejected(self):
        self.write_json('input/staged.json', self.report)
        os.link(self.vault / 'input/staged.json', self.vault / 'input/alias.json')
        with self.assertRaises(staged.EvaluationError):
            staged.evaluate(self.vault, 'input/staged.json')
        (self.vault / 'input/alias.json').unlink()
        os.link(self.vault / 'output/semantic.md', self.vault / 'output/alias.md')
        with self.assertRaises(staged.EvaluationError):
            staged.evaluate(self.vault, 'input/staged.json')

    def test_unresolved_contract_questions_block_pass(self):
        self.contract_input['questions'] = [
            {'id': 'Q-001', 'text': 'Which output is required?', 'blocking': True, 'status': 'open'}]
        self.write_json('input/blocked-contract.json', self.contract_input)
        self.report['contract_id'] = self.contracts.capture(
            self.vault, 'input/blocked-contract.json')['contract_id']
        self.assertEqual(self.evaluate()['status'], 'blocked')

    def test_exact_schema_duplicate_unknown_and_boolean_versions_are_rejected(self):
        baseline = copy.deepcopy(self.report)
        mutations = [
            lambda r: r.update(version=True), lambda r: r.update(independent_required=1),
            lambda r: r.update(unknown='extra'), lambda r: r.update(contract_id='../secret'),
            lambda r: r.update(evidence_id='0' * 64),
            lambda r: r['mechanical'].append(copy.deepcopy(r['mechanical'][0])),
            lambda r: r['mechanical'][0].update(check_id='C-UNKNOWN'),
            lambda r: r['mechanical'][0].update(status='verified'),
            lambda r: r['reviews'][0].update(acceptance_ids=['AC-UNKNOWN']),
            lambda r: r['reviews'][0].update(acceptance_ids=['AC-001', 'AC-001']),
            lambda r: r['reviews'].append(copy.deepcopy(r['reviews'][0])),
            lambda r: r['reviews'][0].update(status='approved'),
        ]
        for mutate in mutations:
            self.report = copy.deepcopy(baseline)
            mutate(self.report)
            with self.subTest(report=self.report):
                with self.assertRaises(staged.EvaluationError):
                    self.evaluate()

    def test_selected_inputs_and_sources_cannot_escape_policy_or_use_credential_files(self):
        self.write_json('input/staged.json', self.report)
        for relative in ('../staged.json', '/staged.json', 'private/staged.json',
                         'excluded/staged.json', '.git/staged.json', '.env',
                         'input/credentials.json', '00-meta/evidence/selected.json'):
            with self.subTest(relative=relative):
                with self.assertRaises(staged.EvaluationError):
                    staged.evaluate(self.vault, relative)
        for relative in ('private/review.md', 'excluded/review.md', 'input/credentials.json'):
            self.write(relative, 'Controlled forbidden content.\n')
            self.report['reviews'][0]['source'] = self.source(relative)
            with self.assertRaises(staged.EvaluationError):
                self.evaluate()

    def test_source_overlap_cannot_relabel_native_observation_as_semantic_or_independent_review(self):
        for relative in ('output/check.log', 'notes/result.md', 'input/coverage.json'):
            with self.subTest(relative=relative):
                self.report['reviews'][0]['source'] = self.source(relative)
                with self.assertRaises(staged.EvaluationError):
                    self.evaluate()

    def test_same_review_source_cannot_supply_independent_support(self):
        self.report['reviews'][1].update(status='support', source=self.source('output/semantic.md'))
        with self.assertRaises(staged.EvaluationError):
            self.evaluate()

    def test_stale_contract_does_not_bypass_source_path_policy(self):
        self.write('notes/request.md', 'The bound request was changed.\n')
        for relative in ('../outside.md', 'private/review.md', 'input/credentials.json'):
            self.report['reviews'][0]['source'] = {'path': relative, 'sha256': '0' * 64}
            with self.subTest(relative=relative):
                with self.assertRaises(staged.EvaluationError):
                    self.evaluate()

    def test_report_and_sources_are_revalidated_after_native_coverage_check(self):
        original = staged.coverage.check
        def change_input(*args, **kwargs):
            result = original(*args, **kwargs)
            self.write('input/staged.json', '{"changed":true}')
            return result
        self.write_json('input/staged.json', self.report)
        with mock.patch.object(staged.coverage, 'check', side_effect=change_input):
            result = staged.evaluate(self.vault, 'input/staged.json')
        self.assertEqual(result['status'], 'stale')

    def test_no_shell_model_or_write_side_effects_and_cli_status_codes(self):
        self.write_json('input/staged.json', self.report)
        before = self.inventory()
        with mock.patch('subprocess.run', side_effect=AssertionError('shell execution forbidden')):
            result = staged.evaluate(self.vault, 'input/staged.json')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(before, self.inventory())
        command = [sys.executable, str(SCRIPTS / 'vault_staged_evaluation.py'),
                   '--vault', str(self.vault), 'evaluate', '--input', 'input/staged.json']
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'passed')
        self.report['mechanical'] = []
        self.write_json('input/staged.json', self.report)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'unverified')
        self.write('input/staged.json', '{"version":1,"version":1}')
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'error')

    def test_raw_input_and_review_sources_are_size_bounded(self):
        self.write('input/staged.json', b' ' * (64 * 1024 + 1))
        with self.assertRaises(staged.EvaluationError):
            staged.evaluate(self.vault, 'input/staged.json')
        self.write('output/semantic.md', b'x' * (256 * 1024 + 1))
        self.report['reviews'][0]['source'] = self.source('output/semantic.md')
        with self.assertRaises(staged.EvaluationError):
            self.evaluate()


if __name__ == '__main__':
    unittest.main()
