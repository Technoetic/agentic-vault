"""Real offline CLI boundaries for caller-supplied routing observations."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'evaluate_skill_routing.py'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class SkillRoutingCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Canonicalize only the trusted harness root (macOS /var is an alias).
        self.directory = Path(self.temporary.name).resolve()
        self.catalog = {'kind': 'skill_routing_catalog', 'schema_version': 1,
                        'source_kind': 'synthetic', 'skills': [
                            {'id': 'test:alpha', 'name': 'alpha', 'description': 'Inspect routing.',
                             'source_sha256': 'a' * 64, 'source_path': 'alpha/SKILL.md'},
                            {'id': 'test:beta', 'name': 'beta', 'description': 'Write routing.',
                             'source_sha256': 'b' * 64, 'source_path': 'beta/SKILL.md'}]}
        self.suite = {'kind': 'skill_routing_suite', 'schema_version': 1,
                      'suite_id': 'cli-original', 'cases': [
                          {'id': 'task', 'prompt': '라우팅을 확인하세요.', 'context': {},
                           'expect': {'required': ['test:alpha'], 'allowed': [],
                                      'forbidden': ['test:beta'], 'before': [],
                                      'entrypoints': ['test:alpha']}},
                          {'id': 'negative', 'prompt': 'Hello.', 'context': {},
                           'expect': {'required': [], 'allowed': [], 'forbidden': [],
                                      'before': [], 'entrypoints': []}}]}
        self.trace = {'kind': 'skill_routing_trace', 'schema_version': 1,
                      'suite_sha256': digest(self.suite), 'catalog_sha256': digest(self.catalog),
                      'source_kind': 'scripted_control', 'observation_kind': 'skill_invocation',
                      'runtime': {'host': 'test-host', 'version': '1', 'model': 'none'},
                      'observations': [{'case_id': 'task', 'status': 'complete',
                                        'invoked': ['test:alpha']},
                                       {'case_id': 'negative', 'status': 'complete', 'invoked': []}]}
        self.write_inputs()

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
        return path

    def write_inputs(self):
        self.catalog_path = self.write('catalog.json', self.catalog)
        self.suite_path = self.write('suite.json', self.suite)
        self.trace_path = self.write('trace.json', self.trace)

    def run_cli(self, *arguments, environment=None):
        return subprocess.run([sys.executable, str(CLI), *map(str, arguments)],
                              cwd=ROOT, capture_output=True, timeout=20, env=environment)

    def evaluate(self, *extra):
        return self.run_cli('evaluate', '--catalog', self.catalog_path,
                            '--cases', self.suite_path, '--trace', self.trace_path, *extra)

    def report(self, result, exit_code=0):
        self.assertEqual(result.returncode, exit_code, result.stderr.decode('utf-8', errors='replace'))
        return json.loads(result.stdout.decode('utf-8'))

    def evaluation_report(self):
        return self.report(self.evaluate())

    def compare(self, before, after):
        return self.run_cli('compare', '--before', self.write('before.json', before),
                            '--after', self.write('after.json', after))

    def rehash(self, report):
        report['report_sha256'] = digest({key: value for key, value in report.items()
                                          if key != 'report_sha256'})
        return report

    def test_pass_report_binds_raw_inputs_sources_and_privacy(self):
        report = self.evaluation_report()
        self.assertEqual(report['summary'], {'cases': 2, 'scored': 2, 'unmeasured': 0,
                                             'passed': 2, 'failed': 0})
        self.assertFalse(report['model_run_performed'])
        self.assertEqual(report['source_kind'], 'scripted_control')
        self.assertNotIn('prompt', json.dumps(report))
        self.assertNotIn('context', json.dumps(report))
        self.assertEqual(report['raw_input_sha256'], {
            'catalog': hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            'cases': hashlib.sha256(self.suite_path.read_bytes()).hexdigest(),
            'trace': hashlib.sha256(self.trace_path.read_bytes()).hexdigest()})
        sources = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                   for name in ('evaluate_skill_routing.py', 'skill_routing_contract.py',
                                'skill_routing_catalog.py')}
        self.assertEqual(report['evaluator_sha256'], digest(sources))
        self.assertEqual(report['report_sha256'],
                         digest({key: value for key, value in report.items()
                                 if key != 'report_sha256'}))

    def test_measured_failure_is_exit_one_and_missing_measurement_exit_two(self):
        self.trace['observations'][0]['invoked'] = []
        self.write_inputs()
        self.assertEqual(self.report(self.evaluate(), 1)['cases'][0]['status'], 'fail')
        self.trace['observations'].pop()
        self.write_inputs()
        report = self.report(self.evaluate(), 2)
        self.assertEqual(report['summary']['failed'], 1)
        self.assertEqual(report['summary']['unmeasured'], 1)

    def test_evidence_kinds_require_explicit_file_read_selection(self):
        self.trace['observation_kind'] = 'skill_file_read'
        self.write_inputs()
        self.assertEqual(self.report(self.evaluate(), 2)['summary']['unmeasured'], 2)
        self.assertEqual(self.report(self.evaluate('--observation-kind', 'skill_file_read'))
                         ['observation_kind'], 'skill_file_read')
        self.trace['observation_kind'] = 'self_report'
        self.write_inputs()
        report = self.report(self.evaluate(), 2)
        self.assertEqual(report['observation_kind'], 'self_report')
        self.assertEqual(report['summary']['scored'], 0)

    def test_output_is_utf8_under_legacy_pipe_encoding(self):
        environment = dict(os.environ, PYTHONIOENCODING='cp949', PYTHONUTF8='0')
        result = self.run_cli('evaluate', '--catalog', self.catalog_path, '--cases',
                              self.suite_path, '--trace', self.trace_path, environment=environment)
        self.assertEqual(self.report(result)['summary']['passed'], 2)

    def test_output_file_is_new_only_and_uses_same_json(self):
        output = self.directory / 'report.json'
        result = self.evaluate('--output', output)
        self.assertEqual(self.report(result), json.loads(output.read_text(encoding='utf-8')))
        original = output.read_bytes()
        rejected = self.evaluate('--output', output)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(output.read_bytes(), original)

    def test_output_rejects_windows_stream_aliases_before_creating_files(self):
        output = self.directory / 'report.json:stream'
        result = self.evaluate('--output', output)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.assertFalse((self.directory / 'report.json').exists())

    def test_malformed_and_missing_inputs_emit_only_fixed_error_codes(self):
        sentinel = 'PRIVATE_INPUT_SENTINEL'
        for raw in ('{"private":"' + sentinel + '","private":2}',
                    '{"x":NaN}', '[' * 40 + '0' + ']' * 40):
            with self.subTest(raw=raw[:12]):
                self.trace_path.write_text(raw, encoding='utf-8')
                result = self.evaluate()
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(sentinel.encode(), result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['kind'], 'skill_routing_error')
        self.trace_path.unlink()
        self.assertEqual(self.evaluate().returncode, 2)

    def test_argument_errors_never_echo_untrusted_arguments(self):
        sentinel = 'PRIVATE_ARGUMENT_SENTINEL'
        result = self.run_cli('evaluate', '--unknown', sentinel)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(sentinel.encode(), result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['kind'], 'skill_routing_error')

    def test_catalog_output_is_reusable_and_analysis_is_separate(self):
        root = self.directory / 'manifests'
        (root / 'alpha').mkdir(parents=True)
        (root / 'alpha' / 'SKILL.md').write_text(
            '---\nname: alpha\ndescription: "Inspect routing."\n---\nUnexecuted body.\n',
            encoding='utf-8')
        output, analysis = self.directory / 'built.json', self.directory / 'analysis.json'
        result = self.run_cli('catalog', '--root', 'test=' + str(root), '--output', output,
                              '--analysis-output', analysis)
        built = self.report(result)
        self.assertEqual(built['kind'], 'skill_routing_catalog')
        self.assertEqual(built['skills'][0]['id'], 'test:alpha')
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), built)
        self.assertTrue(analysis.is_file())
        self.catalog = built
        self.suite['cases'][0]['expect']['forbidden'] = []
        self.trace['catalog_sha256'] = digest(built)
        self.trace['suite_sha256'] = digest(self.suite)
        self.write_inputs()
        self.assertEqual(self.evaluation_report()['summary']['passed'], 2)

    def test_compatible_comparison_reports_improvement_and_regression(self):
        after = self.evaluation_report()
        self.trace['observations'][0]['invoked'] = []
        self.write_inputs()
        before = self.report(self.evaluate(), 1)
        report = self.report(self.compare(before, after))
        self.assertEqual(report['improved'], ['task'])
        self.assertEqual(report['regressed'], [])
        self.assertEqual(report['unchanged'], ['negative'])
        self.assertFalse(report['model_run_performed'])
        self.assertEqual(self.report(self.compare(after, before), 1)['regressed'], ['task'])

    def test_comparison_disclaims_regrading_and_observation_authentication(self):
        before = self.evaluation_report()
        supplied = copy.deepcopy(before)
        # Omitted expectations prevent independent regrading of this altered
        # caller-attested grade. Its fresh hash establishes no host-event proof.
        supplied['cases'][0]['observed'] = ['test:beta']
        self.rehash(supplied)
        comparison = self.report(self.compare(before, supplied))
        self.assertIn('regrades_expectations', comparison)
        self.assertIn('authenticates_observations', comparison)
        self.assertIs(comparison['regrades_expectations'], False)
        self.assertIs(comparison['authenticates_observations'], False)
        self.assertEqual(comparison['unchanged'], ['task', 'negative'])

    def test_missing_measurement_is_not_an_improvement(self):
        after = self.evaluation_report()
        self.trace['observations'].pop(0)
        self.write_inputs()
        before = self.report(self.evaluate(), 2)
        report = self.report(self.compare(before, after), 2)
        self.assertEqual(report['improved'], [])
        self.assertEqual(report['unmeasured'], ['task'])

    def test_description_changes_are_compatible_when_catalog_ids_match(self):
        before = self.evaluation_report()
        self.catalog['skills'][0]['description'] = 'Changed discoverability text.'
        self.catalog['skills'][0]['source_sha256'] = 'c' * 64
        self.trace['catalog_sha256'] = digest(self.catalog)
        self.write_inputs()
        after = self.evaluation_report()
        self.assertNotEqual(before['catalog_sha256'], after['catalog_sha256'])
        self.assertEqual(self.report(self.compare(before, after))['unchanged'], ['task', 'negative'])

    def test_incompatible_suite_runtime_source_evaluator_or_ids_is_rejected(self):
        original = self.evaluation_report()
        changes = [('suite_sha256', 'd' * 64), ('suite_id', 'another-suite'),
                   ('evaluator_sha256', 'e' * 64), ('source_kind', 'host_observation'),
                   ('observation_kind', 'skill_file_read'),
                   ('catalog_skill_ids', ['test:alpha']),
                   ('runtime', {'host': 'other-host', 'version': '1', 'model': 'none'})]
        for field, replacement in changes:
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = replacement
                self.rehash(changed)
                self.assertEqual(self.compare(original, changed).returncode, 2)

    def test_report_tampering_is_rejected_even_with_recomputed_outer_hash(self):
        original = self.evaluation_report()
        tampered = copy.deepcopy(original)
        tampered['summary']['passed'] = 99
        self.assertEqual(self.compare(original, tampered).returncode, 2)
        mutations = [lambda r: r['summary'].__setitem__('passed', 99),
                     lambda r: r['cases'][0].__setitem__('status', 'fail'),
                     lambda r: r['cases'][0].__setitem__('observed', 'test:alpha'),
                     lambda r: r['cases'][0].__setitem__('missing_required', ['test:alpha']),
                     lambda r: r['cases'][0].__setitem__('unexpected', ['test:alpha']),
                     lambda r: r['cases'][0].__setitem__('reason_codes', ['made_up']),
                     lambda r: r.__setitem__('model_run_performed', True),
                     lambda r: r.__setitem__('private', 'unrecognized'),
                     lambda r: r['summary'].__setitem__('cases', True),
                     lambda r: r['cases'].__setitem__(1, copy.deepcopy(r['cases'][0]))]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(original)
                mutation(changed)
                self.rehash(changed)
                self.assertEqual(self.compare(original, changed).returncode, 2)

    def test_compare_rejects_malformed_identifiers_even_when_reports_match(self):
        original = self.evaluation_report()
        changes = [lambda r: r.__setitem__('suite_id', 'invalid suite identifier'),
                   lambda r: r['cases'][0].__setitem__('id', 'invalid case identifier'),
                   lambda r: r['catalog_skill_ids'].__setitem__(1, 'test:' + 'b' * 65)]
        for change in changes:
            with self.subTest(change=change):
                invalid = copy.deepcopy(original)
                change(invalid)
                self.rehash(invalid)
                self.assertEqual(self.compare(invalid, invalid).returncode, 2)

    def test_valid_underscore_and_hyphen_skill_ids_remain_comparable(self):
        self.catalog['skills'][0]['id'] = '_test:-alpha'
        self.suite['cases'][0]['expect']['required'] = ['_test:-alpha']
        self.suite['cases'][0]['expect']['entrypoints'] = ['_test:-alpha']
        self.trace['observations'][0]['invoked'] = ['_test:-alpha']
        self.trace['suite_sha256'] = digest(self.suite)
        self.trace['catalog_sha256'] = digest(self.catalog)
        self.write_inputs()
        report = self.evaluation_report()
        self.assertEqual(self.report(self.compare(report, report))['unchanged'], ['task', 'negative'])

    def test_known_catalog_skill_can_still_be_an_unexpected_invocation(self):
        self.trace['observations'][1]['invoked'] = ['test:beta']
        self.write_inputs()
        report = self.report(self.evaluate(), 1)
        self.assertEqual(report['cases'][1]['unexpected'], ['test:beta'])
        self.assertEqual(self.report(self.compare(report, report))['unchanged'], ['task', 'negative'])

    def test_comparison_runtime_text_matches_the_contract(self):
        self.trace['runtime']['host'] = '\tCaller host\n'
        self.write_inputs()
        report = self.evaluation_report()
        self.assertEqual(self.report(self.compare(report, report))['unchanged'], ['task', 'negative'])
        invalid = copy.deepcopy(report)
        invalid['runtime']['host'] = '   '
        self.rehash(invalid)
        self.assertEqual(self.compare(invalid, invalid).returncode, 2)

    def test_comparison_accepts_large_valid_order_violation_lists(self):
        ids = ['test:s' + str(index) for index in range(47)]
        template = self.catalog['skills'][0]
        self.catalog['skills'] = [dict(template, id=ident) for ident in ids]
        self.suite['cases'] = [self.suite['cases'][0]]
        self.suite['cases'][0]['expect'] = {
            'required': ids, 'allowed': [], 'forbidden': [], 'entrypoints': [],
            'before': [[earlier, later] for index, earlier in enumerate(ids)
                       for later in ids[index + 1:]]}
        self.trace['observations'] = [{'case_id': 'task', 'status': 'complete',
                                       'invoked': list(reversed(ids))}]
        self.trace['suite_sha256'] = digest(self.suite)
        self.trace['catalog_sha256'] = digest(self.catalog)
        self.write_inputs()
        report = self.report(self.evaluate(), 1)
        self.assertEqual(len(report['cases'][0]['order_violations']), 1081)
        self.assertEqual(self.report(self.compare(report, report))['unchanged'], ['task'])

    def test_comparison_rejects_contradictory_skill_violation_categories(self):
        self.trace['observations'][1]['invoked'] = ['test:beta']
        self.write_inputs()
        original = self.report(self.evaluate(), 1)
        for category in ('forbidden', 'order_violations'):
            with self.subTest(category=category):
                invalid = copy.deepcopy(original)
                row = invalid['cases'][1]
                if category == 'forbidden':
                    row['forbidden'] = ['test:beta']
                    row['reason_codes'] = ['forbidden', 'unexpected']
                else:
                    row['order_violations'] = [['test:alpha', 'test:beta']]
                    row['reason_codes'] = ['unexpected', 'order_violation']
                self.rehash(invalid)
                self.assertEqual(self.compare(invalid, invalid).returncode, 2)


if __name__ == '__main__':
    unittest.main()
