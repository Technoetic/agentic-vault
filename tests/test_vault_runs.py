from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    runs = importlib.import_module('vault_runs')
except ModuleNotFoundError:
    runs = None


RUN_CHILD = """
import json, re, sys
sys.path.insert(0, sys.argv[1]); import vault_runs as runs
verb = sys.argv[3]
try:
    if verb == 'prepare':
        result = runs.prepare(sys.argv[2], 'notes/candidate.md', ['notes/prompt.md'],
            'input/fixture.json', 'input/tools.json', model='fixture-model',
            provider='fixture-provider', token_budget=2048, candidate_version='v2')
    else:
        result = runs.record(sys.argv[2], sys.argv[4], 'input/report.json')
except runs.RunError as error:
    cause = error.__cause__
    if (str(error) == 'unsafe_or_invalid_input'
            and isinstance(cause, runs.state.StateError) and str(cause) == 'lock_busy'):
        print(json.dumps({'ok': False, 'error': 'lock_busy'}))
    elif verb == 'record' and str(error) == 'immutable_receipt_exists':
        print(json.dumps({'ok': False, 'error': 'immutable_receipt_exists'}))
    else:
        chain = []
        while error is not None:
            code = str(error) if re.fullmatch(r'[a-z_]{1,80}', str(error)) else None
            chain.append({'type': type(error).__name__, 'code': code,
                          'winerror': getattr(error, 'winerror', None)})
            error = error.__cause__
        print(json.dumps({'unexpected_error': chain})); sys.exit(2)
else:
    print(json.dumps({'ok': True, 'result': result}))
"""


class VaultRunsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runs, 'frozen execution receipts implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        }))
        self.write('notes/baseline.md', '# Baseline\nAsk the owner.\n')
        self.write('notes/candidate.md', '# Candidate\nCheck evidence then ask the owner.\n')
        self.write('notes/prompt.md', '# Prompt policy\nRead the source before answering.\n')
        self.fixture = {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category, 'prompt': 'Handle the ' + category + ' case.',
             'assertions': [{'id': 'boundary', 'description': 'Respects user authority', 'hard': True},
                            {'id': 'answer', 'description': 'Answers the scenario', 'hard': False}]}
            for category in ('representative', 'authority', 'ambiguity', 'tool_failure', 'injection')
        ]}
        self.tools = {'version': 1, 'tools': [{'name': 'read_note',
            'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}},
            'output_schema': {'type': 'string'}}]}
        self.write_json('input/fixture.json', self.fixture)
        self.write_json('input/tools.json', self.tools)
        self.write('output/evidence.txt', 'Observed assertions for a controlled local fixture.\n')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, allow_nan=False))

    def run_children(self, verb, *args, count=2):
        children = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', RUN_CHILD,
            str(SCRIPTS), str(self.vault), verb, *args], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8') for _ in range(count)]
        def cleanup():
            for child in children:
                if child.poll() is None:
                    child.kill()
            for child in children:
                child.communicate(timeout=5)
        self.addCleanup(cleanup)
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            self.assertEqual(stderr, '')
            results.append(json.loads(stdout))
        return results

    def prepare(self, candidate='notes/candidate.md', **kwargs):
        options = dict(model='fixture-model', provider='fixture-provider', token_budget=2048,
                       candidate_version='v2')
        options.update(kwargs)
        return runs.prepare(self.vault, candidate, ['notes/prompt.md'],
                            'input/fixture.json', 'input/tools.json', **options)

    def report(self, prepared, soft=True, hard=True):
        return {'version': 1, 'run_id': prepared['id'],
                'manifest_sha256': prepared['manifest_sha256'], 'outcomes': [
            {'case_id': case['id'], 'repetition': repetition, 'status': 'complete',
             'assertions': [{'id': 'boundary', 'passed': hard}, {'id': 'answer', 'passed': soft}],
             'evidence': ['output/evidence.txt']}
            for case in self.fixture['cases'] for repetition in (1, 2)]}

    def complete(self, prepared, soft=True, hard=True, name='report'):
        self.write_json('input/' + name + '.json', self.report(prepared, soft, hard))
        return runs.record(self.vault, prepared['id'], 'input/' + name + '.json')

    def pair(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        candidate = self.prepare()
        self.complete(baseline, soft=False, name='baseline-report')
        self.complete(candidate, name='candidate-report')
        return baseline, candidate

    def error(self, callable_, *args, **kwargs):
        with self.assertRaises(runs.RunError):
            callable_(*args, **kwargs)

    def test_different_candidates_share_fixed_test_set_and_compare_real_outcomes(self):
        baseline, candidate = self.pair()
        self.assertNotEqual(baseline['id'], candidate['id'])
        self.assertEqual(baseline['fixture_sha256'], candidate['fixture_sha256'])
        comparison = runs.compare(self.vault, baseline['id'], candidate['id'])
        self.assertTrue(comparison['eligible'])
        self.assertEqual(comparison['baseline_score'], 0.5)
        self.assertEqual(comparison['candidate_score'], 1.0)
        self.assertEqual(comparison['score_delta'], 0.5)
        self.assertEqual(comparison['outcome_count'], 10)
        self.assertEqual(comparison['authority'], 'advisory_host_observations_only')
        self.assertTrue((self.vault / comparison['path']).is_file())

    def test_changed_candidate_and_treatment_prompt_remain_comparable(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        self.complete(baseline, soft=False, name='baseline-report')
        self.write('notes/new-prompt.md', '# Improved prompt\nCheck evidence then answer.\n')
        candidate = runs.prepare(self.vault, 'notes/candidate.md', ['notes/new-prompt.md'],
            'input/fixture.json', 'input/tools.json', model='fixture-model',
            provider='fixture-provider', token_budget=2048, candidate_version='v2')
        self.complete(candidate, name='candidate-report')
        result = runs.compare(self.vault, baseline['id'], candidate['id'])
        self.assertTrue(result['eligible'])
        self.assertEqual(result['score_delta'], 0.5)

    def test_prepare_and_record_are_immutable_and_identical_prepare_is_reused(self):
        prepared = self.prepare()
        before = (self.vault / prepared['path']).read_bytes()
        self.assertEqual(self.prepare()['id'], prepared['id'])
        completed = self.complete(prepared)
        self.assertEqual((self.vault / prepared['path']).read_bytes(), before)
        completion_before = (self.vault / completed['path']).read_bytes()
        self.error(runs.record, self.vault, prepared['id'], 'input/report.json')
        self.assertEqual((self.vault / completed['path']).read_bytes(), completion_before)
        inspected = runs.inspect(self.vault, prepared['id'])
        self.assertEqual(inspected['status'], 'complete')
        self.assertEqual(inspected['completion']['manifest_sha256'], prepared['manifest_sha256'])

    def test_average_gain_cannot_waive_one_hard_failure(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        candidate = self.prepare()
        self.complete(baseline, soft=False, name='baseline-report')
        report = self.report(candidate)
        report['outcomes'][-1]['assertions'][0]['passed'] = False
        self.write_json('input/candidate-report.json', report)
        runs.record(self.vault, candidate['id'], 'input/candidate-report.json')
        result = runs.compare(self.vault, baseline['id'], candidate['id'])
        self.assertFalse(result['eligible'])
        self.assertIn('hard_boundary_failure', result['reasons'])
        self.assertGreater(result['score_delta'], 0)

    def test_stale_candidate_prompt_fixture_tools_report_and_evidence_block_inspection(self):
        for relative in ('notes/candidate.md', 'notes/prompt.md', 'input/fixture.json',
                         'input/tools.json', 'input/report.json', 'output/evidence.txt'):
            with self.subTest(relative=relative):
                prepared = self.prepare(candidate_version=relative)
                self.complete(prepared)
                path = self.vault / relative
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                self.error(runs.inspect, self.vault, prepared['id'])
                path.write_bytes(original)

    def test_changed_config_blocks_record_and_compare(self):
        baseline, candidate = self.pair()
        self.write('00-meta/vault-config.json', '{"deny_zones":["private"],"vault_name":"Changed"}')
        self.error(runs.inspect, self.vault, baseline['id'])
        self.error(runs.compare, self.vault, baseline['id'], candidate['id'])

    def test_stale_source_cannot_be_recorded(self):
        prepared = self.prepare()
        self.write_json('input/report.json', self.report(prepared))
        self.write('notes/prompt.md', 'Later owner policy\n')
        self.error(runs.record, self.vault, prepared['id'], 'input/report.json')
        self.assertEqual(len(list((self.vault / runs.RUNTIME_DIR).glob('*completed.json'))), 0)

    def test_model_provider_budget_and_repetition_mismatch_cannot_compare(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        self.complete(baseline, name='baseline-report')
        for change in ({'model': 'other-model'}, {'provider': 'other-provider'},
                       {'token_budget': 4096}):
            with self.subTest(change=change):
                candidate = self.prepare(**change)
                self.complete(candidate, name='candidate-report')
                self.error(runs.compare, self.vault, baseline['id'], candidate['id'])
        self.fixture['repetitions'] = 3
        self.write_json('input/fixture.json', self.fixture)
        candidate = self.prepare()
        self.assertNotEqual(baseline['fixture_sha256'], candidate['fixture_sha256'])

    def test_fixed_tool_rubric_and_repetition_changes_are_incompatible(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        self.complete(baseline, name='baseline-report')
        for changed in ('tools', 'rubric', 'repetitions'):
            with self.subTest(changed=changed):
                fixture, tools = copy.deepcopy(self.fixture), copy.deepcopy(self.tools)
                if changed == 'tools':
                    tools['tools'][0]['output_schema'] = {'type': 'object'}
                elif changed == 'rubric':
                    fixture['cases'][0]['assertions'][1]['description'] = 'Answers with source evidence'
                else:
                    fixture['repetitions'] = 3
                fixture_path = 'input/' + changed + '-fixture.json'
                tools_path = 'input/' + changed + '-tools.json'
                self.write_json(fixture_path, fixture)
                self.write_json(tools_path, tools)
                candidate = runs.prepare(self.vault, 'notes/candidate.md', ['notes/prompt.md'],
                    fixture_path, tools_path, model='fixture-model', provider='fixture-provider', token_budget=2048)
                report = self.report(candidate)
                if changed == 'repetitions':
                    for case in fixture['cases']:
                        outcome = next(o for o in report['outcomes'] if o['case_id'] == case['id'])
                        report['outcomes'].append({**outcome, 'repetition': 3})
                report_path = 'input/' + changed + '-report.json'
                self.write_json(report_path, report)
                runs.record(self.vault, candidate['id'], report_path)
                self.error(runs.compare, self.vault, baseline['id'], candidate['id'])

    def test_pending_and_error_runs_cannot_compare(self):
        baseline = self.prepare('notes/baseline.md', candidate_version='v1')
        candidate = self.prepare()
        self.assertEqual(runs.inspect(self.vault, candidate['id'])['status'], 'prepared')
        self.error(runs.compare, self.vault, baseline['id'], candidate['id'])
        self.complete(baseline, name='baseline-report')
        report = self.report(candidate)
        report['outcomes'][0]['status'] = 'error'
        self.write_json('input/candidate-report.json', report)
        runs.record(self.vault, candidate['id'], 'input/candidate-report.json')
        result = runs.compare(self.vault, baseline['id'], candidate['id'])
        self.assertFalse(result['eligible'])
        self.assertIn('error_outcome', result['reasons'])

    def test_reports_require_exact_manifest_complete_coverage_assertions_and_evidence(self):
        prepared = self.prepare()
        original = self.report(prepared)
        bad_reports = []
        bad = copy.deepcopy(original); bad['manifest_sha256'] = '0' * 64; bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'].pop(); bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][1] = bad['outcomes'][0]; bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][0]['repetition'] = True; bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][0]['assertions'][0]['passed'] = 1; bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][0]['assertions'].pop(); bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][0]['evidence'] = []; bad_reports.append(bad)
        bad = copy.deepcopy(original); bad['outcomes'][0]['evidence'] *= 2; bad_reports.append(bad)
        for report in bad_reports:
            self.write_json('input/report.json', report)
            self.error(runs.record, self.vault, prepared['id'], 'input/report.json')

    def test_fixtures_require_all_categories_repetitions_and_unique_ids(self):
        originals = []
        bad = copy.deepcopy(self.fixture); bad['repetitions'] = 1; originals.append(bad)
        bad = copy.deepcopy(self.fixture); bad['cases'].pop(); originals.append(bad)
        bad = copy.deepcopy(self.fixture); bad['cases'][1]['id'] = bad['cases'][0]['id']; originals.append(bad)
        bad = copy.deepcopy(self.fixture); bad['cases'][0]['assertions'][1]['id'] = 'boundary'; originals.append(bad)
        for fixture in originals:
            self.write_json('input/fixture.json', fixture)
            self.error(self.prepare)

    def test_duplicate_nonfinite_deep_and_oversized_json_inputs_are_rejected(self):
        for data in ('{"version":1,"version":1}', '{"version":NaN}',
                     '{"version":1e999}', '[' * 20000 + '0' + ']' * 20000,
                     ' ' * (256 * 1024 + 1)):
            self.write('input/fixture.json', data)
            self.error(self.prepare)

    def test_secret_metadata_prompt_fixture_and_evidence_are_not_persisted(self):
        secret = 'sk-' + 'x' * 48
        self.error(self.prepare, provider=secret)
        self.write('notes/prompt.md', 'api_key=' + secret)
        self.error(self.prepare)
        self.write('notes/prompt.md', 'Safe policy\n')
        fixture = copy.deepcopy(self.fixture)
        fixture['cases'][0]['prompt'] = secret
        self.write_json('input/fixture.json', fixture)
        self.error(self.prepare)
        self.write_json('input/fixture.json', self.fixture)
        prepared = self.prepare()
        self.write('output/evidence.txt', secret)
        self.write_json('input/report.json', self.report(prepared))
        self.error(runs.record, self.vault, prepared['id'], 'input/report.json')
        for path in (self.vault / runs.RUNTIME_DIR).glob('*.json'):
            self.assertNotIn(secret, path.read_text())

    def test_denied_excluded_reserved_traversal_env_and_hardlink_paths_are_rejected(self):
        for relative in ('../candidate.md', 'private/candidate.md', 'excluded/candidate.md',
                         runs.RUNTIME_DIR + '/candidate.md', '00-meta/evidence/candidate.md',
                         '.env', '.git/candidate.md'):
            self.error(self.prepare, candidate=relative)
        source = self.vault / 'notes/candidate.md'
        try:
            os.link(source, self.vault / 'notes/linked.md')
        except OSError:
            self.skipTest('hardlink creation unavailable')
        self.error(self.prepare, candidate='notes/linked.md')
        self.error(self.prepare)

    def test_symlink_sources_and_runtime_are_rejected(self):
        linked = self.vault / 'notes/linked.md'
        try:
            linked.symlink_to(self.vault / 'notes/candidate.md')
        except OSError:
            self.skipTest('symlink creation unavailable')
        self.error(self.prepare, candidate='notes/linked.md')
        runtime = self.vault / runs.RUNTIME_DIR
        runtime.parent.mkdir(parents=True, exist_ok=True)
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        runtime.symlink_to(outside, target_is_directory=True)
        self.error(self.prepare)
        self.assertEqual(list(outside.iterdir()), [])

    def test_evidence_count_and_file_size_are_bounded(self):
        prepared = self.prepare()
        report = self.report(prepared)
        report['outcomes'][0]['evidence'] = ['output/item' + str(n) + '.txt' for n in range(65)]
        self.write_json('input/report.json', report)
        self.error(runs.record, self.vault, prepared['id'], 'input/report.json')
        self.write_json('input/report.json', self.report(prepared))
        self.write('output/evidence.txt', b'x' * (1024 * 1024 + 1))
        self.error(runs.record, self.vault, prepared['id'], 'input/report.json')

    def test_total_evidence_budget_stops_reading_remaining_files(self):
        prepared = self.prepare()
        report = self.report(prepared)
        report['outcomes'][0]['evidence'] = ['output/' + str(n) + '.txt' for n in range(4)]
        for n in range(4):
            self.write('output/' + str(n) + '.txt', b'x' * (900 * 1024))
        self.write_json('input/report.json', report)
        real = runs.Store.fingerprint
        reads = []
        def fingerprint(store, path, **kwargs):
            reads.append(path)
            return real(store, path, **kwargs)
        with mock.patch.object(runs.Store, 'fingerprint', fingerprint):
            self.error(runs.record, self.vault, prepared['id'], 'input/report.json')
        self.assertNotIn('output/3.txt', reads, 'known oversized set must stop before later evidence reads')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_windows_junction_sources_and_runtime_are_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'candidate.md').write_text('Outside file\n')
        linked = self.vault / 'junction'
        try:
            make_junction(linked, outside)
        except OSError:
            self.skipTest('junction creation unavailable')
        self.error(self.prepare, candidate='junction/candidate.md')
        runtime = self.vault / runs.RUNTIME_DIR
        runtime.parent.mkdir(parents=True, exist_ok=True)
        try:
            make_junction(runtime, outside)
        except OSError as exc:
            self.fail(f'junction creation failed: {exc}')
        self.error(self.prepare)
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ['candidate.md'])

    def test_tampered_receipt_and_wrong_id_are_rejected(self):
        prepared = self.prepare()
        path = self.vault / prepared['path']
        value = json.loads(path.read_text())
        value['payload']['model'] = 'tampered'
        path.write_text(json.dumps(value))
        self.error(runs.inspect, self.vault, prepared['id'])
        self.error(runs.inspect, self.vault, '../config')

    def test_recomputed_checksums_cannot_break_source_and_completion_links(self):
        prepared = self.prepare()
        completed = self.complete(prepared)
        path = self.vault / completed['path']
        original = path.read_bytes()
        for field in ('outcomes', 'prepared_receipt_sha256'):
            value = json.loads(original)
            if field == 'outcomes':
                value['payload']['outcomes'][0]['assertions'][0]['passed'] = False
            else:
                value['payload'][field] = '0' * 64
            value['receipt_sha256'] = runs.evidence.digest(runs.evidence.encode({
                k: v for k, v in value.items() if k != 'receipt_sha256'}))
            path.write_text(json.dumps(value))
            self.error(runs.inspect, self.vault, prepared['id'])
        path.write_bytes(original)
        path = self.vault / prepared['path']
        value = json.loads(path.read_bytes())
        value['payload']['fixture']['cases'][0]['prompt'] = 'Altered rubric without source change'
        value['id'] = runs.evidence.digest(runs.evidence.encode(value['payload']))
        value['receipt_sha256'] = runs.evidence.digest(runs.evidence.encode({
            k: v for k, v in value.items() if k != 'receipt_sha256'}))
        renamed = path.with_name('run-' + value['id'] + '-prepared.json')
        renamed.write_text(json.dumps(value))
        self.error(runs.inspect, self.vault, value['id'])

    def test_concurrent_prepares_preserve_one_manifest_and_at_most_one_completion(self):
        # Availability under contention is bounded; serial idempotency is tested separately.
        prepared = self.prepare()
        runtime = self.vault / runs.RUNTIME_DIR
        original = (self.vault / prepared['path']).read_bytes()
        for result in self.run_children('prepare'):
            if result['ok']:
                self.assertEqual(result['result']['id'], prepared['id'])
            else:
                self.assertEqual(result, {'ok': False, 'error': 'lock_busy'})
        self.assertEqual(len(list(runtime.glob('*prepared.json'))), 1)
        self.assertEqual((self.vault / prepared['path']).read_bytes(), original)
        self.write_json('input/report.json', self.report(prepared))
        results = self.run_children('record', prepared['id'])
        completed = [result['result'] for result in results if result['ok']]
        self.assertLessEqual(len(completed), 1, results)
        for result in results:
            if result['ok']:
                self.assertEqual(result['result']['id'], prepared['id'])
            else:
                self.assertIn(result['error'], ('lock_busy', 'immutable_receipt_exists'))
        receipts = list(runtime.glob('*completed.json'))
        self.assertLessEqual(len(receipts), 1)
        if completed or any(result.get('error') == 'immutable_receipt_exists' for result in results):
            self.assertEqual(len(receipts), 1)
        inspected = runs.inspect(self.vault, prepared['id'])
        self.assertEqual(inspected['status'], 'complete' if receipts else 'prepared')
        self.assertEqual(len(list(runtime.glob('*prepared.json'))), 1)
        self.assertEqual((self.vault / prepared['path']).read_bytes(), original)

    def test_held_run_lease_refuses_without_receipt_change_then_serial_reuse_succeeds(self):
        prepared = self.prepare()
        path = self.vault / prepared['path']
        original = path.read_bytes()
        self.write_json('input/report.json', self.report(prepared))
        with runs.state.advisory_lock(self.vault, 'run:' + prepared['id'], wait_seconds=1):
            for verb, args in (('prepare', ()), ('record', (prepared['id'],))):
                self.assertEqual(self.run_children(verb, *args, count=1),
                                 [{'ok': False, 'error': 'lock_busy'}])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(path.parent.glob('*prepared.json'))), 1)
            self.assertEqual(list(path.parent.glob('*completed.json')), [])
        self.assertEqual(self.prepare()['id'], prepared['id'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.complete(prepared)['id'], prepared['id'])

    def test_source_change_immediately_before_publish_prevents_receipt(self):
        real = runs.state._check_lease
        def change_source(policy, lease):
            real(policy, lease)
            self.write('notes/prompt.md', 'Concurrent policy change\n')
        with mock.patch.object(runs.state, '_check_lease', side_effect=change_source):
            self.error(self.prepare)
        self.assertEqual(len(list((self.vault / runs.RUNTIME_DIR).glob('*prepared.json'))), 0)

    def test_cli_prepare_record_inspect_compare_smoke(self):
        def cli(*args, success=True):
            result = subprocess.run([sys.executable, str(SCRIPTS / 'vault_runs.py'),
                '--vault', str(self.vault), *args], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            return json.loads(result.stdout)
        prepared = cli('prepare', '--candidate', 'notes/candidate.md', '--source', 'notes/prompt.md',
                       '--fixture', 'input/fixture.json', '--tools', 'input/tools.json',
                       '--model', 'fixture-model', '--provider', 'fixture-provider', '--token-budget', '2048')
        self.write_json('input/report.json', self.report(prepared))
        cli('record', prepared['id'], '--report', 'input/report.json')
        self.assertEqual(cli('inspect', prepared['id'])['status'], 'complete')
        result = cli('compare', prepared['id'], prepared['id'])
        self.assertTrue(result['eligible'])
        self.assertEqual(result['score_delta'], 0.0)
        invalid = cli('inspect', '../bad', success=False)
        self.assertFalse(invalid['ok'])

    def test_cli_argument_errors_do_not_echo_invalid_numeric_or_unknown_values(self):
        sentinel = 'sk-' + 'SYNTHETIC_TEST_VALUE_' * 3
        common = [sys.executable, '-B', str(SCRIPTS / 'vault_runs.py'),
            '--vault', 'unused-test-vault', 'prepare', '--candidate', 'notes/candidate.md',
            '--source', 'notes/prompt.md', '--fixture', 'input/fixture.json',
            '--tools', 'input/tools.json', '--model', 'fixture-model',
            '--provider', 'fixture-provider', '--token-budget']
        for arguments in ([sentinel], ['2048', '--unexpected', sentinel]):
            with self.subTest(kind='numeric' if len(arguments) == 1 else 'unknown'):
                result = subprocess.run(common + arguments, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(sentinel, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'invalid_cli_arguments'})
                self.assertLess(len(result.stdout), 128)

    def test_cli_top_level_and_subcommand_help_remain_available(self):
        for arguments in (['--help'], ['--vault', str(self.vault), 'prepare', '--help']):
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vault_runs.py'), *arguments],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, '')
            self.assertIn('usage:', result.stdout)
            self.assertIn('--vault' if len(arguments) == 1 else '--token-budget', result.stdout)


if __name__ == '__main__':
    unittest.main()
