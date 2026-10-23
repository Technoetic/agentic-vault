from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_vault_runs as fixture_module

runs = fixture_module.runs


class EvaluatorBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.VaultRunsTests(
            'test_different_candidates_share_fixed_test_set_and_compare_real_outcomes')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.vault = self.fixture.vault
        self.fixture.write('evaluator/grade.py', '# Fixed grader snapshot\n')
        self.fixture.write('evaluator/support.py', '# Fixed support snapshot\n')
        self.fixture.write_json('evaluator/config.json', {'minimum_evidence': 1})

    def prepare(self, candidate='notes/candidate.md', **kwargs):
        options = dict(evaluator_sources=['evaluator/grade.py'],
                       evaluator_config='evaluator/config.json')
        options.update(kwargs)
        return self.fixture.prepare(candidate, **options)

    def pair(self, **kwargs):
        before = self.prepare('notes/baseline.md', candidate_version='v1', **kwargs)
        after = self.prepare(**kwargs)
        self.fixture.complete(before, soft=False, name='before')
        self.fixture.complete(after, name='after')
        return before, after

    def test_no_options_preserve_legacy_manifest_and_id(self):
        prepared = self.fixture.prepare()
        manifest = runs.inspect(self.vault, prepared['id'])['manifest']
        self.assertNotIn('evaluator', manifest)
        self.assertEqual(prepared['id'], runs.evidence.digest(runs.evidence.encode(manifest)))
        # Passing the optional defaults explicitly must also leave old data alone.
        self.assertEqual(prepared, self.fixture.prepare(
            evaluator_sources=None, evaluator_config=None))

    def test_bound_evaluator_is_common_condition_but_candidate_can_differ(self):
        before, after = self.pair()
        self.assertNotEqual(before['id'], after['id'])
        self.assertEqual(before['compatibility_sha256'], after['compatibility_sha256'])
        manifest = runs.inspect(self.vault, after['id'])['manifest']
        binding = manifest['evaluator']
        self.assertEqual(binding['sources'][0]['path'], 'evaluator/grade.py')
        self.assertEqual(binding['config']['path'], 'evaluator/config.json')
        comparison = runs.compare(self.vault, before['id'], after['id'])
        self.assertTrue(comparison['eligible'])
        self.assertEqual(comparison['score_delta'], 0.5)

    def test_optional_config_and_source_order_are_canonical(self):
        a = self.prepare(evaluator_sources=['evaluator/support.py', 'evaluator/grade.py'],
                         evaluator_config=None)
        b = self.prepare(evaluator_sources=['evaluator/grade.py', 'evaluator/support.py'],
                         evaluator_config=None)
        self.assertEqual(a, b)
        self.assertIsNone(runs.inspect(self.vault, a['id'])['manifest']['evaluator']['config'])

    def test_changed_grader_or_config_cannot_record_or_inspect_or_compare(self):
        before, after = self.pair()
        for relative in ('evaluator/grade.py', 'evaluator/config.json'):
            with self.subTest(relative=relative):
                old = (self.vault / relative).read_bytes()
                self.fixture.write(relative, old + b'\n')
                with self.assertRaises(runs.RunError):
                    runs.inspect(self.vault, after['id'])
                with self.assertRaises(runs.RunError):
                    runs.compare(self.vault, before['id'], after['id'])
                # A host's all-passed report cannot overcome a changed grader.
                pending = self.prepare(candidate_version='pending')
                self.fixture.write(relative, old)
                self.fixture.write_json('input/changed-grader-report.json', self.fixture.report(pending))
                with self.assertRaises(runs.RunError):
                    runs.record(self.vault, pending['id'], 'input/changed-grader-report.json')
                # Restore fixtures and isolate the next subcase from immutable receipts.
                self.fixture.write(relative, old)

    def test_different_evaluators_or_unbound_pair_cannot_compare(self):
        old = self.fixture.prepare('notes/baseline.md')
        bound = self.prepare()
        other = self.prepare(evaluator_sources=['evaluator/support.py'])
        for item, name in ((old, 'unbound'), (bound, 'bound'), (other, 'other')):
            self.fixture.complete(item, name=name)
        for left, right in ((old, bound), (bound, other)):
            with self.subTest(left=left['id'], right=right['id']):
                with self.assertRaisesRegex(runs.RunError, 'incompatible_execution_contracts'):
                    runs.compare(self.vault, left['id'], right['id'])

    def test_evaluator_cannot_be_a_treatment_or_fixture_input(self):
        for path in ('notes/candidate.md', 'notes/prompt.md', 'input/fixture.json',
                     'input/tools.json', 'evaluator/config.json'):
            with self.subTest(path=path):
                with self.assertRaises(runs.RunError):
                    self.prepare(evaluator_sources=[path])
        with self.assertRaises(runs.RunError):
            self.prepare(evaluator_config='notes/prompt.md')

    def test_missing_empty_duplicate_or_unsafe_evaluator_selection_is_rejected(self):
        for sources in ([], 'evaluator/grade.py', ['evaluator/grade.py'] * 2,
                        ['missing.py'], ['../outside.py'], ['private/grader.py'],
                        ['excluded/grader.py'], ['evaluator/.env']):
            with self.subTest(sources=sources):
                with self.assertRaises(runs.RunError):
                    self.prepare(evaluator_sources=sources)
        with self.assertRaises(runs.RunError):
            self.prepare(evaluator_sources=None)

    def test_evaluator_config_is_a_bounded_json_object(self):
        for value in ('not JSON', '[]', '{"a": 1, "a": 2}', '{"a": NaN}'):
            self.fixture.write('evaluator/invalid.json', value)
            with self.subTest(value=value):
                with self.assertRaises(runs.RunError):
                    self.prepare(evaluator_config='evaluator/invalid.json')

    def test_evaluator_selection_obeys_count_byte_and_sensitive_limits(self):
        with self.assertRaises(runs.RunError):
            self.prepare(evaluator_sources=['evaluator/grade.py'] * (runs.MAX_FILES + 1))
        self.fixture.write('evaluator/large.py', 'x' * (runs.MAX_SOURCE_BYTES + 1))
        with self.assertRaises(runs.RunError):
            self.prepare(evaluator_sources=['evaluator/large.py'])
        self.fixture.write('evaluator/secret.py', 'sk-' + 'testcredential' * 8)
        with self.assertRaises(runs.RunError):
            self.prepare(evaluator_sources=['evaluator/secret.py'])
        with mock.patch.object(runs, 'MAX_TOTAL_BYTES', 32):
            with self.assertRaises(runs.RunError):
                self.prepare()

    def test_evaluator_cannot_be_report_or_observation_evidence(self):
        prepared = self.prepare()
        for path in ('evaluator/grade.py', 'evaluator/config.json'):
            report = self.fixture.report(prepared)
            for outcome in report['outcomes']:
                outcome['evidence'] = [path]
            self.fixture.write_json('input/overlap-report.json', report)
            with self.subTest(path=path):
                with self.assertRaises(runs.RunError):
                    runs.record(self.vault, prepared['id'], 'input/overlap-report.json')

    def test_publish_rechecks_evaluator_after_capture(self):
        original = runs.Store.publish

        def publish(store, *args, **kwargs):
            self.fixture.write('evaluator/grade.py', '# Changed immediately before publish\n')
            return original(store, *args, **kwargs)

        with mock.patch.object(runs.Store, 'publish', publish):
            with self.assertRaises(runs.RunError):
                self.prepare()
        self.assertFalse(list((self.vault / runs.RUNTIME_DIR).glob('run-*-prepared.json')))

    def test_recomputed_receipt_cannot_change_evaluator_without_source_binding(self):
        prepared = self.prepare()
        store = runs.Store(self.vault)
        receipt = store.read_record(runs._names(prepared['id'])[0])
        forged = copy.deepcopy(receipt['payload'])
        forged['evaluator']['sources'][0]['sha256'] = '0' * 64
        new_id = runs.evidence.digest(runs.evidence.encode(forged))
        receipt['payload'], receipt['id'] = forged, new_id
        receipt['receipt_sha256'] = runs.evidence.digest(runs.evidence.encode({
            key: value for key, value in receipt.items() if key != 'receipt_sha256'}))
        path = store.record_path(runs._names(new_id)[0])
        path.write_bytes(runs.evidence.encode(receipt))
        with self.assertRaises(runs.RunError):
            runs.inspect(self.vault, new_id)

    def test_bound_run_rejects_retyped_outcome_with_recomputed_checksum(self):
        prepared = self.prepare()
        self.fixture.complete(prepared)
        store = runs.Store(self.vault)
        name = runs._names(prepared['id'])[1]
        receipt = store.read_record(name)
        # Python compares 1 == True; serialized source linkage must remain strict.
        receipt['payload']['outcomes'][0]['assertions'][0]['passed'] = 1
        receipt['receipt_sha256'] = runs.evidence.digest(runs.evidence.encode({
            key: value for key, value in receipt.items() if key != 'receipt_sha256'}))
        store.record_path(name).write_bytes(runs.evidence.encode(receipt))
        with self.assertRaises(runs.RunError):
            runs.inspect(self.vault, prepared['id'])

    def test_cli_can_prepare_bound_evaluator_and_refuses_missing_sources(self):
        script = Path(runs.__file__)
        args = [sys.executable, '-X', 'utf8', str(script), '--vault', str(self.vault),
                'prepare', '--candidate', 'notes/candidate.md', '--source', 'notes/prompt.md',
                '--fixture', 'input/fixture.json', '--tools', 'input/tools.json',
                '--model', 'fixture-model', '--provider', 'fixture-provider', '--token-budget', '2048']
        completed = subprocess.run(args + ['--evaluator-source', 'evaluator/grade.py',
            '--evaluator-config', 'evaluator/config.json'], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        inspected = runs.inspect(self.vault, json.loads(completed.stdout)['id'])
        self.assertIn('evaluator', inspected['manifest'])
        invalid = subprocess.run(args + ['--evaluator-config', 'evaluator/config.json'],
                                 capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stderr, '')


if __name__ == '__main__':
    unittest.main()
