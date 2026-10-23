from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import subprocess
import sys
import unittest
from unittest import mock

try:
    from tests import test_vault_runs as native
except ImportError:
    import test_vault_runs as native

try:
    costs = importlib.import_module('vault_run_costs')
except ModuleNotFoundError:
    costs = None


class RunCostsTests(unittest.TestCase):
    """Compose the native fixture without inheriting/re-running its test suite."""

    def setUp(self):
        self.assertIsNotNone(costs, 'source-bound run cost accounting is missing')
        self.base = native.VaultRunsTests(methodName='runTest')
        self.addCleanup(self.base.doCleanups)
        self.base.setUp()
        self.vault = self.base.vault

    def report(self, prepared, *, prefix='cost', value=10, scope=True):
        entries = []
        for phase in ('generation', 'evaluation', 'selection', 'recording'):
            path = 'output/' + prefix + '-' + phase + '.txt'
            self.base.write(path, 'Caller supplied resource observation.\n')
            entries.append(dict(id=phase, phase=phase, status='complete',
                observation='instrument_log', recorder='fixture-recorder', evidence=[path],
                metrics=dict(input_tokens=value, output_tokens=value * 2, calls=1,
                             elapsed_ms=value * 10, cost_microusd=value * 3), cost_basis='billed'))
        return dict(version=1, run_id=prepared['id'], scope_complete=scope, entries=entries)

    def record(self, prepared, report=None, *, prefix='cost', **kwargs):
        if report is None:
            report = self.report(prepared, prefix=prefix, **kwargs)
        path = 'input/' + prefix + '.json'
        self.base.write_json(path, report)
        return costs.record(self.vault, prepared['id'], path)

    def error(self, function, *args):
        with self.assertRaises(native.runs.RunError):
            function(*args)

    def rewrite_receipt(self, result, mutate):
        path = self.vault / result['path']
        value = json.loads(path.read_text(encoding='utf-8'))
        mutate(value)
        value['id'] = native.runs.evidence.digest(native.runs.evidence.encode(value['payload']))
        value['receipt_sha256'] = native.runs.evidence.digest(native.runs.evidence.encode(
            {key: item for key, item in value.items() if key != 'receipt_sha256'}))
        new_path = path.with_name('run-cost-' + value['id'] + '.json')
        new_path.write_bytes(native.runs.evidence.encode(value))
        return value['id']

    def test_prepared_run_costs_bind_sources_and_include_failed_and_discarded_attempts(self):
        prepared = self.base.prepare()
        report = self.report(prepared)
        report['entries'][0]['status'] = 'error'
        report['entries'][1]['status'] = 'discarded'
        report['entries'].reverse()
        result = self.record(prepared, report)
        self.assertEqual(native.runs.inspect(self.vault, prepared['id'])['status'], 'prepared')
        inspected = costs.inspect(self.vault, result['id'])
        self.assertEqual(inspected['run_id'], prepared['id'])
        self.assertEqual([e['id'] for e in inspected['entries']],
                         ['evaluation', 'generation', 'recording', 'selection'])
        self.assertEqual(inspected['totals'], dict(input_tokens=40, output_tokens=80,
                         calls=4, elapsed_ms=400, cost_microusd=120))
        self.assertEqual(inspected['missing_phases'], [])
        self.assertTrue(inspected['full_cost_available'])
        self.assertEqual(inspected['metering'], 'caller_described_not_authenticated')
        self.assertTrue((self.vault / result['path']).is_file())

    def test_unknown_is_not_zero_and_known_subtotals_remain_visible(self):
        prepared = self.base.prepare()
        report = self.report(prepared, value=0)
        report['entries'][0].update(observation='unknown', evidence=[], cost_basis='unknown',
                                   metrics={key: None for key in report['entries'][0]['metrics']})
        result = self.record(prepared, report)
        inspected = costs.inspect(self.vault, result['id'])
        self.assertIsNone(inspected['totals']['cost_microusd'])
        self.assertEqual(inspected['known_subtotals']['cost_microusd'], 0)
        self.assertEqual(inspected['unknown_counts']['calls'], 1)
        self.assertEqual(inspected['totals']['calls'], None)
        self.assertFalse(inspected['full_cost_available'])

    def test_missing_phases_and_incomplete_scope_are_explicit_in_summary(self):
        prepared = self.base.prepare()
        report = self.report(prepared, scope=False)
        report['entries'] = report['entries'][:1]
        result = self.record(prepared, report)
        summary = costs.summarize(self.vault, [result['id']])
        self.assertFalse(summary['scope_complete'])
        self.assertFalse(summary['full_cost_available'])
        self.assertEqual(summary['scopes'][0]['missing_phases'], ['evaluation', 'recording', 'selection'])
        self.assertEqual(summary['totals']['calls'], 1)

    def test_compare_combines_native_performance_and_reported_deltas(self):
        baseline, candidate = self.base.pair()
        before = self.record(baseline, prefix='before', value=10)
        after = self.record(candidate, prefix='after', value=8)
        result = costs.compare(self.vault, before['id'], after['id'])
        self.assertTrue(result['performance']['eligible'])
        self.assertEqual(result['performance']['score_delta'], 0.5)
        self.assertTrue(result['cost_comparison_available'])
        self.assertEqual(result['reported_deltas'], dict(input_tokens=-8, output_tokens=-16,
                         calls=0, elapsed_ms=-80, cost_microusd=-24))
        self.assertEqual(result['cost_basis'], 'billed')

    def test_comparison_requires_native_completion_and_preserves_hard_veto(self):
        baseline = self.base.prepare('notes/baseline.md')
        candidate = self.base.prepare()
        before = self.record(baseline, prefix='before')
        after = self.record(candidate, prefix='after')
        self.error(costs.compare, self.vault, before['id'], after['id'])
        self.base.complete(baseline, name='baseline-report')
        self.base.complete(candidate, hard=False, name='candidate-report')
        result = costs.compare(self.vault, before['id'], after['id'])
        self.assertFalse(result['performance']['eligible'])
        self.assertIn('hard_boundary_failure', result['performance']['reasons'])

    def test_scope_unknown_and_mixed_or_unequal_bases_prevent_cost_comparison(self):
        baseline, candidate = self.base.pair()
        before = self.record(baseline, prefix='before')
        for case in ('scope', 'missing', 'unknown', 'partial', 'mixed', 'unequal'):
            with self.subTest(case=case):
                report = self.report(candidate, prefix=case)
                first = report['entries'][0]
                if case == 'scope':
                    report['scope_complete'] = False
                elif case == 'missing':
                    report['entries'].pop()
                elif case == 'unknown':
                    first.update(observation='unknown', evidence=[], cost_basis='unknown',
                                 metrics={key: None for key in first['metrics']})
                elif case == 'partial':
                    first['metrics']['calls'] = None
                elif case == 'mixed':
                    first['cost_basis'] = 'estimated'
                else:
                    for entry in report['entries']:
                        entry['cost_basis'] = 'estimated'
                after = self.record(candidate, report, prefix=case)
                result = costs.compare(self.vault, before['id'], after['id'])
                self.assertFalse(result['cost_comparison_available'])
                self.assertTrue(result['cost_reasons'])
                self.assertTrue(all(value is None for value in result['reported_deltas'].values()))

    def test_all_estimated_entries_compare_on_estimated_basis(self):
        baseline, candidate = self.base.pair()
        receipts = []
        for prefix, run in (('before', baseline), ('after', candidate)):
            report = self.report(run, prefix=prefix)
            for entry in report['entries']:
                entry['cost_basis'] = 'estimated'
            receipts.append(self.record(run, report, prefix=prefix))
        result = costs.compare(self.vault, *(item['id'] for item in receipts))
        self.assertTrue(result['cost_comparison_available'])
        self.assertEqual(result['cost_basis'], 'estimated')

    def test_strict_schema_rejects_malformed_values_and_duplicate_entries_or_evidence(self):
        prepared = self.base.prepare()
        original = self.report(prepared)
        changes = [lambda r: r.update(extra=True), lambda r: r.update(version=True),
            lambda r: r.update(scope_complete=1), lambda r: r.update(run_id='0' * 64),
            lambda r: r.update(entries=[]), lambda r: r['entries'][0].update(extra=0),
            lambda r: r['entries'][0].update(id='unsafe label'),
            lambda r: r['entries'][0].update(phase='other'),
            lambda r: r['entries'][0].update(status='other'),
            lambda r: r['entries'][0].update(observation='other'),
            lambda r: r['entries'][0].update(recorder='unsafe label'),
            lambda r: r['entries'][0].update(cost_basis='other'),
            lambda r: r['entries'][0].update(evidence=[]),
            lambda r: r['entries'][0].update(observation='unknown'),
            lambda r: r['entries'][0]['metrics'].update(calls=True),
            lambda r: r['entries'][0]['metrics'].update(calls=1.0),
            lambda r: r['entries'][0]['metrics'].update(calls=-1),
            lambda r: r['entries'][0]['metrics'].update(calls=10**15 + 1),
            lambda r: r['entries'][0]['metrics'].update(extra=0),
            lambda r: r['entries'][0]['metrics'].pop('calls'),
            lambda r: r['entries'][0].update(cost_basis='unknown'),
            lambda r: r['entries'][0]['metrics'].update(cost_microusd=None),
            lambda r: r['entries'].append(copy.deepcopy(r['entries'][0])),
            lambda r: r['entries'][0]['evidence'].append(r['entries'][0]['evidence'][0]),
            lambda r: r['entries'][1].update(evidence=r['entries'][0]['evidence'])]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                report = copy.deepcopy(original)
                change(report)
                self.base.write_json('input/bad-cost.json', report)
                self.error(costs.record, self.vault, prepared['id'], 'input/bad-cost.json')

    def test_known_observation_can_have_unknown_cost_without_conflating_zero(self):
        prepared = self.base.prepare()
        report = self.report(prepared, value=0)
        report['entries'][0]['metrics']['cost_microusd'] = None
        report['entries'][0]['cost_basis'] = 'unknown'
        inspected = costs.inspect(self.vault, self.record(prepared, report)['id'])
        self.assertEqual(inspected['totals']['input_tokens'], 0)
        self.assertIsNone(inspected['totals']['cost_microusd'])
        self.assertEqual(inspected['unknown_counts']['cost_microusd'], 1)

    def test_self_native_input_reserved_and_unsafe_evidence_are_rejected(self):
        prepared = self.base.prepare()
        for path in ('input/cost.json', 'notes/candidate.md', 'notes/prompt.md',
                     'input/fixture.json', 'input/tools.json', '../escape.txt', '/absolute.txt',
                     'private/log.txt', 'excluded/log.txt', 'output/.env',
                     native.runs.RUNTIME_DIR + '/made-up.json', '00-meta/proposals/log.txt'):
            with self.subTest(path=path):
                report = self.report(prepared)
                report['entries'][0]['evidence'] = [path]
                self.base.write_json('input/cost.json', report)
                self.error(costs.record, self.vault, prepared['id'], 'input/cost.json')
        self.error(costs.record, self.vault, prepared['id'], 'input/fixture.json')

    def test_secret_report_metadata_and_evidence_are_rejected_without_persisting(self):
        prepared = self.base.prepare()
        report = self.report(prepared)
        secret = 'sk-' + 'x' * 48
        report['entries'][0]['recorder'] = secret
        self.base.write_json('input/secret.json', report)
        self.error(costs.record, self.vault, prepared['id'], 'input/secret.json')
        report = self.report(prepared)
        self.base.write(report['entries'][0]['evidence'][0], 'api_key=' + secret)
        self.base.write_json('input/secret.json', report)
        self.error(costs.record, self.vault, prepared['id'], 'input/secret.json')
        self.assertEqual(list((self.vault / native.runs.RUNTIME_DIR).glob('run-cost-*')), [])

    def test_oversized_entry_report_and_evidence_sets_are_rejected(self):
        prepared = self.base.prepare()
        report = self.report(prepared)
        report['entries'] = [dict(copy.deepcopy(report['entries'][0]), id='entry-' + str(i))
                             for i in range(65)]
        self.base.write_json('input/large.json', report)
        self.error(costs.record, self.vault, prepared['id'], 'input/large.json')
        self.base.write('input/large.json', ' ' * (64 * 1024 + 1))
        self.error(costs.record, self.vault, prepared['id'], 'input/large.json')
        report = self.report(prepared)
        self.base.write(report['entries'][0]['evidence'][0], 'x' * (1024 * 1024 + 1))
        self.base.write_json('input/large.json', report)
        self.error(costs.record, self.vault, prepared['id'], 'input/large.json')
        report = self.report(prepared)
        for entry in report['entries']:
            self.base.write(entry['evidence'][0], 'x' * (600 * 1024))
        self.base.write_json('input/large.json', report)
        self.error(costs.record, self.vault, prepared['id'], 'input/large.json')

    def test_stale_report_evidence_and_native_inputs_fail_currentness(self):
        prepared = self.base.prepare()
        result = self.record(prepared)
        for relative in ('input/cost.json', 'output/cost-generation.txt', 'notes/candidate.md',
                         'notes/prompt.md', 'input/fixture.json', 'input/tools.json'):
            with self.subTest(relative=relative):
                path = self.vault / relative
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                self.error(costs.inspect, self.vault, result['id'])
                path.write_bytes(original)

    def test_recomputed_checksums_and_ids_cannot_sever_report_normalization(self):
        prepared = self.base.prepare()
        result = self.record(prepared)
        tampered_id = self.rewrite_receipt(result,
            lambda value: value['payload']['entries'][0]['metrics'].update(calls=999))
        self.error(costs.inspect, self.vault, tampered_id)
        tampered_id = self.rewrite_receipt(result,
            lambda value: value['payload'].update(scope_complete=False))
        self.error(costs.inspect, self.vault, tampered_id)

    def test_report_reserialization_does_not_authorize_tampered_normalized_data(self):
        prepared = self.base.prepare()
        result = self.record(prepared)
        report_path = self.vault / 'input/cost.json'
        report_path.write_text(json.dumps(json.loads(report_path.read_text()), indent=2), encoding='utf-8')
        def mutate(value):
            value['payload']['report_source'] = native.runs.Store(self.vault).fingerprint('input/cost.json')[0]
            value['payload']['entries'][0]['metrics']['calls'] = 999
        tampered_id = self.rewrite_receipt(result, mutate)
        self.error(costs.inspect, self.vault, tampered_id)

    def test_receipt_numeric_type_tampering_cannot_hide_behind_python_equality(self):
        prepared = self.base.prepare()
        result = self.record(prepared)
        changes = [lambda value: value['payload']['entries'][0]['metrics'].update(calls=True),
                   lambda value: value['payload']['entries'][0]['metrics'].update(elapsed_ms=100.0),
                   lambda value: value['payload'].update(scope_complete=1),
                   lambda value: value['payload']['evidence'][0].update(
                       size=float(value['payload']['evidence'][0]['size']))]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                tampered_id = self.rewrite_receipt(result, change)
                self.error(costs.inspect, self.vault, tampered_id)

    def test_publication_rechecks_report_evidence_and_native_inputs(self):
        prepared = self.base.prepare()
        report = self.report(prepared)
        self.base.write_json('input/cost.json', report)
        publish = native.runs.Store.publish
        for relative in ('input/cost.json', 'output/cost-generation.txt', 'notes/prompt.md'):
            with self.subTest(relative=relative):
                path = self.vault / relative
                original = path.read_bytes()
                def racing_publish(store, *args, **kwargs):
                    path.write_bytes(original + b' ')
                    return publish(store, *args, **kwargs)
                with mock.patch.object(native.runs.Store, 'publish', racing_publish):
                    self.error(costs.record, self.vault, prepared['id'], 'input/cost.json')
                path.write_bytes(original)
        self.assertEqual(list((self.vault / native.runs.RUNTIME_DIR).glob('run-cost-*')), [])

    def test_cost_receipt_is_immutable_and_binding_to_prepared_receipt_is_exact(self):
        prepared = self.base.prepare()
        result = self.record(prepared)
        before = (self.vault / result['path']).read_bytes()
        self.error(costs.record, self.vault, prepared['id'], 'input/cost.json')
        self.assertEqual((self.vault / result['path']).read_bytes(), before)
        tampered_id = self.rewrite_receipt(result,
            lambda value: value['payload'].update(prepared_receipt_sha256='0' * 64))
        self.error(costs.inspect, self.vault, tampered_id)

    def test_summary_rejects_duplicates_runs_shared_evidence_and_unselected_ids(self):
        baseline, candidate = self.base.pair()
        before = self.record(baseline, prefix='before')
        duplicate_run = self.record(baseline, prefix='another')
        report = self.report(candidate, prefix='after')
        report['entries'][0]['evidence'] = ['output/before-generation.txt']
        shared = self.record(candidate, report, prefix='after')
        for ids in ([], [before['id']] * 2, [before['id'], duplicate_run['id']],
                    [before['id'], shared['id']], [before['id']] * 65, ['0' * 64]):
            with self.subTest(ids=ids):
                self.error(costs.summarize, self.vault, ids)

    def test_summary_aggregates_only_explicit_unique_runs_and_is_read_only(self):
        baseline, candidate = self.base.pair()
        before = self.record(baseline, prefix='before', value=10)
        after = self.record(candidate, prefix='after', value=0)
        runtime = self.vault / native.runs.RUNTIME_DIR
        original = {path.name: path.read_bytes() for path in runtime.glob('*.json')}
        result = costs.summarize(self.vault, [after['id'], before['id']])
        self.assertEqual(result['totals']['cost_microusd'], 120)
        self.assertEqual(result['totals']['calls'], 8)
        self.assertEqual(result['run_count'], 2)
        self.assertTrue(result['full_cost_available'])
        costs.inspect(self.vault, before['id'])
        self.assertEqual({path.name: path.read_bytes() for path in runtime.glob('*.json')}, original)

    def test_cli_all_verbs_and_content_free_invalid_arguments(self):
        baseline, candidate = self.base.pair()
        receipts = []
        for prefix, run in (('before', baseline), ('after', candidate)):
            self.base.write_json('input/' + prefix + '.json', self.report(run, prefix=prefix))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = costs.main(['--vault', str(self.vault), 'record', run['id'],
                                     '--report', 'input/' + prefix + '.json'])
            self.assertEqual(status, 0)
            receipts.append(json.loads(stream.getvalue())['id'])
        for tail in (['inspect', receipts[0]], ['summarize', *receipts], ['compare', *receipts]):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(costs.main(['--vault', str(self.vault), *tail]), 0)
            self.assertTrue(json.loads(stream.getvalue())['ok'])
        secret = 'sk-' + 'z' * 48
        child = subprocess.run([sys.executable, '-X', 'utf8', str(native.SCRIPTS / 'vault_run_costs.py'),
            '--vault', str(self.vault), 'inspect', '--' + secret], capture_output=True,
            text=True, encoding='utf-8', timeout=15)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(json.loads(child.stdout), dict(ok=False, error='invalid_cli_arguments'))
        self.assertNotIn(secret, child.stdout + child.stderr)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(costs.main(['--vault', str(self.vault), 'inspect', secret]), 2)
        self.assertNotIn(secret, stream.getvalue())


if __name__ == '__main__':
    unittest.main()
