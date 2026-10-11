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

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_evidence as evidence
try:
    coverage = importlib.import_module('vault_coverage')
except ModuleNotFoundError:
    coverage = None


class VaultCoverageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(coverage, 'optional general-task coverage checker is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve() / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        })
        self.write('notes/result.md', '# Proposed result\n')
        self.write('output/check.log', 'Controlled fixture output.\n')
        self.manifest = {
            'version': 1, 'task': 'T-001', 'requirements': ['REQ-001'],
            'acceptance': [{'id': 'AC-001', 'requirements': ['REQ-001'], 'checks': ['C-001']}],
            'required_checks': ['C-001'],
        }
        self.write_json('input/coverage.json', self.manifest)

    def write(self, relative, raw):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, ensure_ascii=True, allow_nan=False))

    def receipt(self, checks=None, *, pending=False, task='T-001'):
        store = evidence.Store(self.vault)
        result = evidence.snapshot(store, SimpleNamespace(
            task=task, objective='Inspect the proposed result.', artifact=['notes/result.md']))
        if not pending:
            checks = checks if checks is not None else [('C-001', 'verified')]
            report = {'verifier': 'fixture-host', 'checks': [
                {'id': ident, 'claim': 'A host-supplied claim.', 'status': status,
                 'method': 'A host-supplied method.', 'observed': 'A host-supplied observation.',
                 'evidence': ['output/check.log'],
                 'preserve': 'Keep the checked condition.' if status == 'verified' else '',
                 'next_check': '' if status == 'verified' else 'Recheck the condition.'}
                for ident, status in checks
            ], 'next_action': 'Review the result.'}
            self.write_json('input/report-' + result['id'] + '.json', report)
            evidence.record(store, result['id'], 'input/report-' + result['id'] + '.json')
        return result['id']

    def check(self, record_id):
        self.write_json('input/coverage.json', self.manifest)
        return coverage.check(self.vault, 'input/coverage.json', record_id)

    def codes(self, result):
        return {issue['code'] for issue in result['issues']}

    def inventory(self):
        return {path.relative_to(self.vault).as_posix():
                (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.vault.rglob('*') if path.is_file()}

    def edit_receipt(self, record_id, transform):
        path = self.vault / evidence.STORE_DIR / (record_id + '.json')
        value = json.loads(path.read_text(encoding='utf-8'))
        transform(value)
        value['receipt_sha256'] = hashlib.sha256(evidence.encode({
            key: item for key, item in value.items() if key != 'receipt_sha256'})).hexdigest()
        path.write_bytes(evidence.encode(value) + b'\n')

    def test_complete_mapping_reports_only_data_completeness_with_provenance(self):
        record_id = self.receipt()
        result = self.check(record_id)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['authority'], 'coverage_data_only')
        self.assertFalse(result['semantic_fulfillment_verified'])
        self.assertFalse(result['execution_verified'])
        self.assertEqual(result['counts'], {
            'requirements': 1, 'acceptance': 1, 'required_checks': 1,
            'submitted_checks': 1, 'reported_verified_checks': 1,
        })
        raw = (self.vault / 'input/coverage.json').read_bytes()
        self.assertEqual(result['manifest']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['evidence']['id'], record_id)
        self.assertEqual(len(result['evidence']['receipt_sha256']), 64)
        self.assertEqual(result['issues'], [])
        self.assertGreaterEqual(len(result['limitations']), 3)

    def test_required_checks_are_independent_of_submitted_checks(self):
        record_id = self.receipt()
        self.manifest['required_checks'].append('C-002')
        self.manifest['acceptance'][0]['checks'].append('C-002')
        result = self.check(record_id)
        self.assertFalse(result['ok'])
        self.assertIn({'code': 'missing_check', 'id': 'C-002'}, result['issues'])
        self.assertIn('unexecuted_or_unreported_check', self.codes(result))
        self.assertEqual(result['counts']['required_checks'], 2)

    def test_unmapped_requirements_acceptance_and_checks_are_diagnosed(self):
        record_id = self.receipt([('C-001', 'verified'), ('C-002', 'verified')])
        self.manifest['requirements'].append('REQ-002')
        self.manifest['required_checks'].append('C-002')
        self.manifest['acceptance'].append({'id': 'AC-002', 'requirements': [], 'checks': []})
        result = self.check(record_id)
        self.assertFalse(result['ok'])
        self.assertIn({'code': 'unmapped_requirement', 'id': 'REQ-002'}, result['issues'])
        self.assertIn({'code': 'unmapped_acceptance', 'id': 'AC-002'}, result['issues'])
        self.assertIn({'code': 'unmapped_check', 'id': 'C-002'}, result['issues'])

    def test_unknown_mapping_targets_and_extra_submitted_checks_are_diagnosed(self):
        record_id = self.receipt([('C-001', 'verified'), ('C-EXTRA', 'verified')])
        self.manifest['acceptance'][0]['requirements'].append('REQ-UNKNOWN')
        self.manifest['acceptance'][0]['checks'].append('C-UNKNOWN')
        result = self.check(record_id)
        self.assertFalse(result['ok'])
        self.assertIn('unknown_requirement', self.codes(result))
        self.assertIn('unknown_check', self.codes(result))
        self.assertIn({'code': 'undeclared_check', 'id': 'C-EXTRA'}, result['issues'])

    def test_pending_receipt_is_not_reported_as_complete_or_actually_unexecuted(self):
        result = self.check(self.receipt(pending=True))
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['ok'])
        self.assertIn('report_not_recorded', self.codes(result))
        self.assertIn('unexecuted_or_unreported_check', self.codes(result))
        self.assertFalse(result['execution_verified'])

    def test_gap_failed_and_regression_outcomes_cannot_complete_coverage(self):
        for status, code in [('gap', 'unsupported_outcome'), ('failed', 'reported_failed'),
                             ('regression', 'reported_regression')]:
            with self.subTest(status=status):
                result = self.check(self.receipt([('C-001', status)]))
                self.assertFalse(result['ok'])
                self.assertIn({'code': code, 'id': 'C-001'}, result['issues'])

    def test_stale_candidate_evidence_and_report_each_block_completeness(self):
        for kind in ('artifact', 'evidence', 'report_source'):
            with self.subTest(kind=kind):
                record_id = self.receipt()
                receipt, _ = evidence.Store(self.vault).load(record_id)
                relative = {'artifact': receipt['artifacts'][0]['path'],
                            'evidence': receipt['evidence'][0]['path'],
                            'report_source': receipt['report_source']['path']}[kind]
                path = self.vault / relative
                previous = path.read_bytes()
                path.write_bytes(previous + b'\nchanged\n')
                result = self.check(record_id)
                self.assertEqual(result['status'], 'stale')
                self.assertFalse(result['ok'])
                self.assertIn('stale_evidence', self.codes(result))
                path.write_bytes(previous)

    def test_missing_bound_file_is_stale_evidence(self):
        record_id = self.receipt()
        (self.vault / 'output/check.log').unlink()
        result = self.check(record_id)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'stale')

    def test_task_mismatch_cannot_reuse_an_unrelated_receipt(self):
        result = self.check(self.receipt(task='OTHER-TASK'))
        self.assertFalse(result['ok'])
        self.assertIn('task_mismatch', self.codes(result))

    def test_manifest_ids_duplicates_and_exact_fields_are_validated(self):
        baseline = copy.deepcopy(self.manifest)
        invalid = [
            {**baseline, 'version': True}, {**baseline, 'requirements': ['REQ-001', 'REQ-001']},
            {**baseline, 'requirements': ['free-form']}, {**baseline, 'required_checks': []},
            {**baseline, 'required_checks': ['C-001', 'C-001']},
            {**baseline, 'acceptance': baseline['acceptance'] * 2},
            {**baseline, 'unknown': 'extra field'},
        ]
        record_id = self.receipt()
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                self.manifest = manifest
                with self.assertRaises(coverage.CoverageError):
                    self.check(record_id)

    def test_manifest_size_depth_and_collection_limits_are_enforced(self):
        record_id = self.receipt()
        oversized = b' ' * (coverage.MAX_MANIFEST_BYTES + 1)
        deep = '0'
        for _ in range(coverage.MAX_DEPTH + 1):
            deep = '[' + deep + ']'
        for raw in (oversized, deep.encode(), b'{"version":1,"version":1}', b'{"version":NaN}'):
            with self.subTest(size=len(raw)):
                self.write('input/coverage.json', raw)
                with self.assertRaises(coverage.CoverageError):
                    coverage.check(self.vault, 'input/coverage.json', record_id)
        self.manifest['requirements'] = ['REQ-' + str(i) for i in range(coverage.MAX_ITEMS + 1)]
        with self.assertRaises(coverage.CoverageError):
            self.check(record_id)

    def test_unsafe_denied_excluded_reserved_and_credentials_inputs_are_rejected(self):
        record_id = self.receipt()
        for path in ('../coverage.json', '/coverage.json', 'private/coverage.json',
                     'excluded/coverage.json', '.git/coverage.json', '.env', 'input/.env.local',
                     '00-meta/runtime/coverage.json', '00-meta/evidence/coverage.json'):
            with self.subTest(path=path):
                with self.assertRaises(coverage.CoverageError):
                    coverage.check(self.vault, path, record_id)

    def test_selected_receipt_credentials_and_excluded_files_are_rejected_before_binding_read(self):
        for path in ('private/check.log', 'excluded/check.log', 'output/.env.local'):
            with self.subTest(path=path):
                record_id = self.receipt()
                self.edit_receipt(record_id, lambda item: (
                    item['evidence'][0].update(path=path),
                    item['report']['checks'][0].update(evidence=[path])))
                original = evidence.bindings
                with mock.patch.object(evidence, 'bindings', wraps=original) as binding_read:
                    with self.assertRaises(coverage.CoverageError):
                        self.check(record_id)
                    binding_read.assert_not_called()

    def test_unknown_receipt_status_is_rejected_without_echoing_it(self):
        record_id = self.receipt()
        self.edit_receipt(record_id, lambda item: item['report']['checks'][0].update(status='invented'))
        with self.assertRaises(coverage.CoverageError) as raised:
            self.check(record_id)
        self.assertEqual(str(raised.exception), 'unsupported_evidence_outcome')

    def test_secret_values_in_manifest_are_not_emitted(self):
        record_id = self.receipt()
        self.manifest['task'] = 'sk-' + 'a' * 48
        with self.assertRaises(coverage.CoverageError) as raised:
            self.check(record_id)
        self.assertNotIn(self.manifest['task'], str(raised.exception))

    def test_secret_receipt_content_is_rejected_without_echoing_it(self):
        record_id = self.receipt()
        secret = 'sk-' + 'b' * 48
        self.edit_receipt(record_id, lambda item: item['report']['checks'][0].update(observed=secret))
        with self.assertRaises(coverage.CoverageError) as raised:
            self.check(record_id)
        self.assertEqual(str(raised.exception), 'sensitive_input')
        self.assertNotIn(secret, str(raised.exception))

    def test_known_secret_manifest_paths_are_rejected_before_open(self):
        record_id = self.receipt()
        original_open = os.open
        for name in ('secrets.json', 'credentials.json', 'service-account.json',
                     'token.json', 'auth-config.json', 'credentials.md'):
            with self.subTest(name=name):
                selected = self.write_json('input/' + name, self.manifest)
                opened = []
                def observe_open(path, *args, **kwargs):
                    opened.append(Path(path))
                    return original_open(path, *args, **kwargs)
                with mock.patch('os.open', side_effect=observe_open):
                    with self.assertRaises(coverage.CoverageError):
                        coverage.check(self.vault, 'input/' + name, record_id)
                self.assertNotIn(selected, opened)

    def test_known_secret_receipt_artifact_evidence_and_report_paths_are_rejected_before_open(self):
        original_open = os.open
        for kind, relative in [('artifact', 'notes/credentials.md'),
                               ('evidence', 'output/token.json'),
                               ('report_source', 'input/secrets.json')]:
            with self.subTest(kind=kind):
                record_id = self.receipt()
                selected = self.write(relative, 'PRIVATE_FIXTURE_BODY\n')
                def change_receipt(item):
                    if kind == 'artifact':
                        item['artifacts'][0]['path'] = relative
                    elif kind == 'evidence':
                        item['evidence'][0]['path'] = relative
                        item['report']['checks'][0]['evidence'] = [relative]
                    else:
                        item['report_source']['path'] = relative
                self.edit_receipt(record_id, change_receipt)
                opened = []
                def observe_open(path, *args, **kwargs):
                    opened.append(Path(path))
                    return original_open(path, *args, **kwargs)
                with mock.patch('os.open', side_effect=observe_open):
                    with self.assertRaises(coverage.CoverageError):
                        coverage.check(self.vault, 'input/coverage.json', record_id)
                self.assertNotIn(selected, opened)

    def test_corrupt_selected_receipt_cannot_supply_verified_outcomes(self):
        record_id = self.receipt()
        path = self.vault / evidence.STORE_DIR / (record_id + '.json')
        receipt = json.loads(path.read_text(encoding='utf-8'))
        receipt['objective'] = 'Changed without recomputing the receipt checksum.'
        path.write_bytes(evidence.encode(receipt))
        with self.assertRaises(coverage.CoverageError):
            self.check(record_id)

    def test_hardlinked_manifest_and_bound_evidence_are_unsafe(self):
        record_id = self.receipt()
        os.link(self.vault / 'input/coverage.json', self.vault / 'input/alias.json')
        with self.assertRaises(coverage.CoverageError):
            coverage.check(self.vault, 'input/coverage.json', record_id)
        (self.vault / 'input/alias.json').unlink()
        os.link(self.vault / 'output/check.log', self.vault / 'output/alias.log')
        result = coverage.check(self.vault, 'input/coverage.json', record_id)
        self.assertEqual(result['status'], 'stale')
        self.assertFalse(result['ok'])

    def test_selected_missing_receipt_does_not_scan_for_substitutes(self):
        self.receipt()
        with mock.patch.object(Path, 'glob', side_effect=AssertionError('scan forbidden')):
            with self.assertRaises(coverage.CoverageError):
                coverage.check(self.vault, 'input/coverage.json', '0' * 32)

    def test_report_claim_observation_and_method_are_never_echoed(self):
        record_id = self.receipt()
        self.edit_receipt(record_id, lambda item: item['report']['checks'][0].update(
            claim='PRIVATE_CLAIM_MARKER', method='PRIVATE_METHOD_MARKER',
            observed='PRIVATE_OBSERVATION_MARKER'))
        result = self.check(record_id)
        self.assertTrue(result['ok'])
        serialized = json.dumps(result)
        for value in ('PRIVATE_CLAIM_MARKER', 'PRIVATE_METHOD_MARKER', 'PRIVATE_OBSERVATION_MARKER'):
            self.assertNotIn(value, serialized)

    def test_manifest_and_receipt_changes_during_check_fail_closed(self):
        for selected in ('manifest', 'receipt'):
            with self.subTest(selected=selected):
                record_id = self.receipt()
                original = evidence.bindings
                changed = False
                def racing_read(*args, **kwargs):
                    nonlocal changed
                    result = original(*args, **kwargs)
                    if not changed:
                        changed = True
                        relative = ('input/coverage.json' if selected == 'manifest' else
                                    evidence.STORE_DIR + '/' + record_id + '.json')
                        path = self.vault / relative
                        path.write_bytes(path.read_bytes() + b'\n')
                    return result
                with mock.patch.object(evidence, 'bindings', side_effect=racing_read):
                    result = self.check(record_id)
                self.assertFalse(result['ok'])
                self.assertIn('changed_during_check', self.codes(result))

    def test_read_only_offline_cli_and_structured_exit_codes(self):
        record_id = self.receipt()
        before = self.inventory()
        with (mock.patch('socket.socket', side_effect=AssertionError('network forbidden')),
              mock.patch('subprocess.run', side_effect=AssertionError('commands forbidden'))):
            result = coverage.check(self.vault, 'input/coverage.json', record_id)
        self.assertTrue(result['ok'])
        self.assertEqual(self.inventory(), before)
        script = SCRIPTS / 'vault_coverage.py'
        command = [sys.executable, str(script), '--vault', str(self.vault), 'check',
                   '--manifest', 'input/coverage.json', '--evidence', record_id]
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, '')
        self.assertEqual(json.loads(process.stdout)['status'], 'complete')
        self.assertEqual(self.inventory(), before)
        (self.vault / 'output/check.log').unlink()
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)['status'], 'stale')
        command[-1] = 'not-an-id'
        process = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stderr, '')
        self.assertEqual(json.loads(process.stdout)['status'], 'error')


if __name__ == '__main__':
    unittest.main()
